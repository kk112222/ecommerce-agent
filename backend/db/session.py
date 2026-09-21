from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.core.config import settings

# 连接串来自配置（.env 的 DATABASE_URL，默认项目根 data_v3.db 的绝对路径）——
# 以前这里硬编码，导致 settings.database_url 是个死字段、切 PostgreSQL 会静默失败
DB_URL = settings.database_url

# 1. 创建异步引擎（echo 跟着 debug 走：默认安静，排查时才开，免得日志刷满并泄业务数据）
engine = create_async_engine(DB_URL, echo=settings.debug)

# 1.5 SQLite 并发写设置（上线必开；本地单人调试根本碰不到，所以容易漏）
# 默认 journal 模式下**写会互相阻塞**，而 SQLite 的 busy_timeout 默认是 0 —— 不等、直接抛
# "database is locked"。连接池是多条连接打同一个文件（不是一条连接排队），所以两个请求
# 同时写就会撞上。本地一个人点不出来，上线两个人同时说话就报 500。
#   WAL         读不阻塞写、写不阻塞读（这是和默认模式最大的区别）
#   busy_timeout 撞锁先等 5 秒再失败，而不是立刻抛
#   synchronous=NORMAL  WAL 下最坏只丢最近一个事务，换来 fsync 次数大幅下降
# 用方言判断守卫：换成 PostgreSQL 时整段自动跳过（PRAGMA 是 SQLite 专有语法）。
# 注意 WAL 会额外生成 data_v3.db-wal / -shm 两个兄弟文件 —— 所以数据卷必须挂**目录**，
# 挂单个 .db 文件会把 -wal 写到容器层，重建即丢。
if engine.dialect.name == "sqlite":

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

# 2. 创建会话工厂
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


# 3. 快捷函数
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
