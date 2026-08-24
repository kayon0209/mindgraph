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
