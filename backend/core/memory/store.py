import uuid

from backend.core.llm.base import Message
from backend.db.models.chat_message import ChatMessage
from backend.db.models.user_profile import UserProfile
from backend.db.models.uploaded_doc import UploadedDoc
from backend.db.session import AsyncSessionLocal
from backend.infrastructure.vector_store.embeddings import embed_text, embed_batch
from backend.infrastructure.vector_store.qdrant_client import (
    ensure_collection, upsert_docs, search_similar, delete_by_user,
)
from sqlalchemy import select

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
