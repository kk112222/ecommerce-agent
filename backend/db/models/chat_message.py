"""会话消息模型 —— 把内存字典的会话历史持久化到数据库

之前 sessions 是内存字典（重启就丢），现在换成这张表：
每条 user/assistant 消息占一行，按 session_id + user_id 归属。
未来个性化记忆 / 多轮上下文都从这里读历史。
"""
from datetime import datetime
from sqlalchemy import ForeignKey, String, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from backend.db.models.base import Base
from backend.db.models.user import User  # 注册 users 表，让外键能解析（自包含，同 user_profile.py）


class ChatMessage(Base):
    __tablename__ = 'chat_messages'
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(50), index=True)   # 会话 id（前端回传的 sid）
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))       # 归属哪个用户（审计隔离）
    role: Mapped[str] = mapped_column(String(20))                      # user / assistant
    content: Mapped[str] = mapped_column(Text)                         # 消息文本
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
