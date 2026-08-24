# 1. 导入 SQLAlchemy 的工具
from datetime import datetime  # Python 自带，处理日期时间
from sqlalchemy import String, DateTime, func  # SQLAlchemy 的字段类型和函数
from sqlalchemy.orm import Mapped, mapped_column  # 2.0 新风格的"列"定义
from backend.db.models.base import Base  #我们刚才定义的基类

class Product(Base):
    #继承Base，SQLAlchemy就知道这是一张表
    __tablename__ = "products"  #数据库里表的名字
    id: Mapped[int] = mapped_column(primary_key=True)  #主键，自动增长
    name: Mapped[str] = mapped_column(String(200))  #商品名，最长200字
    category: Mapped[str] = mapped_column(String(100))  #分类
    price: Mapped[int] = mapped_column(default=0)  #售价（单位：分）
    cost: Mapped[int] = mapped_column(default=0)  #成本（单位：分）
    stock: Mapped[int] = mapped_column(default=0)  #库存数量
    created_at: Mapped[datetime] = mapped_column(DateTime,server_default=func.now())  # 创建时间