"""End-to-end authentication boundary tests for the FastAPI router graph."""
from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from api.main import app


@pytest.fixture()
def auth_enabled(monkeypatch):
    import api.auth as auth

    monkeypatch.setattr(auth, "AUTH_MODE", "api_key")
    monkeypatch.setattr(auth, "validate_api_key", lambda _key: None)


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/api/v1/mindgraph/notes", None),
        ("post", "/api/v1/evaluations/runs", {}),
        ("post", "/api/v1/governance/datasets", {}),
        ("post", "/api/v1/feedback", {}),
        ("post", "/api/v1/mindgraph/relations/extract", {}),
        ("post", "/api/v1/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
    ],
)
def test_protected_api_rejects_anonymous_requests(auth_enabled, method, path, payload):
    """Removing router-level auth must make at least one mutation fail open."""
    client = TestClient(app, raise_server_exceptions=False)
    response = client.request(method, path, json=payload)
    client.close()

    assert response.status_code == 401, response.text


def test_health_remains_public_when_auth_is_enabled(auth_enabled):
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/v1/health")
    client.close()

    assert response.status_code == 200


def test_directory_sync_rejects_non_admin_api_key(monkeypatch, tmp_path):
    import api.auth as auth

    monkeypatch.setattr(auth, "AUTH_MODE", "api_key")
    monkeypatch.setattr(
        auth,
        "validate_api_key",
        lambda _key: {
            "name": "writer",
            "roles": ["read", "write"],
            "allow": ["workspace:corp"],
            "deny": [],
        },
    )
    source = tmp_path / "source"
    source.mkdir()

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/v1/connectors/directories",
        headers={"X-API-Key": "writer-key"},
        json={"source_path": str(source)},
    )
    client.close()

    assert response.status_code == 403, response.text


def test_auth_mode_resolution_priority(monkeypatch):
    """模块覆盖 > 进程环境变量 > settings(.env/默认)，非法值回退 demo。"""
    from types import SimpleNamespace

    import api.auth as auth

    # 1) 模块级覆盖最高（测试 monkeypatch 路径）
    monkeypatch.setattr(auth, "AUTH_MODE", "api_key")
    monkeypatch.setenv("AUTH_MODE", "off")
    assert auth._auth_mode() == "api_key"

    # 2) 进程环境变量次之（docker/compose env_file 路径）
    monkeypatch.setattr(auth, "AUTH_MODE", None)
    assert auth._auth_mode() == "off"

    # 3) 无环境变量时回退 settings（裸机 uvicorn 读 .env 的路径）
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.setattr(
        "infrastructure.settings.get_settings",
        lambda: SimpleNamespace(AUTH_MODE="off"),
    )
    assert auth._auth_mode() == "off"

    # 4) 非法配置值 fail-closed 回退 demo 并告警
    monkeypatch.setattr(auth, "AUTH_MODE", "bogus")
    assert auth._auth_mode() == "demo"


def test_default_demo_key_has_admin_role(monkeypatch, tmp_path):
    """demo 模式自动生成的默认 key 必须带 admin 角色，否则关系治理端点不可用。"""
    import json as json_module

    import api.auth as auth

    key_file = tmp_path / "api_keys.json"
    monkeypatch.setattr(auth, "API_KEYS_FILE", key_file)
    auth._ensure_api_keys_file()
    data = json_module.loads(key_file.read_text(encoding="utf-8"))
    default_key = next(iter(data["keys"].values()))
    assert "admin" in default_key["roles"]
