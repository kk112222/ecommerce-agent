"""
自定义中间件

包含：
- 请求耗时日志
- 统一异常兜底处理
"""

import time
import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("ecommerce-agent")


class RequestLogMiddleware(BaseHTTPMiddleware):
    """记录每个请求的方法、路径、状态码、耗时"""

    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        elapsed_ms = (time.time() - start) * 1000
        logger.info(
            f"[{request.method}] {request.url.path} → {response.status_code} ({elapsed_ms:.2f}ms)"
        )
        # 在响应头也带上耗时，方便前端调试
        response.headers["X-Request-Time-ms"] = f"{elapsed_ms:.2f}"
        return response


class ExceptionHandlerMiddleware(BaseHTTPMiddleware):
    """全局异常兜底：捕获未处理异常，统一返回 JSON"""

    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception:
            logger.exception(f"[{request.method}] {request.url.path} 发生未捕获异常")
            # 生产环境不泄路 traceback，这里用通用提示
            return JSONResponse(
                status_code=500,
                content={
                    "error": "服务器内部错误",
                    "path": str(request.url.path),
                },
            )
