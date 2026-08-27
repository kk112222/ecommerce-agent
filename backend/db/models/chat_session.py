"""会话元信息模型 —— 支持多会话列表 / 重命名 / 软删除

chat_messages 表只管"消息"，这张表管"会话本身"：
每个会话一行（session_id + user_id），记录标题、创建时间、最近活跃时间、软删除标记。
侧边栏的会话列表、排序、删除都从这里读。
"""
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column
from backend.db.models.base import Base
from backend.db.models.user import User  # 注册 users 表，让外键能解析（自包含，同 user_profile.py）


class ChatSession(Base):
    __tablename__ = 'chat_sessions'
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(50), index=True)   # 前端回传的会话 id
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))       # 归属用户（审计隔离）
    title: Mapped[str] = mapped_column(String(100), default="")        # 首条用户消息自动取 / 手动重命名
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)   # 软删除：记录留着，列表不再显示
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
