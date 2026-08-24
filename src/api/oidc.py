"""SSO / OIDC 令牌校验（Phase 5-4）。

最小可行方案：
- 校验 Bearer JWT（RS256/HS256），从 IdP 的 JWKS 端点取公钥（带缓存）；
- 校验 issuer / audience / exp；
- 把 claims 映射成 MindGraph principal：name/roles/workspaces/departments；
- 与现有 API Key 认证并存：Authorization: Bearer 优先走 OIDC，否则回退 API Key。

设计原则（对齐 Phase 5）：依赖部署环境信息（IdP 配置），置于计划末尾；
本地优先、离线安全——未配置 OIDC 时完全不影响现有认证流程。
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from infrastructure.settings import get_settings

logger = logging.getLogger("mindgraph.auth.oidc")

_JWKS_CACHE: dict[str, Any] = {
    "issuer": None,
    "jwks_uri": None,
    "client": None,
    "fetched_at": 0.0,
}


def _algorithms() -> list[str]:
    return [a.strip() for a in get_settings().OIDC_ALGORITHMS.split(",") if a.strip()]


def _get_jwks_client(force: bool = False) -> Any | None:
    settings = get_settings()
    if not settings.OIDC_ISSUER_URL:
        return None
    issuer = settings.OIDC_ISSUER_URL.rstrip("/")
    ttl = settings.OIDC_JWKS_CACHE_TTL_SECONDS
    now = time.time()
    if (
        not force
        and _JWKS_CACHE["issuer"] == issuer
        and _JWKS_CACHE["client"] is not None
        and (now - _JWKS_CACHE["fetched_at"]) < ttl
    ):
        return _JWKS_CACHE["client"]

    try:
        from jwt import PyJWKClient

        discovery_url = issuer + "/.well-known/openid-configuration"
        with httpx.Client(timeout=10.0) as client:
            discovery = client.get(discovery_url)
            discovery.raise_for_status()
            jwks_uri = discovery.json().get("jwks_uri")
            if not jwks_uri:
                logger.error("oidc_discovery_missing_jwks_uri", extra={"issuer": issuer})
                return None
        jwks_client = PyJWKClient(jwks_uri, cache_keys=True)
        _JWKS_CACHE.update(
            {
                "issuer": issuer,
                "jwks_uri": jwks_uri,
                "client": jwks_client,
                "fetched_at": now,
            }
        )
        return jwks_client
    except Exception:
        logger.exception("oidc_jwks_fetch_failed", extra={"issuer": settings.OIDC_ISSUER_URL})
        if _JWKS_CACHE["issuer"] == issuer:
            return _JWKS_CACHE["client"]
        return None


def validate_id_token(token: str) -> dict[str, Any] | None:
    """校验 OIDC ID/Access Token，返回 claims dict；失败返回 None。

    使用 PyJWT（已随 zhipuai 安装；若不可用则整个 OIDC 功能降级）。
    """
    settings = get_settings()
    if not settings.OIDC_ENABLED or not settings.OIDC_ISSUER_URL:
        return None
    try:
        import jwt as pyjwt  # type: ignore
    except ImportError:
        logger.warning("oidc_disabled_pyjwt_missing")
        return None

    algorithms = _algorithms()
    audience = settings.OIDC_AUDIENCE or settings.OIDC_CLIENT_ID
    issuer = settings.OIDC_ISSUER_URL

    try:
        jwks_client = _get_jwks_client()
        if jwks_client is None:
            return None
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        claims = pyjwt.decode(
            token,
            signing_key.key,
            algorithms=algorithms,
            audience=audience,
            issuer=issuer,
            options={"require": ["exp", "iat", "iss", "sub"]},
        )
        return claims
    except Exception:
        logger.exception("oidc_token_validation_failed")
        return None


def claims_to_principal(claims: dict[str, Any]) -> dict[str, Any]:
    """把 OIDC claims 映射成 MindGraph principal（带 ACL scopes）。"""
    settings = get_settings()
    name = (
        claims.get(settings.OIDC_USERNAME_CLAIM)
        or claims.get("email")
        or claims.get("sub")
        or "oidc-user"
    )
    roles = claims.get(settings.OIDC_ROLES_CLAIM) or claims.get("roles") or []
    if isinstance(roles, str):
        roles = [r.strip() for r in roles.split(",") if r.strip()]
    workspaces = claims.get(settings.OIDC_WORKSPACES_CLAIM) or claims.get("workspaces") or []
    departments = claims.get(settings.OIDC_DEPARTMENTS_CLAIM) or claims.get("departments") or []
    if isinstance(workspaces, str):
        workspaces = [w.strip() for w in workspaces.split(",") if w.strip()]
    if isinstance(departments, str):
        departments = [d.strip() for d in departments.split(",") if d.strip()]

    return {
        "authenticated": True,
        "auth_mode": "oidc",
        "name": str(name),
        "sub": claims.get("sub"),
        "roles": list(roles),
        "workspaces": list(workspaces),
        "departments": list(departments),
        "allow": [],
        "deny": [],
    }


def principal_from_bearer(authorization: str | None) -> dict[str, Any] | None:
    """从 Authorization: Bearer <token> 解析 OIDC principal；未启用 / 非法返回 None。"""
    settings = get_settings()
    if not settings.OIDC_ENABLED or not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    claims = validate_id_token(parts[1])
    if not claims:
        return None
    return claims_to_principal(claims)
