"""FastAPI 端点集成测试。"""
from __future__ import annotations

# 必须在导入 app 前设置环境变量
import os

from fastapi.testclient import TestClient
import pytest

pytestmark = pytest.mark.integration

os.environ["ENVIRONMENT"] = "development"
os.environ["AUTH_MODE"] = "off"
os.environ["CHAT_PROVIDER"] = "deepseek"
os.environ["OPENAI_COMPAT_API_KEY"] = "test-key"
os.environ["OPENAI_COMPAT_MODEL"] = "deepseek-test"
os.environ["OPENAI_COMPAT_BASE_URL"] = "https://test.example.com"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """FastAPI TestClient。"""
    from unittest.mock import patch

    import api.auth as auth
    from infrastructure.database import ProductDatabase

    previous_auth_mode = auth.AUTH_MODE
    auth.AUTH_MODE = "off"
    test_database = ProductDatabase(tmp_path_factory.mktemp("api") / "product.sqlite3")
    with patch("api.dependencies.ProductDatabase", return_value=test_database), \
         patch("api.dependencies.DocumentLifecycleService.import_existing_markdown"), \
         patch("api.dependencies.ServiceContainer._register_builtin_datasets"):
        from api.main import app
        with TestClient(app) as c:
            yield c
    auth.AUTH_MODE = previous_auth_mode


class TestHealthEndpoints:
    def test_health_returns_ok(self, client):
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_root_returns_info(self, client):
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "MindGraph"

    def test_openapi_exposes_mindgraph_product_contract(self, client):
        response = client.get("/api/openapi.json")
        assert response.status_code == 200
        info = response.json()["info"]
        assert info["title"] == "MindGraph API"
        assert "企业制度与决策依据" in info["description"]

    def test_request_id_header(self, client):
        response = client.get("/api/v1/health")
        assert "X-Request-ID" in response.headers


class TestSecurityHeaders:
    def test_security_headers_present(self, client):
        response = client.get("/api/v1/health")
        headers = response.headers
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert headers.get("X-Frame-Options") == "DENY"
        assert headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"


class TestErrorHandling:
    def test_404_not_found(self, client):
        response = client.get("/api/v1/nonexistent")
        assert response.status_code == 404

    def test_validation_error_on_invalid_chat_request(self, client):
        response = client.post("/api/v1/chat", json={"question": ""})
        assert response.status_code == 422

    def test_error_response_format(self, client):
        response = client.post("/api/v1/chat", json={"question": ""})
        data = response.json()
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]
        assert "request_id" in data["error"]

    def test_value_error_formatted_as_validation_error(self, client):
        """测试 ValueError 被格式化为 422 响应。"""
        # 这是一个间接测试 - 发送明显非法的参数
        response = client.post("/api/v1/chat", json={"question": 12345})
        assert response.status_code == 422


class TestCORS:
    def test_cors_headers(self, client):
        response = client.options(
            "/api/v1/health",
            headers={
                "Origin": "http://localhost:8501",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers
