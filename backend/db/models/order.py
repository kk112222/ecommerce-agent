from sqlalchemy import ForeignKey
from datetime import datetime  # Python 自带，处理日期时间
from sqlalchemy import String, DateTime, func  # SQLAlchemy 的字段类型和函数
from sqlalchemy.orm import Mapped, mapped_column # 2.0 新风格的"列"定义
from backend.db.models.base import Base
class Order(Base):
    __tablename__ = 'orders'
    id:Mapped[int] = mapped_column(primary_key=True)
    product_id:Mapped[int] = mapped_column(ForeignKey('products.id'))
    quantity:Mapped[int] = mapped_column(default=0) #购买数量
    amount:Mapped[int] = mapped_column(default=0) #订单金额
    order_date:Mapped[datetime] = mapped_column(DateTime,server_default=func.now())
    user_id:Mapped[int] = mapped_column(ForeignKey('users.id'))
    status:Mapped[str] = mapped_column(String(20),default="pending")
