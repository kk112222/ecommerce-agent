import json
import logging
import uuid
from fastapi import Depends
from backend.db.models.user import User
from backend.api.deps import get_current_user
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from backend.agents.supervisor import build_supervisor
from backend.api.schemas.chat import ChatRequest, ChatResponse
from backend.core.llm.factory import create_llm
from backend.core.tool.registry import ToolRegistry
from backend.tools import register_all_tools
import asyncio
from backend.core.memory.store import (
    save_message, get_messages, recall_user_memories, get_uploaded_docs, upsert_session,
    merge_semantic_memories, save_episodic_memories, semantic_snapshot,
)
from backend.core.memory.extractor import ProfileExtractor

# per-user 提炼写锁：同一用户的 /chat 与 /chat/stream 可能并发触发提炼，
# 记忆是"读旧→merge 写"的读改写，不加锁会互相覆盖丢记忆（外部评审 M3 真 bug）
_EXTRACT_LOCKS: dict[int, asyncio.Lock] = {}
_EXTRACT_GUARD = asyncio.Lock()


async def _user_extract_lock(user_id: int) -> asyncio.Lock:
    async with _EXTRACT_GUARD:
        lock = _EXTRACT_LOCKS.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            _EXTRACT_LOCKS[user_id] = lock
        return lock
router = APIRouter()

logger = logging.getLogger("ecommerce-agent")   # 与中间件同 logger，日志格式统一




@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, current_user: User = Depends(get_current_user)):
    """普通接口：一次性返回完整回复"""
    sid = request.session_id or str(uuid.uuid4())[:8]
    llm = create_llm()
    registry = ToolRegistry()
    register_all_tools(registry, llm)
    history_text = await get_messages(sid, current_user.id)
    await save_message(sid, current_user.id, "user", request.message)
    await upsert_session(sid, current_user.id, request.message)   # 同步会话元信息（多会话列表）
    graph = build_supervisor(llm, registry)
    profile = await recall_user_memories(current_user.id, request.message)
    uploaded_data = await get_uploaded_docs(sid, current_user.id)
    final = await graph.invoke({"goal": request.message, "history": history_text,
                                "user_profile": profile, "uploaded_data": uploaded_data})
    result = final["report"]
    await save_message(sid, current_user.id, "assistant", result)
    asyncio.create_task(_extract_and_save(current_user.id, sid, request.message, result))
    return ChatResponse(reply=result, session_id=sid)


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest, current_user: User = Depends(get_current_user)):
    """流式接口：SSE 推送 计划 → 子任务 → 报告"""
    sid = request.session_id or str(uuid.uuid4())[:8]
    llm = create_llm()
    registry = ToolRegistry()
    register_all_tools(registry, llm)
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
        queue: asyncio.Queue = asyncio.Queue()

        async def run_graph():                 # 后台任务：跑整个多 Agent 图
            try:
                graph = build_supervisor(llm, registry, on_event=lambda evt: queue.put(evt))
                final = await graph.invoke({"goal": goal, "history": history_text,
                                            "user_profile": profile, "uploaded_data": uploaded_data})
                # 图跑完，把完整报告落库（user 消息在请求进来时已存）
                report = final.get("report", "")
                if report:
                    await save_message(sid, current_user.id, "assistant", report)
                asyncio.create_task(_extract_and_save(current_user.id, sid, goal, report))
            except Exception as e:
                # Agent/LLM 链路失败也必须把结束信号送出去（finally），否则 SSE 永远挂起、前端无限转圈
                logger.exception("Agent 链路异常")
                queue.put_nowait({"type": "error", "message": f"分析失败：{e}"})
            finally:
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
    """后台提炼记忆并写入 —— 不阻塞回复，失败只记日志

    改造点（对照外部评审）：
    - 原料是【整轮】用户问 + 助手答（M1）：画像信号藏在"答完用户认不认可/追不追问"里，
      只看孤立问题会把稳定偏好漏掉；
    - 一次 LLM 调用拆 semantic（稳定画像，合并旧全量）+ episodic（情景记忆，append），
      分别走增量写路径（M4/M5），不再删光重建；
    - per-user 锁串行化"读-改-写"（M3 并发丢记忆真 bug）。
    """
    goal_s = (goal or "").strip()
    if len(goal_s) < 4 or goal_s in _TRIVIAL_GOALS:
        return
    try:
        llm = create_llm()
        async with _user_extract_lock(user_id):
            extractor = ProfileExtractor(llm)
            old_profile = await semantic_snapshot(user_id)          # ① 语义画像合并底子
            # ② 整轮对话当原料（报告可能很长，截前 1600 字省 token）
            conversation = f"用户：{goal_s}\n助手：{(reply or '')[:1600]}"
            out = await extractor.extract(conversation, old_profile=old_profile)
            # ③ 写路径：语义增量合并 + 情景 append（两类各自幂等，可安全串行执行）
            if out.get("semantic"):
                await merge_semantic_memories(user_id, out["semantic"], source_session=sid)
            if out.get("episodic"):
                await save_episodic_memories(user_id, out["episodic"], source_session=sid)
    except Exception:
        logger.exception("记忆提炼失败")        # 记忆失败不影响主流程

