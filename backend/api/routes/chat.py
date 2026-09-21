import json
import logging
import uuid
import weakref
from contextlib import asynccontextmanager
from fastapi import Depends
from backend.db.models.user import User
from backend.db.models.memory_task import MemoryExtractTask
from backend.api.deps import get_current_user
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from backend.agents.supervisor import build_supervisor
from backend.api.schemas.chat import ChatRequest, ChatResponse
from backend.core.config import settings
from backend.core.llm.budget import BudgetedLLM, BudgetExceeded, UsageBudget
from backend.core.llm.factory import create_llm
from backend.core.tool.registry import ToolRegistry
from backend.tools import register_all_tools
import asyncio
from sqlalchemy import select
from backend.db.session import AsyncSessionLocal
from backend.core.memory.store import (
    save_message, get_messages, recall_user_memories, get_uploaded_docs, upsert_session,
    merge_semantic_memories, save_episodic_memories, semantic_snapshot,
)
from backend.core.memory.extractor import ProfileExtractor

# per-user 提炼写锁：同一用户的 /chat 与 /chat/stream 可能并发触发提炼，
# 记忆是"读旧→merge 写"的读改写，不加锁会互相覆盖丢记忆（外部评审 M3 真 bug）。
# 用 WeakValueDictionary：锁只在"正被等待"期间被强引用，没人用了自动回收
# —— 以前的普通 dict 只增不减，用户越多内存越涨（P2-16）。
_EXTRACT_LOCKS: "weakref.WeakValueDictionary[int, asyncio.Lock]" = weakref.WeakValueDictionary()
_EXTRACT_GUARD = asyncio.Lock()

# 后台任务的强引用集合：asyncio.create_task 的返回值没人接的话，任务可能在跑完前被 GC，
# 表现就是"这轮记忆莫名没提炼"（P2-16）。持有引用 + 完成即摘除。
_BACKGROUND_TASKS: set[asyncio.Task] = set()

# 提炼结果计数（可观测性）：失败不再只躺在日志里，/api/memory 能看到
_EXTRACT_STATS = {"ok": 0, "skipped": 0, "failed": 0, "retried": 0}


@asynccontextmanager
async def _user_extract_lock(user_id: int):
    """用法：`async with _user_extract_lock(user_id): ...`

    是**异步上下文管理器**，不是"返回一把锁的协程"。两者写错一个字的代价很大：
    调用处写 `async with`、这里写 `async def ... return lock`，拿到的是 coroutine，
    直接 TypeError；而提炼跑在后台、异常只进日志，表现出来只是"记忆时有时无"，很难发现。
    """
    async with _EXTRACT_GUARD:
        lock = _EXTRACT_LOCKS.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            _EXTRACT_LOCKS[user_id] = lock
    # 出了 GUARD 本地变量仍强引用着 lock —— 等待期间不会被 WeakValueDictionary 回收
    async with lock:
        yield


def _spawn(coro) -> asyncio.Task:
    """起后台任务并持有强引用（见 _BACKGROUND_TASKS 的说明）"""
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


def _budgeted_llm() -> tuple:
    """本轮对话的 LLM + 预算账本（P2-11）

    在 route 层包这一次，往下（planner/executor/synthesizer/角色 Agent/工具内部拿到的 llm）
    就都是同一个 BudgetedLLM —— 这才叫"全局"预算：只卡 Graph 节点的话，
    ReAct 循环（每个子任务 ≤10 轮）和工具自己发起的调用照样能无限烧。
    """
    budget = UsageBudget(max_tokens=settings.agent_budget_tokens,
                         max_seconds=settings.agent_budget_seconds)
    return BudgetedLLM(create_llm(), budget), budget


router = APIRouter()

logger = logging.getLogger("ecommerce-agent")   # 与中间件同 logger，日志格式统一




@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, current_user: User = Depends(get_current_user)):
    """普通接口：一次性返回完整回复"""
    sid = request.session_id or str(uuid.uuid4())[:8]
    llm, budget = _budgeted_llm()
    registry = ToolRegistry()
    register_all_tools(registry, llm, context={"user_id": current_user.id, "session_id": sid})
    history_text = await get_messages(sid, current_user.id)
    await save_message(sid, current_user.id, "user", request.message)
    await upsert_session(sid, current_user.id, request.message)   # 同步会话元信息（多会话列表）
    graph = build_supervisor(llm, registry)
    profile = await recall_user_memories(current_user.id, request.message)
    uploaded_data = await get_uploaded_docs(sid, current_user.id)
    try:
        final = await graph.invoke({"goal": request.message, "history": history_text,
                                    "user_profile": profile, "uploaded_data": uploaded_data})
        result = final["report"]
    except BudgetExceeded as e:
        # 各节点内部已经降级过，正常到不了这里；留一层兜底，别把 500 抛给前端
        result = f"⚠️ 本轮预算用尽（{e.detail}），未能生成完整报告，请缩小问题范围后重试。"
    await save_message(sid, current_user.id, "assistant", result)
    _spawn(_extract_in_background(current_user.id, sid, request.message, result))
    return ChatResponse(reply=result, session_id=sid, usage=budget.snapshot())


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest, current_user: User = Depends(get_current_user)):
    """流式接口：SSE 推送 计划 → 子任务 → 报告"""
    sid = request.session_id or str(uuid.uuid4())[:8]
    llm, budget = _budgeted_llm()
    # 事件队列在这里建（而不是在 event_stream 里）：文档工具落盘后要主动往里塞
    # 一条 document 事件，前端才能立刻拿到下载入口 —— 这就是 P0-3 的前端那一半
    queue: asyncio.Queue = asyncio.Queue()
    registry = ToolRegistry()
    register_all_tools(registry, llm, context={
        "user_id": current_user.id, "session_id": sid,
        "on_document": queue.put_nowait,
    })
    # ① 读历史（在存当前消息之前）
    history_text = await get_messages(sid, current_user.id)
    # ② 读用户画像（长期记忆）
    profile = await recall_user_memories(current_user.id, request.message)
    # ③ 读该会话上传的文件数据（对比分析用）
    uploaded_data = await get_uploaded_docs(sid, current_user.id)
    # ④ 存当前问题
    await save_message(sid, current_user.id, "user", request.message)
    await upsert_session(sid, current_user.id, request.message)   # 同步会话元信息（多会话列表）
    goal = request.message

    async def event_stream():
        async def run_graph():                 # 后台任务：跑整个多 Agent 图
            try:
                graph = build_supervisor(llm, registry, on_event=lambda evt: queue.put(evt))
                final = await graph.invoke({"goal": goal, "history": history_text,
                                            "user_profile": profile, "uploaded_data": uploaded_data})
                # 图跑完，把完整报告落库（user 消息在请求进来时已存）
                report = final.get("report", "")
                if report:
                    await save_message(sid, current_user.id, "assistant", report)
                _spawn(_extract_in_background(current_user.id, sid, goal, report))
            except BudgetExceeded as e:
                # 节点内部已经逐级降级过，正常到不了这里；万一到了，也照样给一句人话并落库，
                # 不能出现"用户看到空白、历史里也没有这一轮"的情况
                logger.warning("本轮 LLM 预算用尽：%s", e.detail)
                report = f"⚠️ 本轮预算用尽（{e.detail}），请缩小问题范围后重试。"
                await save_message(sid, current_user.id, "assistant", report)
            except Exception as e:
                # Agent/LLM 链路失败也必须把结束信号送出去（finally），否则 SSE 永远挂起、前端无限转圈
                logger.exception("Agent 链路异常")
                queue.put_nowait({"type": "error", "message": f"分析失败：{e}"})
            finally:
                # 用量汇总必须在结束信号之前推（P2-11）：无论成败都让用户看到这轮花了多少，
                # 也是排查"这轮为什么慢/贵"的第一手数据
                queue.put_nowait({"type": "usage", "usage": budget.snapshot()})
                await queue.put(None)          # 结束信号：无论成败必达

        task = asyncio.create_task(run_graph())

        while True:                            # 边等边推
            evt = await queue.get()
            if evt is None:
                break
            yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        await task
        yield f"data: {json.dumps({'type': 'session', 'session_id': sid}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# 太琐碎的寒暄不触发提炼（省成本：外部评审 M2 的轻量版，完整版是"攒 N 轮再提炼"）
_TRIVIAL_GOALS = {"谢谢", "好的", "嗯", "好", "ok", "OK", "知道了", "好的好的"}


async def _extract_and_save(user_id: int, sid: str, goal: str, reply: str) -> None:
    """提炼记忆并写入 —— 抛异常给调用方（后台包装负责记数/落重试表）

    改造点（对照外部评审）：
    - 原料是【整轮】用户问 + 助手答（M1）：画像信号藏在"答完用户认不认可/追不追问"里，
      只看孤立问题会把稳定偏好漏掉；
    - 一次 LLM 调用拆 semantic（稳定画像，合并旧全量）+ episodic（情景记忆，append），
      分别走增量写路径（M4/M5），不再删光重建；
    - per-user 锁串行化"读-改-写"（M3 并发丢记忆真 bug）。
    """
    goal_s = (goal or "").strip()
    if len(goal_s) < 4 or goal_s in _TRIVIAL_GOALS:
        _EXTRACT_STATS["skipped"] += 1
        return
    llm = create_llm()
    async with _user_extract_lock(user_id):
        extractor = ProfileExtractor(llm)
        old_profile = await semantic_snapshot(user_id)          # ① 语义画像合并底子
        # ② 整轮对话当原料（报告可能很长，截前 1600 字省 token）
        conversation = f"用户：{goal_s}\n助手：{(reply or '')[:1600]}"
        out = await extractor.extract(conversation, old_profile=old_profile)
        # ③ 写路径：语义增量合并 + 情景 append（两类各自幂等，可安全串行执行）
        if out.get("semantic"):
            # 传 llm：歧义档要靠它判"同一偏好的更新 vs 两条并列偏好"（P1-4）
            await merge_semantic_memories(user_id, out["semantic"],
                                          source_session=sid, llm=llm)
        if out.get("episodic"):
            await save_episodic_memories(user_id, out["episodic"], source_session=sid)
    _EXTRACT_STATS["ok"] += 1


async def _extract_in_background(user_id: int, sid: str, goal: str, reply: str,
                                 task_id: int | None = None) -> None:
    """后台提炼的兜底包装（P2-16）

    以前失败只写一行日志，用户感受是"记忆时有时无"，也没法观测。
    现在：失败落 memory_extract_tasks 表（连整轮原料一起），启动时重放；
    计数进 _EXTRACT_STATS。记忆失败仍然不影响主流程。
    """
    try:
        await _extract_and_save(user_id, sid, goal, reply)
        if task_id is not None:
            await _finish_task(task_id, "done")
    except Exception as e:
        _EXTRACT_STATS["failed"] += 1
        logger.exception("记忆提炼失败")
        await _record_failure(user_id, sid, goal, reply, e, task_id)


async def _record_failure(user_id: int, sid: str, goal: str, reply: str,
                          error: Exception, task_id: int | None) -> None:
    """把失败的提炼落表（已有任务就累加次数，不重复插行）"""
    try:
        async with AsyncSessionLocal() as db:
            if task_id is None:
                db.add(MemoryExtractTask(user_id=user_id, session_id=sid, goal=goal,
                                         reply=reply, status="pending", attempts=1,
                                         last_error=f"{type(error).__name__}: {error}"[:500]))
            else:
                row = await db.get(MemoryExtractTask, task_id)
                if row:
                    row.attempts = (row.attempts or 0) + 1
                    row.last_error = f"{type(error).__name__}: {error}"[:500]
                    # 反复失败的（比如 API Key 失效）就别无限重试了
                    row.status = "failed" if row.attempts >= 3 else "pending"
            await db.commit()
    except Exception:
        logger.exception("记录提炼失败任务时又失败了")   # 兜底不能再抛


async def _finish_task(task_id: int, status: str) -> None:
    async with AsyncSessionLocal() as db:
        row = await db.get(MemoryExtractTask, task_id)
        if row:
            row.status = status
            await db.commit()


async def retry_pending_extracts(limit: int = 20) -> dict:
    """重放上次没跑成的提炼（启动时调用）

    重放安全的前提是写路径幂等：语义走增量合并（同义只刷热度）、情景按相似度去重，
    所以重复跑一轮最多是白做一次合并，不会把记忆堆重复。
    """
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(MemoryExtractTask)
            .where(MemoryExtractTask.status == "pending")
            .order_by(MemoryExtractTask.id).limit(limit)
        )).scalars().all()
    for r in rows:
        _EXTRACT_STATS["retried"] += 1
        _spawn(_extract_in_background(r.user_id, r.session_id or "", r.goal, r.reply, r.id))
    if rows:
        logger.info("重放 %d 条未完成的记忆提炼", len(rows))
    return {"retried": len(rows)}

