"""API 鉴权模块：API Key 验证 + Bearer Token + 简单会话管理。"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from functools import lru_cache, wraps
from pathlib import Path
from typing import Callable

from fastapi import Depends, Header, HTTPException, Request

from domain.errors import AuthenticationError, AuthorizationError

logger = logging.getLogger("expense_rag.api.auth")

# 环境变量
AUTH_MODE: str = os.getenv("AUTH_MODE", "demo").lower()
API_KEYS_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "api_keys.json"
API_KEY_HEADER = "X-API-Key"

# 允许的认证模式
VALID_AUTH_MODES = {"off", "api_key", "bearer", "demo"}

# ── API Key 管理 ──


def _ensure_api_keys_file() -> None:
    """确保 API Keys 文件存在并初始化。"""
    API_KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not API_KEYS_FILE.exists():
        import json
        default_key = secrets.token_urlsafe(32)
        API_KEYS_FILE.write_text(
            json.dumps(
                {
                    "keys": {
                        default_key: {
                            "name": "default",
                            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "roles": ["read", "write"],
                            "enabled": True,
                        }
                    }
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        logger.warning("generated_default_api_key", extra={"key_prefix": default_key[:8]})


def load_api_keys() -> dict[str, dict]:
    """加载 API Keys（带缓存，仅在文件变更时重载）。"""
    import json

    _ensure_api_keys_file()
    mtime = API_KEYS_FILE.stat().st_mtime
    cached = getattr(load_api_keys, "_cache", None)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(API_KEYS_FILE.read_text(encoding="utf-8"))
        result = {k: v for k, v in data.get("keys", {}).items() if v.get("enabled", True)}
        load_api_keys._cache = (mtime, result)  # type: ignore[attr-defined]
        return result
    except Exception as exc:
        logger.error("failed_to_load_api_keys", extra={"error": str(exc)})
        return {} if cached is None else cached[1]


def validate_api_key(api_key: str) -> dict | None:
    """验证 API Key 并返回 key 元信息。"""
    keys = load_api_keys()
    # 常量时间比较防止时序攻击
    for stored_key, meta in keys.items():
        if hmac.compare_digest(api_key.encode(), stored_key.encode()):
            return meta
    return None


# ── 鉴权依赖 ──


async def get_api_key(request: Request, x_api_key: str | None = Header(None, alias="X-API-Key")) -> dict:
    """从请求头提取 API Key 并验证。"""
    if AUTH_MODE == "off":
        return {"name": "anonymous", "roles": ["read", "write"]}

    api_key = x_api_key
    if not api_key:
        # 也尝试从 Authorization Bearer 中提取
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            api_key = auth_header[7:]

    if not api_key:
        raise AuthenticationError("API Key required. Provide it via X-API-Key header or Authorization: Bearer <key>.")

    key_info = validate_api_key(api_key)
    if not key_info:
        logger.warning("invalid_api_key", extra={"request_id": getattr(request.state, "request_id", None)})
        raise AuthenticationError("Invalid API Key.")

    return key_info


def require_role(role: str) -> Callable:
    """依赖注入：要求特定角色。"""

    async def role_checker(key_info: dict = Depends(get_api_key)) -> dict:
        if role not in key_info.get("roles", []):
            raise AuthorizationError(f"Missing required role: {role}")
        return key_info

    return role_checker


# ── Demo 模式会话管理 ──


class DemoSessionManager:
    """Demo 模式的简单会话管理（不适用于生产）。"""

    _CLEANUP_INTERVAL = 300  # 每 5 分钟触发一次清理检查

    def __init__(self) -> None:
        self._sessions: dict[str, dict] = {}
        self._session_timeout = int(os.getenv("SESSION_TIMEOUT_SECONDS", "3600"))
        self._last_cleanup: float = 0.0

    def create_session(self, username: str) -> str:
        session_id = secrets.token_urlsafe(32)
        self._sessions[session_id] = {
            "username": username,
            "created_at": time.time(),
            "last_active": time.time(),
        }
        # 触发清理（非阻塞，按 interval 节流）
        self._maybe_cleanup()
        return session_id

    def validate_session(self, session_id: str) -> dict | None:
        session = self._sessions.get(session_id)
        if not session:
            return None
        if time.time() - session["last_active"] > self._session_timeout:
            del self._sessions[session_id]
            return None
        session["last_active"] = time.time()
        return session

    def destroy_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def _maybe_cleanup(self) -> None:
        """按间隔节流地清理过期会话。"""
        now = time.time()
        if now - self._last_cleanup < self._CLEANUP_INTERVAL:
            return
        self._last_cleanup = now
        self.cleanup_expired()

    def cleanup_expired(self) -> int:
        """清理过期会话，返回清理数量。"""
        now = time.time()
        expired = [sid for sid, s in self._sessions.items() if now - s["last_active"] > self._session_timeout]
        for sid in expired:
            del self._sessions[sid]
        if expired:
            logger.info("session_cleanup", extra={"cleaned": len(expired)})
        return len(expired)


@lru_cache(maxsize=1)
def get_session_manager() -> DemoSessionManager:
    return DemoSessionManager()
