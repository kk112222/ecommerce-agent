"""长期记忆模型 —— 语义画像 + 情景记忆的统一存储（SQLite 是唯一事实源）

改造背景（外部评审）：
- 旧实现"删光重建"：每次提炼都 qdrant delete_by_user 整组清空再插，并发/乱序会丢记忆，
  也没有"逐条删/看/管理"的入口，缺情景记忆层。
- 新结构：每一条长期记忆在 SQLite 落一行元信息（类型/重要性/时间/来源/是否生效），
  qdrant 只当"向量召回"的辅助索引（点 id 记在这行上 → 可逐条删向量）。
  kind 区分两类：
  - semantic  语义画像：跨会话稳定（负责类目/关注 KPI/报告偏好），由 LLM 合并旧画像产出
  - episodic  情景记忆：发生过什么（带时间的事实/数值/新动向/待跟进），append + 去重即可
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.models.base import Base
from backend.db.models.user import User  # 自包含：让 users 表注册，外键能解析


class LongTermMemory(Base):
    __tablename__ = 'long_term_memories'

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    kind: Mapped[str] = mapped_column(String(10))                  # semantic / episodic
    text: Mapped[str] = mapped_column(Text)
    importance: Mapped[int] = mapped_column(default=1)             # 1~10，被召回命中 +1（可用度反馈）
    active: Mapped[bool] = mapped_column(default=True)             # False = 已作废（被新版本替换/不再成立），保留行可审计
    source_session: Mapped[str | None] = mapped_column(String(50), nullable=True)   # 记下这条记忆的会话
    qdrant_point_id: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 对应向量点 id（删向量用）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_access_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)   # 最近一次被召回（热度）
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)       # 过期时间（默认不过期，留口子）
