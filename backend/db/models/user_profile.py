from datetime import datetime


from sqlalchemy import  DateTime, func, ForeignKey,Text
from sqlalchemy.orm import Mapped, mapped_column
from backend.db.models.base import Base
from backend.db.models.user import User  # 注册 users 表，让外键能解析（自包含）

class UserProfile(Base):
    __tablename__ = 'user_profile'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), unique=True)
    preferences: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
