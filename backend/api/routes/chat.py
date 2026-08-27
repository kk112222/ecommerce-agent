import json
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
from backend.core.memory.store import save_message, get_messages, get_user_profile, update_user_profile, save_user_memories, recall_user_memories, get_uploaded_docs, upsert_session
from backend.core.memory.extractor import ProfileExtractor
router = APIRouter()




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
    asyncio.create_task(_extract_and_save(current_user.id, request.message))
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
            graph = build_supervisor(llm, registry, on_event=lambda evt: queue.put(evt))
            final = await graph.invoke({"goal": goal, "history": history_text,
                                        "user_profile": profile, "uploaded_data": uploaded_data})
            # 图跑完，把完整报告落库（user 消息在请求进来时已存）
            report = final.get("report", "")
            if report:
                await save_message(sid, current_user.id, "assistant", report)
            asyncio.create_task(_extract_and_save(current_user.id, goal))
            await queue.put(None)              # 结束信号

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


async def _extract_and_save(user_id: int, goal: str) -> None:
    """后台提炼记忆并更新 —— 不阻塞回复，失败静默
    读-合并-写：先读旧画像当合并底子，LLM 把「旧画像+本轮对话」合并成全量画像，再覆盖写
    （否则每轮只提炼本轮内容，会把之前的画像冲掉，长期记忆不累积）"""
    try:
        llm = create_llm()
        extractor = ProfileExtractor(llm)
        old_profile = await get_user_profile(user_id)              # ① 读旧画像（合并的底子）
        sentences = await extractor.extract(f"用户：{goal}", old_profile=old_profile)  # ② 合并提炼
        if sentences:                         # 提炼出内容才更新，空列表不覆盖旧记忆
            await save_user_memories(user_id, sentences)                # ③ 覆盖写全量：多条记忆向量化
            await update_user_profile(user_id, "；".join(sentences))    # 兜底：一句话画像表同步
    except Exception:
        pass                                  # 记忆提炼失败不影响主流程

