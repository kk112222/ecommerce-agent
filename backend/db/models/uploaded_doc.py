from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.models.base import Base
from backend.db.models.user import User  # 自包含：注册 users 表，让外键能解析


class UploadedDoc(Base):
    """用户上传的文件解析结果 —— 按会话关联，聊天时注入 Agent 做对比分析

    场景：运营上传"竞品价格.csv"，问"对比我和竞品定价"，
    聊天时后端把这份上传数据取出来，和 user_profile 一样注入 Agent。
    """
    __tablename__ = "uploaded_doc"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(Text)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    filename: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)  # 解析后的纯文本
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
