import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from backend.core.config import settings
from backend.core.memory.store import reconcile_memory

from backend.api.routes.chat import router as chat_router, retry_pending_extracts
from backend.api.routes.dashboard import router as dashboard_router
from backend.api.routes.auth import router as auth_router
from backend.api.routes.upload import router as upload_router
from backend.api.routes.session import router as session_router
from backend.api.routes.memory import router as memory_router
from backend.api.routes.documents import router as documents_router
from backend.api.middleware import RequestLogMiddleware, ExceptionHandlerMiddleware
from backend.db.models.base import Base
from backend.db.models.insight_cache import InsightCache  # noqa: F401 触发注册（让 metadata 发现新表）
from backend.db.models.user_memory import LongTermMemory  # noqa: F401 触发注册（让 metadata 发现新表）
from backend.db.models.memory_task import MemoryExtractTask  # noqa: F401 触发注册
from backend.db.session import engine

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动：幂等补建缺失的表（新增表不用重跑 init_db，create_all 只建缺的，不清已有数据）

    顺带做一次记忆对账（P1-5）：SQLite 是事实源、qdrant 只是索引，两者没有共享事务，
    进程被杀/写库失败会留下"行没向量"或"孤儿向量"。开机时校准一次，代价小、收益是
    召回不会静默降级。对账失败绝不能让服务起不来（记忆是辅助功能）。
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        report = await reconcile_memory()
        if report["repaired"] or report["orphans"] or report["failed"]:
            logging.getLogger(__name__).warning("启动记忆对账：%s", report)
    except Exception:
        logging.getLogger(__name__).exception("启动记忆对账失败（不影响服务启动）")
    try:
        # 上次进程被杀 / LLM 抽风时没跑完的记忆提炼，这里补跑（P2-16）
        await retry_pending_extracts()
    except Exception:
        logging.getLogger(__name__).exception("重放记忆提炼失败（不影响服务启动）")
    yield


app = FastAPI(title="掌柜 · 电商运营 AI 助手 API", lifespan=lifespan)

# ===== 中间件（注册顺序 = 从内到外的包裹顺序）=====

# ① 最内层 — 跨域处理（OPTIONS 预检直接拦截，不进入业务逻辑）
# 白名单而非 "*"：接口带 JWT，通配符等于把跨域边界完全放开
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ② 中间层 — 请求耗时日志（记录每个接口的响应时间）
app.add_middleware(RequestLogMiddleware)

# ③ 最外层 — 异常兜底（捕获所有未处理异常，返回统一 JSON）
app.add_middleware(ExceptionHandlerMiddleware)

app.include_router(chat_router, prefix="/api")
app.include_router(dashboard_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(upload_router, prefix="/api")
app.include_router(session_router, prefix="/api")
app.include_router(memory_router, prefix="/api")
app.include_router(documents_router, prefix="/api")


# ===== 运维探针（给 docker healthcheck / nginx 上游检查用）=====
# 不查库、不调 LLM：探针要的是"进程还活着吗"，查库会把下游故障误报成"没起来"。
# 放在 /api 之外，免得被 JWT 依赖拦掉（探针不会带 token）。
@app.get("/health", tags=["运维"], summary="存活探针")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)