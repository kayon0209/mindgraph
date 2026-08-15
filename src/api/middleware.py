"""生产级中间件：安全 Headers、请求日志、速率限制、请求体大小限制、响应计时。"""
from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


logger = logging.getLogger("expense_rag.api.middleware")


# ── 安全 Headers 中间件 ──

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """添加生产级安全响应头。"""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # 不设 HSTS 在 HTTP 环境，生产应在反向代理层配置
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        # API 不应被嵌入 iframe
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response


# ── 请求日志中间件 ──

class LoggingMiddleware(BaseHTTPMiddleware):
    """记录每个请求的 method、path、status、耗时。"""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
        request.state.request_id = request_id
        started = time.perf_counter()

        logger.info(
            "request_started",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "client": request.client.host if request.client else "unknown",
            },
        )

        response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)

        response.headers["X-Request-ID"] = request_id

        log_level = logging.WARNING if response.status_code >= 500 else logging.INFO
        logger.log(
            log_level,
            "request_completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "latency_ms": elapsed_ms,
            },
        )

        return response


# ── 响应计时中间件 ──

class TimingMiddleware(BaseHTTPMiddleware):
    """在响应头中添加 X-Response-Time。"""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        response.headers["X-Response-Time"] = str(elapsed_ms)
        return response


# ── 请求体大小限制中间件 ──

class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """限制请求体大小，防止大文件攻击。"""

    def __init__(self, app, max_body_bytes: int = 10 * 1024 * 1024) -> None:
        super().__init__(app)
        self.max_body_bytes = max_body_bytes

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                length = int(content_length)
                if length > self.max_body_bytes:
                    logger.warning(
                        "request_too_large",
                        extra={
                            "request_id": getattr(request.state, "request_id", None),
                            "content_length": length,
                            "max_allowed": self.max_body_bytes,
                            "path": request.url.path,
                        },
                    )
                    from fastapi.responses import JSONResponse
                    return JSONResponse(
                        status_code=413,
                        content={
                            "error": {
                                "code": "payload_too_large",
                                "message": f"Request body too large. Maximum allowed: {self.max_body_bytes} bytes",
                                "request_id": getattr(request.state, "request_id", None),
                            }
                        },
                    )
            except ValueError:
                pass
        return await call_next(request)


# ── 速率限制中间件 ──

class RateLimitMiddleware(BaseHTTPMiddleware):
    """基于 IP + 路径的滑动窗口速率限制。"""

    def __init__(self, app, max_requests: int = 60, window_seconds: int = 60) -> None:
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        # 简单内存存储（生产建议换 Redis）
        self._store: dict[str, list[float]] = defaultdict(list)
        self._last_cleanup: float = time.monotonic()

    def _client_key(self, request: Request) -> str:
        """生成客户端标识 key。不信任 X-Forwarded-For（可被客户端伪造）。"""
        client_ip = request.client.host if request.client else "unknown"
        path = request.url.path
        return f"{client_ip}:{path}"

    def _clean_window(self, key: str, now: float) -> None:
        """移除窗口外的旧记录。"""
        window_start = now - self.window_seconds
        self._store[key] = [ts for ts in self._store[key] if ts > window_start]

    def _cleanup_stale_keys(self, now: float) -> None:
        """定期清理无活跃记录的 key（防止内存泄漏）。"""
        # 每隔 window_seconds * 3 清理一次
        if now - self._last_cleanup < self.window_seconds * 3:
            return
        window_start = now - self.window_seconds
        stale = [key for key, timestamps in list(self._store.items())
                 if not timestamps or all(ts <= window_start for ts in timestamps)]
        for key in stale:
            del self._store[key]
        self._last_cleanup = now

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # 跳过健康检查等不需要限制的路由
        if request.url.path.startswith("/api/v1/health"):
            return await call_next(request)

        key = self._client_key(request)
        now = time.monotonic()
        self._clean_window(key, now)
        self._cleanup_stale_keys(now)

        if len(self._store[key]) >= self.max_requests:
            logger.warning(
                "rate_limit_hit",
                extra={
                    "request_id": getattr(request.state, "request_id", None),
                    "client": key,
                    "current_count": len(self._store[key]),
                },
            )
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limit_exceeded",
                        "message": f"Rate limit exceeded. {self.max_requests} requests per {self.window_seconds}s allowed.",
                        "request_id": getattr(request.state, "request_id", None),
                    }
                },
                headers={
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(self.window_seconds - (now - self._store[key][0]))),
                    "Retry-After": str(int(self.window_seconds - (now - self._store[key][0]))),
                },
            )

        self._store[key].append(now)

        response = await call_next(request)
        after = time.monotonic()
        remaining = max(0, self.max_requests - len(self._store[key]))
        first_ts = self._store[key][0] if self._store[key] else after
        reset_seconds = max(0, int(self.window_seconds - (after - first_ts)))
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_seconds)
        return response
