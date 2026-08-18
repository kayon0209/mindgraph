"""SSO / OIDC 集成测试（Phase 5-4）。

不依赖真实 IdP：用 HS256 自签 JWT，并 monkeypatch JWKS 取 key，验证：
- claims_to_principal 把 workspaces/departments/roles 映射成 principal；
- principal_from_bearer 在 OIDC_ENABLED=True 时解析 Bearer；
- get_optional_principal 优先 OIDC Bearer，再回退 API Key。
"""
from __future__ import annotations

import time
from pathlib import Path

import jwt as pyjwt
import pytest

from api.oidc import claims_to_principal, principal_from_bearer

SHARED_SECRET = "test-shared-secret"


def _make_token(claims: dict) -> str:
    payload = {
        "iss": "https://idp.example.com",
        "sub": "user-123",
        "aud": "mindgraph",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
        **claims,
    }
    return pyjwt.encode(payload, SHARED_SECRET, algorithm="HS256")


@pytest.fixture()
def oidc_enabled(monkeypatch):
    from infrastructure import settings as settings_mod

    monkeypatch.setattr(settings_mod.get_settings(), "OIDC_ENABLED", True)
    monkeypatch.setattr(settings_mod.get_settings(), "OIDC_ISSUER_URL", "https://idp.example.com")
    monkeypatch.setattr(settings_mod.get_settings(), "OIDC_CLIENT_ID", "mindgraph")
    monkeypatch.setattr(settings_mod.get_settings(), "OIDC_AUDIENCE", "mindgraph")
    monkeypatch.setattr(settings_mod.get_settings(), "OIDC_ALGORITHMS", "HS256")
    # monkeypatch validate_id_token 走 HS256 共享密钥路径
    from api import oidc

    def _fake_validate(token: str):
        try:
            return pyjwt.decode(token, SHARED_SECRET, algorithms=["HS256"], audience="mindgraph", issuer="https://idp.example.com")
        except Exception:
            return None

    monkeypatch.setattr(oidc, "validate_id_token", _fake_validate)
    yield


def test_claims_to_principal_maps_workspaces_and_departments():
    claims = {
        "preferred_username": "alice@corp.com",
        "roles": ["read"],
        "workspaces": ["corp"],
        "departments": ["finance"],
    }
    principal = claims_to_principal(claims)
    assert principal["authenticated"] is True
    assert principal["auth_mode"] == "oidc"
    assert principal["name"] == "alice@corp.com"
    assert principal["workspaces"] == ["corp"]
    assert principal["departments"] == ["finance"]
    assert principal["roles"] == ["read"]


def test_principal_from_bearer_parses_valid_token(oidc_enabled):
    token = _make_token({
        "preferred_username": "bob@corp.com",
        "roles": ["read"],
        "workspaces": ["corp"],
        "departments": ["finance"],
    })
    principal = principal_from_bearer(f"Bearer {token}")
    assert principal is not None
    assert principal["name"] == "bob@corp.com"
    assert principal["departments"] == ["finance"]


def test_principal_from_bearer_returns_none_when_disabled():
    # OIDC 未启用（默认）
    assert principal_from_bearer("Bearer some-token") is None
    assert principal_from_bearer(None) is None


def test_get_optional_principal_prefers_oidc_bearer(oidc_enabled, monkeypatch):
    import api.auth as auth

    monkeypatch.setattr(auth, "AUTH_MODE", "bearer")
    token = _make_token({
        "preferred_username": "carol@corp.com",
        "roles": ["read", "write"],
        "workspaces": ["corp"],
        "departments": ["finance"],
    })

    from fastapi import Request

    request = Request(scope={"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())], "query_string": b""})
    principal = auth.get_optional_principal(request)
    assert principal["authenticated"] is True
    assert principal["auth_mode"] == "oidc"
    assert principal["name"] == "carol@corp.com"


def test_oidc_principal_acl_scope_filters_notes(oidc_enabled, tmp_path: Path, monkeypatch):
    """端到端：OIDC principal 的 ACL scope 在台账列表生效。"""
    from types import SimpleNamespace

    import api.auth as auth
    from application.vault_sync_service import VaultSyncService
    from infrastructure.database import ProductDatabase
    from api.dependencies import override_container
    from api.main import app
    from fastapi.testclient import TestClient

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "finance.md").write_text(
        """---
mindgraph_id: finance-note
owner: 财务部
policy_key: expense.general
version: "1.0"
status: active
effective_from: 2026-01-01
workspace: corp
department: finance
---
# 费用制度
报销应在 30 日内提交。
""",
        encoding="utf-8",
    )
    (vault / "hr.md").write_text(
        """---
mindgraph_id: hr-note
owner: 人力资源部
policy_key: hr.leave
version: "1.0"
status: active
effective_from: 2026-01-01
workspace: corp
department: hr
---
# 请假制度
年假应在年初申报。
""",
        encoding="utf-8",
    )
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    VaultSyncService(database, vault, write_ids=False).scan_vault()
    graph_store = SimpleNamespace(related_note_ids=lambda *_a, **_kw: [], note_titles=lambda _ids: {})
    override_container(SimpleNamespace(database=database, mindgraph_graph_store=graph_store))

    token = _make_token({
        "preferred_username": "dave@corp.com",
        "roles": ["read"],
        "departments": ["finance"],
    })
    monkeypatch.setattr(auth, "AUTH_MODE", "bearer")
    client = TestClient(app, raise_server_exceptions=False)
    try:
        resp = client.get("/api/v1/mindgraph/notes", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        ids = {item["id"] for item in resp.json()["items"]}
        assert "finance-note" in ids
        assert "hr-note" not in ids
    finally:
        client.close()
        override_container(None)
