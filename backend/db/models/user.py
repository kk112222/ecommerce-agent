from datetime import datetime
from sqlalchemy import String, DateTime, func
from sqlalchemy.orm import Mapped,mapped_column
from backend.db.models.base import Base


class User(Base):
    __tablename__ = 'users'
    id:Mapped[int] = mapped_column(primary_key=True)
    username:Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name:Mapped[str] = mapped_column(String(100))
    password_hash:Mapped[str] = mapped_column(String(255))
    level:Mapped[str] = mapped_column(String(20),default="normal")
    created_at:Mapped[datetime] = mapped_column(DateTime,server_default=func.now())