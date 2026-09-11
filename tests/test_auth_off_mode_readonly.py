"""AUTH_MODE=off 的授权边界回归（安全修复 F-OFF-WRITE）。

背景：off 模式原先无条件给匿名主体 read+write+admin，只要 `AUTH_MODE=off`
被遗留在任何对外可达的部署里，任何人都能重建索引、改连接器、导出 bad-case。
修复后 off 模式默认只读；写权限必须由 `AUTH_OFF_ALLOW_WRITES=true` 显式开启。

本文件锁定三件事：
1. off + 未开开关 → 主体只有 read 角色；
2. off + 开关打开 → 恢复 read/write/admin（本地开发路径不被破坏）；
3. 真实 HTTP 面上，off 默认只读时写端点被拒绝（403 而非 200）。
"""
from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from api.main import app


@pytest.fixture()
def off_mode_readonly(monkeypatch):
    """off 模式 + 关闭写开关。"""
    import api.auth as auth

    monkeypatch.setattr(auth, "AUTH_MODE", "off")
    monkeypatch.delenv("AUTH_OFF_ALLOW_WRITES", raising=False)


@pytest.fixture()
def off_mode_with_writes(monkeypatch):
    """off 模式 + 显式打开写开关。"""
    import api.auth as auth

    monkeypatch.setattr(auth, "AUTH_MODE", "off")
    monkeypatch.setenv("AUTH_OFF_ALLOW_WRITES", "true")


def test_off_mode_defaults_to_read_only_roles(off_mode_readonly):
    import api.auth as auth

    assert auth._off_mode_roles() == ["read"]


def test_off_mode_roles_include_write_only_with_explicit_opt_in(off_mode_with_writes):
    import api.auth as auth

    assert auth._off_mode_roles() == ["read", "write", "admin"]


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", "/api/v1/governance/datasets", {}),
        ("post", "/api/v1/mindgraph/relations/extract", {}),
    ],
)
def test_off_mode_readonly_rejects_write_endpoints(off_mode_readonly, method, path, payload):
    """off 默认只读时，写端点必须被拒（不能只看角色列表，要走真实 HTTP 面）。"""
    client = TestClient(app, raise_server_exceptions=False)
    response = client.request(method, path, json=payload)
    client.close()

    assert response.status_code in (401, 403), response.text


def test_off_mode_readonly_keeps_read_endpoints_working(off_mode_readonly):
    """只读收敛不能顺手把读面也关掉：health 必须仍然可用。"""
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/v1/health")
    client.close()

    assert response.status_code == 200, response.text
