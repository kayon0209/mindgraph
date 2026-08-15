"""生产级异常处理器：统一错误响应格式 + 结构化日志 + 敏感信息过滤。"""
from __future__ import annotations

import logging
import traceback
from http import HTTPStatus

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from domain.errors import (
    AuthenticationError,
    AuthorizationError,
    ProductError,
    RateLimitError,
)

logger = logging.getLogger("expense_rag.api.errors")

# 生产环境隐藏的错误详情
_PROD_HIDDEN_DETAIL = "An internal error occurred. Please contact support."


def _build_error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    detail: dict | None = None,
) -> JSONResponse:
    """构建统一的错误响应格式。"""
    request_id = getattr(request.state, "request_id", None)
    body: dict = {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
        }
    }
    if detail:
        body["error"]["detail"] = detail
    return JSONResponse(status_code=status_code, content=body)


async def product_error_handler(request: Request, exc: ProductError) -> JSONResponse:
    """处理所有 ProductError 子类异常。"""
    extra: dict = {
        "request_id": getattr(request.state, "request_id", None),
        "error_code": exc.code,
        "error_type": type(exc).__name__,
    }
    if exc.detail:
        extra["error_detail"] = exc.detail

    if exc.status_code >= 500:
        logger.exception("product_error_5xx", extra=extra)
    else:
        logger.warning("product_error_4xx", extra=extra)

    return _build_error_response(
        request, exc.status_code, exc.code, str(exc) or exc.__doc__ or "An error occurred", exc.detail
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """处理 FastAPI 请求参数校验失败（422）。"""
    errors = []
    for error in exc.errors():
        errors.append({
            "field": " -> ".join(str(loc) for loc in error["loc"]),
            "message": error["msg"],
            "type": error.get("type", "unknown"),
        })
    logger.warning(
        "request_validation_failed",
        extra={
            "request_id": getattr(request.state, "request_id", None),
            "validation_errors": errors,
        },
    )
    return _build_error_response(
        request, 422, "validation_error", "Request validation failed", {"errors": errors}
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """处理 Starlette HTTP 异常。"""
    logger.warning(
        "http_exception",
        extra={
            "request_id": getattr(request.state, "request_id", None),
            "status_code": exc.status_code,
            "detail": str(exc.detail),
        },
    )
    return _build_error_response(
        request, exc.status_code, f"http_{exc.status_code}", str(exc.detail)
    )


async def authentication_error_handler(request: Request, exc: AuthenticationError) -> JSONResponse:
    """处理认证失败（401）。"""
    logger.warning(
        "authentication_failed",
        extra={"request_id": getattr(request.state, "request_id", None), "detail": str(exc)},
    )
    response = _build_error_response(request, 401, "authentication_error", str(exc) or "Authentication required")
    response.headers["WWW-Authenticate"] = "Bearer"
    return response


async def authorization_error_handler(request: Request, exc: AuthorizationError) -> JSONResponse:
    """处理鉴权失败（403）。"""
    logger.warning(
        "authorization_failed",
        extra={"request_id": getattr(request.state, "request_id", None)},
    )
    return _build_error_response(request, 403, "authorization_error", str(exc) or "Insufficient permissions")


async def rate_limit_handler(request: Request, exc: RateLimitError) -> JSONResponse:
    """处理速率限制（429）。"""
    logger.warning(
        "rate_limited",
        extra={"request_id": getattr(request.state, "request_id", None)},
    )
    response = _build_error_response(request, 429, "rate_limit_exceeded", str(exc) or "Too many requests")
    response.headers["Retry-After"] = "60"
    return response


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """处理所有未捕获异常（500）。生产环境不暴露内部错误细节。"""
    import os

    is_production = os.getenv("ENVIRONMENT", "development") == "production"
    request_id = getattr(request.state, "request_id", None)

    logger.exception(
        "unhandled_error",
        extra={
            "request_id": request_id,
            "error_type": type(exc).__name__,
            "traceback": traceback.format_exc() if not is_production else "[redacted]",
        },
    )

    message = _PROD_HIDDEN_DETAIL if is_production else f"Internal error: {type(exc).__name__}: {exc}"
    detail = None
    if not is_production:
        detail = {"exception_type": type(exc).__name__, "traceback": traceback.format_exc()}

    return _build_error_response(request, 500, "internal_error", message, detail)


async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """处理 ValueError（422）。"""
    logger.warning(
        "value_error",
        extra={
            "request_id": getattr(request.state, "request_id", None),
            "message": str(exc),
        },
    )
    return _build_error_response(request, 422, "validation_error", str(exc))
