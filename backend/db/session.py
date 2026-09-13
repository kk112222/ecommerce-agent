from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.core.config import settings

# 连接串来自配置（.env 的 DATABASE_URL，默认项目根 data_v3.db 的绝对路径）——
# 以前这里硬编码，导致 settings.database_url 是个死字段、切 PostgreSQL 会静默失败
DB_URL = settings.database_url

# 1. 创建异步引擎（echo 跟着 debug 走：默认安静，排查时才开，免得日志刷满并泄业务数据）
engine = create_async_engine(DB_URL, echo=settings.debug)

# 2. 创建会话工厂
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


# 3. 快捷函数
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
