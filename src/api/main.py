"""
FastAPI 应用入口 — 生产级配置。
- 全局异常处理
- 安全中间件（CORS / 安全 Headers / 速率限制）
- 请求追踪 ID
- 结构化日志
- 健康检查
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import logging

from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.auth import require_authenticated
from api.dependencies import get_container
from api.exception_handlers import (
    authentication_error_handler,
    authorization_error_handler,
    http_exception_handler,
    product_error_handler,
    rate_limit_handler,
    unhandled_error_handler,
    validation_error_handler,
    value_error_handler,
)
from api.middleware import (
    LoggingMiddleware,
    RateLimitMiddleware,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
    TimingMiddleware,
)
from api.routes import chat, connectors, evaluation, feedback, governance, health, knowledge, mcp, mindgraph_chat, mindgraph_readonly
from domain.errors import (
    AuthenticationError,
    AuthorizationError,
    ProductError,
    RateLimitError,
)
from infrastructure.logging_config import configure_logging
from infrastructure.settings import get_settings

# ── 日志配置（使用 logging_config 中的结构化日志） ──
_settings = get_settings()
configure_logging(
    level=_settings.LOG_LEVEL,
    log_format="json" if _settings.is_production else "console",
)

logger = logging.getLogger("mindgraph.api")

# ── 应用生命周期 ──


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化 ServiceContainer，关闭时清理资源。"""
    logger.info("application_starting", extra={"environment": _settings.ENVIRONMENT})
    container = get_container()
    logger.info("service_container_initialized")
    yield
    logger.info("application_shutting_down")
    # 清理连接池等资源
    try:
        container.database.close()
    except Exception:
        pass
    logger.info("application_stopped")


# ── FastAPI 应用 ──

app = FastAPI(
    title="MindGraph API",
    description="企业制度与决策依据知识服务 — 基于可溯源 Hybrid RAG 与受控关系扩展",
    version="3.1.0",
    lifespan=lifespan,
    docs_url="/api/docs" if _settings.openapi_enabled else None,
    redoc_url="/api/redoc" if _settings.openapi_enabled else None,
    openapi_url="/api/openapi.json" if _settings.openapi_enabled else None,
)

# ── CORS 中间件 ──

app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origin_list,
    # 通配符 origin 时必须关闭凭据（P2-8）：否则任意网页可携带凭据跨域调用。
    allow_credentials=_settings.cors_allow_credentials,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID", "X-API-Key"],
    expose_headers=["X-Request-ID", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
    max_age=600,
)

# ── 安全中间件（顺序重要：先添加的在内层） ──

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestSizeLimitMiddleware, max_body_bytes=_settings.MAX_UPLOAD_BYTES)
app.add_middleware(TimingMiddleware)
app.add_middleware(LoggingMiddleware)  # 必须在 RateLimit 之后添加（内层），以确保 request_id 已设置

# ── 速率限制 ──
# 生产环境强制开启（settings.rate_limit_effective）；非生产默认关闭可显式打开。

if _settings.rate_limit_effective:
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=_settings.RATE_LIMIT_MAX_REQUESTS,
        window_seconds=_settings.RATE_LIMIT_WINDOW_SECONDS,
    )

# ── 异常处理器注册 ──

app.add_exception_handler(ProductError, product_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(AuthenticationError, authentication_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(AuthorizationError, authorization_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(RateLimitError, rate_limit_handler)  # type: ignore[arg-type]
app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(ValueError, value_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
app.add_exception_handler(Exception, unhandled_error_handler)

# ── 路由注册 ──

API_PREFIX = "/api/v1"
app.include_router(health.router, prefix=API_PREFIX)
for route in (chat.router, connectors.router, knowledge.router, evaluation.router, feedback.router, governance.router, mindgraph_chat.router, mindgraph_readonly.router, mcp.router):
    app.include_router(route, prefix=API_PREFIX, dependencies=[Depends(require_authenticated)])


# ── 根路径 ──

@app.get("/", include_in_schema=False)
async def root():
    return {
        "service": "MindGraph",
        "version": "3.1.0",
        "docs": "/api/docs",
        "health": f"{API_PREFIX}/health",
    }
