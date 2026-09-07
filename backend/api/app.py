import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from backend.api.routes.chat import router as chat_router
from backend.api.routes.dashboard import router as dashboard_router
from backend.api.routes.auth import router as auth_router
from backend.api.routes.upload import router as upload_router
from backend.api.routes.session import router as session_router
from backend.api.routes.memory import router as memory_router
from backend.api.middleware import RequestLogMiddleware, ExceptionHandlerMiddleware
from backend.db.models.base import Base
from backend.db.models.insight_cache import InsightCache  # noqa: F401 触发注册（让 metadata 发现新表）
from backend.db.models.user_memory import LongTermMemory  # noqa: F401 触发注册（让 metadata 发现新表）
from backend.db.session import engine

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

app = FastAPI(title="电商运营 AI Agent")


@app.on_event("startup")
async def _ensure_tables():
    """幂等补建缺失的表（新增表不用重跑 init_db，create_all 只建缺的，不清已有数据）"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# ===== 中间件（注册顺序 = 从内到外的包裹顺序）=====

# ① 最内层 — 跨域处理（OPTIONS 预检直接拦截，不进入业务逻辑）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)