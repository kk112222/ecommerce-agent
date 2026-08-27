import uuid

from backend.core.llm.base import Message
from backend.db.models.chat_message import ChatMessage
from backend.db.models.chat_session import ChatSession
from backend.db.models.user_profile import UserProfile
from backend.db.models.uploaded_doc import UploadedDoc
from backend.db.session import AsyncSessionLocal
from backend.infrastructure.vector_store.embeddings import embed_text, embed_batch
from backend.infrastructure.vector_store.qdrant_client import (
    ensure_collection, upsert_docs, search_similar, delete_by_user,
)
from sqlalchemy import select, func, delete

MEMORY_COLLECTION = "user_memories"  # 用户记忆的 qdrant collection（和知识库 kb_docs 分开）


async def save_message(sid: str, user_id: int, role: str, content: str) -> None:
    """存一条消息到 chat_messages 表（会话历史持久化）"""
    async with AsyncSessionLocal() as db:
        db.add(ChatMessage(session_id=sid, user_id=user_id, role=role, content=content))
        await db.commit()


async def load_messages(sid: str, user_id: int) -> list[Message]:
    """读某个会话的历史消息（按时间升序），供未来多轮上下文/个性化记忆使用"""
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(ChatMessage).where(
                ChatMessage.session_id == sid,
                ChatMessage.user_id == user_id,
            ).order_by(ChatMessage.id)
        )).scalars().all()
        return [Message(role=r.role, content=r.content) for r in rows]
async def get_messages(sid: str, user_id: int,limit:int=10) -> str:
    history = await load_messages(sid, user_id)
    if not history:
        return ""
    recent = history[-limit:]
    lines = []
    for msg in recent:
        who = "用户" if msg.role == "user" else "助手"
        lines.append(f"{who}: {msg.content}")
    return "\n".join(lines)
async def get_user_profile(user_id:int) -> str:
    #获取用户偏好
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(UserProfile).where(
                UserProfile.user_id == user_id,
            )
        )).scalar_one_or_none()
        return row.preferences if row else ""

async def update_user_profile(user_id:int,preferences:str) -> None:
    """更新用户偏好"""
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(UserProfile).where(
                UserProfile.user_id == user_id,
            )
        )).scalar_one_or_none()
        if row:
            row.preferences = preferences
        else:
            db.add(UserProfile(user_id=user_id,preferences=preferences))
        await db.commit()


async def save_user_memories(user_id: int, sentences: list[str]) -> None:
    """把提炼出的记忆句子向量化存进 qdrant（覆盖式：先清该用户旧的再插新的，避免重复积累）"""
    if not sentences:
        return
    ensure_collection(MEMORY_COLLECTION)
    delete_by_user(MEMORY_COLLECTION, user_id)   # 旧记忆作废
    vectors = await embed_batch(sentences)
    points = [
        {"id": str(uuid.uuid4()), "vector": v, "payload": {"text": s, "user_id": user_id}}
        for s, v in zip(sentences, vectors) if v
    ]
    if points:
        upsert_docs(points, collection_name=MEMORY_COLLECTION)


async def recall_user_memories(user_id: int, query: str, top_k: int = 3) -> str:
    """按当前问题语义召回该用户最相关的记忆，拼成文本；召回为空则回退一句话画像表（兜底）"""
    vector = await embed_text(query)
    hits = search_similar(vector, top_k=top_k, collection_name=MEMORY_COLLECTION,
                          user_id=user_id) if vector else []
    if hits:
        return "\n".join(h["text"] for h in hits)
    return await get_user_profile(user_id)   # 兜底：向量库还没有记忆时用一句话画像


async def save_uploaded_doc(session_id: str, user_id: int, filename: str, content: str) -> None:
    """存一条上传文件的解析结果（按会话关联，同一个 session 可传多个文件）"""
    async with AsyncSessionLocal() as db:
        db.add(UploadedDoc(session_id=session_id, user_id=user_id,
                           filename=filename, content=content))
        await db.commit()


async def get_uploaded_docs(session_id: str, user_id: int) -> str:
    """读该会话上传过的文件解析文本，拼成字符串（聊天时注入 Agent，供对比分析）"""
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(UploadedDoc).where(
                UploadedDoc.session_id == session_id,
                UploadedDoc.user_id == user_id,
            ).order_by(UploadedDoc.id)
        )).scalars().all()
    if not rows:
        return ""
    return "\n\n".join(f"【上传文件：{r.filename}】\n{r.content}" for r in rows)


# ============ 会话元信息（多会话支持）============
# 每次对话落库时同步一张 chat_sessions 表：侧边栏列表 / 重命名 / 删除都靠它。
# 和消息表分离的好处：会话列表只查这张小表，不用 DISTINCT 扫消息表；还留了软删除的口子。


async def upsert_session(session_id: str, user_id: int, title_hint: str = "") -> None:
    """每次对话落库时同步会话元信息（chat.py 里存 user 消息后调用）

    - 会话不存在 → 新建，标题取本轮第一条用户消息（截断 30 字）
    - 会话已存在 → 刷新 updated_at（置顶用）；只有还没标题（首轮）才补标题，
      手动重命名过的标题不覆盖
    """
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(ChatSession).where(
                ChatSession.session_id == session_id,
                ChatSession.user_id == user_id,
            )
        )).scalars().first()
        if row:
            row.is_deleted = False          # 软删后同一 sid 复用 → 自动恢复
            if not row.title:
                row.title = (title_hint or "")[:30]
        else:
            db.add(ChatSession(session_id=session_id, user_id=user_id,
                               title=(title_hint or "")[:30]))
        await db.commit()


async def list_sessions(user_id: int) -> list[dict]:
    """当前用户未删除的会话列表，按最近活跃倒序，带消息数（侧边栏用）"""
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(ChatSession, func.count(ChatMessage.id).label("msg_count"))
            .outerjoin(ChatMessage,
                       (ChatMessage.session_id == ChatSession.session_id) &
                       (ChatMessage.user_id == ChatSession.user_id))
            .where(ChatSession.user_id == user_id, ChatSession.is_deleted == False)
            .group_by(ChatSession.id)
            .order_by(ChatSession.updated_at.desc())
        )).all()
    return [{
        "id": s.session_id,
        "title": s.title or "新对话",
        "msg_count": cnt,
        "updated_at": s.updated_at.isoformat() if s.updated_at else "",
    } for s, cnt in rows]


async def get_session_messages(session_id: str, user_id: int) -> list[dict]:
    """某个会话的完整历史消息（切换会话时前端加载，只回 user/assistant 两种角色）"""
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(ChatMessage).where(
                ChatMessage.session_id == session_id,
                ChatMessage.user_id == user_id,
            ).order_by(ChatMessage.id)
        )).scalars().all()
    return [{
        "role": r.role, "content": r.content,
        "created_at": r.created_at.isoformat() if r.created_at else "",
    } for r in rows]


async def rename_session(session_id: str, user_id: int, title: str) -> None:
    """手动重命名会话标题"""
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(ChatSession).where(
                ChatSession.session_id == session_id,
                ChatSession.user_id == user_id,
            )
        )).scalars().first()
        if row:
            row.title = title.strip()[:30]
            await db.commit()


async def delete_session(session_id: str, user_id: int) -> None:
    """彻底删除会话：连同该会话下的消息一起物理删除（不可恢复）"""
    async with AsyncSessionLocal() as db:
        await db.execute(
            delete(ChatMessage).where(
                ChatMessage.session_id == session_id,
                ChatMessage.user_id == user_id,
            )
        )
        await db.execute(
            delete(ChatSession).where(
                ChatSession.session_id == session_id,
                ChatSession.user_id == user_id,
            )
        )
        await db.commit()
