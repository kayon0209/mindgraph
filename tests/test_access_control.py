"""ACL 权限过滤回归测试（企业部署最易出错且价值最高的部分）。

覆盖：
- principal -> access_scope 解析（workspaces/departments/acl/roles）
- note_acl_matches：workspace/department 命中、public、deny、越权拒绝
- chunk_acl_matches：与 note 一致
- /mindgraph/notes 列表按主体裁剪
- /mindgraph/notes/{id} 越权返回 404 并写审计
- 检索管线 _filter_by_access 在 access_scope 下裁剪越权 chunk
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from api.dependencies import override_container
from api.main import app
from application.access_control import (
    build_access_scope,
    chunk_acl_matches,
    note_acl_matches,
)
from application.vault_sync_service import VaultSyncService
from domain.errors import AuthenticationError
from infrastructure.database import ProductDatabase
from retrieval.pipeline import RetrievalPipeline
from retrieval.types import Chunk, RetrievalCandidate, RetrievalTrace


def _vault_with_acl(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "finance").mkdir(parents=True)
    (vault / "hr").mkdir(parents=True)
    (vault / "finance" / "expense.md").write_text(
        """---
mindgraph_id: note-finance
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
    (vault / "hr" / "leave.md").write_text(
        """---
mindgraph_id: note-hr
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
    (vault / "public.md").write_text(
        """---
mindgraph_id: note-public
acl_public: true
workspace: corp
department: finance
---
# 公开公告
公司全员适用。
""",
        encoding="utf-8",
    )
    return vault


def _bootstrap_db(tmp_path: Path) -> tuple[ProductDatabase, Path]:
    vault = _vault_with_acl(tmp_path)
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    VaultSyncService(database, vault, write_ids=False).scan_vault()
    return database, vault


def test_build_access_scope_from_workspaces_and_departments():
    principal = {
        "authenticated": True,
        "name": "finance_user",
        "roles": ["read"],
        "workspaces": ["corp"],
        "departments": ["finance"],
    }
    scope = build_access_scope(principal)
    assert scope is not None
    assert "workspace:corp" in scope["allow"]
    assert "department:finance" in scope["allow"]


def test_admin_role_gets_wildcard_access():
    principal = {"authenticated": True, "name": "admin", "roles": ["admin"]}
    scope = build_access_scope(principal)
    assert "*" in scope["allow"]


def test_unauthenticated_principal_returns_none_scope():
    assert build_access_scope({"authenticated": False}) is None
    assert build_access_scope(None) is None


def test_demo_anonymous_scope_is_public_only(monkeypatch):
    """Removing the explicit public scope would expose private notes again."""
    import api.auth as auth

    monkeypatch.setattr(auth, "AUTH_MODE", "demo")
    monkeypatch.setattr(
        auth,
        "get_optional_principal",
        lambda _request: {
            "name": "anonymous",
            "roles": [],
            "authenticated": False,
            "auth_mode": "demo",
            "allow": [],
            "deny": [],
        },
    )

    scope = auth.resolve_access_scope(SimpleNamespace(headers={}))
    private_note = {"workspace": "corp", "department": "finance", "acl_json": "{}", "acl_public": 0}
    public_note = {"workspace": "corp", "department": "finance", "acl_json": "{}", "acl_public": 1}

    assert scope is not None
    assert scope["public_only"] is True
    assert note_acl_matches(private_note, scope) is False
    assert note_acl_matches(public_note, scope) is True


@pytest.mark.parametrize("provided_key", [None, "invalid-key"])
def test_api_key_mode_rejects_missing_or_invalid_credentials(monkeypatch, provided_key):
    """Enterprise auth must not downgrade a bad credential to anonymous."""
    import api.auth as auth

    monkeypatch.setattr(auth, "AUTH_MODE", "api_key")
    monkeypatch.setattr(auth, "validate_api_key", lambda _key: None)
    headers = {} if provided_key is None else {"X-API-Key": provided_key}

    with pytest.raises(AuthenticationError):
        auth.get_required_principal(SimpleNamespace(headers=headers, state=SimpleNamespace()))


def test_note_acl_matches_workspace_and_department():
    # finance scope grants workspace:corp + department:finance
    scope = {"allow": ["workspace:corp", "department:finance"], "deny": []}
    finance_note = {"workspace": "corp", "department": "finance", "acl_json": "{}", "acl_public": 0}
    # HR note shares workspace=corp, so workspace grant applies → visible
    hr_note = {"workspace": "corp", "department": "hr", "acl_json": "{}", "acl_public": 0}
    assert note_acl_matches(finance_note, scope) is True
    assert note_acl_matches(hr_note, scope) is True

    # department-only scope (no workspace grant) → HR note denied
    dept_only_scope = {"allow": ["department:finance"], "deny": []}
    assert note_acl_matches(finance_note, dept_only_scope) is True
    assert note_acl_matches(hr_note, dept_only_scope) is False

    # different workspace entirely → denied
    other_ws_scope = {"allow": ["workspace:other"], "deny": []}
    assert note_acl_matches(finance_note, other_ws_scope) is False


def test_note_acl_public_overrides_scope():
    scope = {"allow": ["department:finance"], "deny": []}
    public_note = {"workspace": "corp", "department": "hr", "acl_json": "{}", "acl_public": 1}
    assert note_acl_matches(public_note, scope) is True


def test_note_acl_denied_tag_blocks_even_if_workspace_matches():
    scope = {"allow": ["workspace:corp"], "deny": ["department:hr"]}
    hr_note = {"workspace": "corp", "department": "hr", "acl_json": "{}", "acl_public": 0}
    assert note_acl_matches(hr_note, scope) is False


def test_chunk_acl_matches_uses_metadata():
    dept_only_scope = {"allow": ["department:finance"], "deny": []}
    finance_chunk = {"workspace": "corp", "department": "finance", "acl_json": "{}", "acl_public": False}
    hr_chunk = {"workspace": "corp", "department": "hr", "acl_json": "{}", "acl_public": False}
    assert chunk_acl_matches(finance_chunk, dept_only_scope) is True
    assert chunk_acl_matches(hr_chunk, dept_only_scope) is False


def test_retrieval_pipeline_filter_by_access_drops_out_of_scope_chunks():
    finance_chunk = Chunk(
        chunk_id="fin::0", text="报销", document_id="fin", chunk_index=0, section_path=None,
        metadata={"workspace": "corp", "department": "finance", "acl_json": "{}", "acl_public": False,
                  "document_status": "active"},
    )
    hr_chunk = Chunk(
        chunk_id="hr::0", text="请假", document_id="hr", chunk_index=0, section_path=None,
        metadata={"workspace": "corp", "department": "hr", "acl_json": "{}", "acl_public": False,
                  "document_status": "active"},
    )
    pipeline = RetrievalPipeline(
        dense=SimpleNamespace(metadata={}),
        sparse=SimpleNamespace(),
        fusion=SimpleNamespace(),
        reranker=None,
    )
    candidates = [
        RetrievalCandidate(chunk=finance_chunk, final_rank=1, original_score=1.0),
        RetrievalCandidate(chunk=hr_chunk, final_rank=2, original_score=0.9),
    ]
    trace = RetrievalTrace(query="q", requested_strategy="hybrid", actual_strategy="hybrid")
    scope = {"allow": ["department:finance"], "deny": []}
    visible = pipeline._filter_by_access(candidates, scope, trace)
    assert [c.chunk.chunk_id for c in visible] == ["fin::0"]
    assert "access_denied_chunks_filtered" in trace.warnings


def test_notes_list_filtered_by_principal_scope(tmp_path: Path):
    database, _vault = _bootstrap_db(tmp_path)
    graph_store = SimpleNamespace(related_note_ids=lambda *_a, **_kw: [], note_titles=lambda _ids: {})
    override_container(SimpleNamespace(database=database, mindgraph_graph_store=graph_store))

    import api.auth as auth

    original_optional = auth.get_optional_principal
    original_auth_mode = auth.AUTH_MODE
    auth.AUTH_MODE = "api_key"
    auth.get_optional_principal = lambda _request: {  # type: ignore[assignment]
        "authenticated": True, "name": "finance_user", "roles": ["read"],
        "departments": ["finance"],
    }
    app.dependency_overrides[auth.get_required_principal] = lambda: {
        "authenticated": True, "name": "finance_user", "roles": ["read"],
        "departments": ["finance"],
    }
    client = TestClient(app, raise_server_exceptions=False)
    try:
        response = client.get("/api/v1/mindgraph/notes")
        assert response.status_code == 200
        payload = response.json()
        ids = {item["id"] for item in payload["items"]}
        assert "note-finance" in ids
        assert "note-public" in ids  # public 对所有主体可见
        assert "note-hr" not in ids
    finally:
        auth.get_optional_principal = original_optional  # type: ignore[assignment]
        auth.AUTH_MODE = original_auth_mode
        app.dependency_overrides.pop(auth.get_required_principal, None)
        client.close()
        override_container(None)


def test_notes_get_note_out_of_scope_returns_404_and_audits(tmp_path: Path):
    database, _vault = _bootstrap_db(tmp_path)
    graph_store = SimpleNamespace(related_note_ids=lambda *_a, **_kw: [], note_titles=lambda _ids: {})
    override_container(SimpleNamespace(database=database, mindgraph_graph_store=graph_store))

    import api.auth as auth

    original_optional = auth.get_optional_principal
    original_auth_mode = auth.AUTH_MODE
    auth.AUTH_MODE = "api_key"
    auth.get_optional_principal = lambda _request: {  # type: ignore[assignment]
        "authenticated": True, "name": "finance_user", "roles": ["read"],
        "departments": ["finance"],
    }
    app.dependency_overrides[auth.get_required_principal] = lambda: {
        "authenticated": True, "name": "finance_user", "roles": ["read"],
        "departments": ["finance"],
    }
    client = TestClient(app, raise_server_exceptions=False)
    try:
        resp = client.get("/api/v1/mindgraph/notes/note-hr")
        assert resp.status_code == 404
        audit = database.fetch_all(
            "SELECT decision, reason FROM access_audit WHERE action='get_note' AND resource='notes/note-hr'"
        )
        assert any(row["decision"] == "deny" and row["reason"] == "out_of_scope" for row in audit)
    finally:
        auth.get_optional_principal = original_optional  # type: ignore[assignment]
        auth.AUTH_MODE = original_auth_mode
        app.dependency_overrides.pop(auth.get_required_principal, None)
        client.close()
        override_container(None)


def test_sync_writes_workspace_department_acl_json(tmp_path: Path):
    database, _vault = _bootstrap_db(tmp_path)
    finance = database.fetch_one("SELECT workspace, department, acl_json, acl_public FROM notes WHERE note_id='note-finance'")
    assert finance["workspace"] == "corp"
    assert finance["department"] == "finance"
    acl = json.loads(finance["acl_json"])
    assert "workspace" in acl and "department" in acl
    public = database.fetch_one("SELECT acl_public FROM notes WHERE note_id='note-public'")
    assert public["acl_public"] == 1
