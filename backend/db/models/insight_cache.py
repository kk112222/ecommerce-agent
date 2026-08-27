from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.models.base import Base
from backend.db.models.user import User  # 自包含：注册 users 表，让外键能解析


class InsightCache(Base):
    """AI 经营洞察的当天缓存 —— 同一天多次打开看板不重复调 LLM

    数据按天聚合，所以缓存粒度是「用户 + 当天日期」：当天内命中秒开，
    第二天数据变化自然失效（新日期查不到缓存 → 重新生成）。
    """
    __tablename__ = "insight_cache"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    cache_date: Mapped[str] = mapped_column(String(10))   # YYYY-MM-DD
    insight: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, default=None)
    cost_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
