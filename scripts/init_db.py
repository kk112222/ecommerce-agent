import asyncio
from sqlalchemy import text
# 导入 session.py 里的引擎
from backend.db.session import engine
# 导入 Base 和所有模型（导入后 SQLAlchemy 才能发现它们）
from backend.db.models.base import Base
from backend.db.models.product import Product  # 触发注册
from backend.db.models.order import Order  # 触发注册
from backend.db.models.user import User  # 触发注册
from backend.db.models.chat_message import ChatMessage  # 触发注册（会话历史持久化）
from backend.db.models.chat_session import ChatSession  # 触发注册（会话元信息：多会话列表/重命名/删除）
from backend.db.models.user_profile import UserProfile
async def init_db():
    # 用引擎创建连接
    async with engine.begin() as conn:
        # 删除所有旧表（开发阶段用，上线后改成 migrate）
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        print("[OK] 数据库表创建成功！")
    async with engine.connect() as conn:
        result = await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ))
        tables = [row[0] for row in result]
        print(f"[DB] 当前数据库表: {tables}")
if __name__ == "__main__":
    asyncio.run(init_db())
