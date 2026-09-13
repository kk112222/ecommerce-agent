"""记忆提炼待重试表（P2-16）

提炼是"回复之后"的后台任务：LLM 偶发失败、进程在提炼途中重启，这一轮记忆就静默丢了
—— 用户只会觉得"它有时候记得我、有时候不记得"。
把失败的提炼连同整轮原料落一行，启动时重放即可。

为什么重放是安全的：写路径已经幂等（语义走增量合并、情景按相似度去重），
同一轮重放最多是"合并了一次没变的画像"，不会重复堆记忆。
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.models.base import Base
from backend.db.models.user import User  # 自包含：让 users 表注册，外键能解析


class MemoryExtractTask(Base):
    __tablename__ = 'memory_extract_tasks'

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    session_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    goal: Mapped[str] = mapped_column(Text)          # 整轮原料：用户问
    reply: Mapped[str] = mapped_column(Text)         # 整轮原料：助手答
    status: Mapped[str] = mapped_column(String(10), default="pending")   # pending / done / failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
