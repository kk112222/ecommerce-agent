from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

# 不管从哪个目录启动，都指向项目根目录的 data.db
_db_path = Path(__file__).parent.parent.parent / "data_v3.db"  # backend/db → backend → 项目根
DB_URL = f"sqlite+aiosqlite:///{_db_path.as_posix()}"

# 1. 创建异步引擎
engine = create_async_engine(DB_URL, echo=True)

# 2. 创建会话工厂
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


# 3. 快捷函数
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
