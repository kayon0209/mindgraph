from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import api.auth as auth
from api.dependencies import override_container
from api.main import app
from application.vault_sync_service import VaultSyncService
from infrastructure.database import ProductDatabase
from mcp_server import handle_jsonrpc
from retrieval.types import Chunk, RetrievalCandidate, RetrievalTrace


class _FakeContainer(SimpleNamespace):
    pass


def _bootstrap(tmp_path: Path):
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

    finance_chunk = Chunk(
        chunk_id="finance-note::0",
        text="报销应在 30 日内提交。",
        document_id="finance-note",
        chunk_index=0,
        section_path="费用制度",
        metadata={
            "mindgraph_id": "finance-note",
            "vault_path": "finance.md",
            "title": "费用制度",
            "doc_name": "finance.md",
            "workspace": "corp",
            "department": "finance",
            "acl_json": "{}",
            "acl_public": False,
            "document_status": "active",
        },
    )
    pipeline = SimpleNamespace(
        retrieve=lambda query, strategy, categories=None, include_historical=False, access_scope=None: RetrievalTrace(
            query=query,
            requested_strategy=strategy,
            actual_strategy=strategy,
            final_selected_chunks=[RetrievalCandidate(chunk=finance_chunk, final_rank=1, dense_score=0.9)],
            candidate_counts={"dense": 1, "final": 1},
        )
    )
    graph_store = SimpleNamespace(related_note_ids=lambda *_a, **_kw: [], note_titles=lambda _ids: {})
    override_container(SimpleNamespace(
        database=database,
        mindgraph_graph_store=graph_store,
        mindgraph_pipeline=lambda top_k, graph_enabled=True: pipeline,
    ))
    return database, vault


def _finance_principal() -> dict[str, object]:
    return {
        "authenticated": True,
        "name": "finance_mcp",
        "roles": ["read"],
        "departments": ["finance"],
    }


def _admin_principal() -> dict[str, object]:
    return {
        "authenticated": True,
        "name": "relation_reviewer",
        "roles": ["read", "admin"],
        "departments": ["finance"],
    }


def _insert_proposed_relation(database: ProductDatabase, relation_id: str = "rel-in-scope") -> None:
    database.execute(
        "INSERT INTO note_relations "
        "(relation_id, source_note_id, target_note_id, relation_type, direction, status, evidence_chunk_id, confidence, proposed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (relation_id, "finance-note", "finance-note", "related_to", "outgoing", "proposed", "finance-note::0", 0.9, "2026-08-26T00:00:00Z"),
    )


def test_mcp_tools_list_exposes_readonly_tools(tmp_path: Path):
    _database, _vault = _bootstrap(tmp_path)
    original = auth.get_optional_principal
    auth.get_optional_principal = lambda _request: _finance_principal()
    client = TestClient(app, raise_server_exceptions=False)
    try:
        resp = client.get("/api/v1/mcp/tools")
        assert resp.status_code == 200
        tool_names = {tool["name"] for tool in resp.json()["tools"]}
        assert {"mindgraph_list_notes", "mindgraph_get_note", "mindgraph_search", "mindgraph_list_relations", "mindgraph_evaluation_overview"}.issubset(tool_names)
    finally:
        auth.get_optional_principal = original
        client.close()
        override_container(None)


def test_mcp_get_note_denies_out_of_scope_note(tmp_path: Path):
    database, _vault = _bootstrap(tmp_path)
    original = auth.get_optional_principal
    auth.get_optional_principal = lambda _request: _finance_principal()
    client = TestClient(app, raise_server_exceptions=False)
    try:
        payload = {
            "jsonrpc": "2.0", "id": "2", "method": "tools/call",
            "params": {"name": "mindgraph_get_note", "arguments": {"note_id": "hr-note"}},
        }
        resp = client.post("/api/v1/mcp", json=payload)
        assert resp.status_code == 200
        text = resp.json()["result"]["content"][0]["text"]
        body = json.loads(text)
        assert body["error"] == "note not found"
        audit = database.fetch_all("SELECT decision, reason FROM access_audit WHERE action='mcp_get_note' AND resource='notes/hr-note'")
        assert any(row["decision"] == "deny" for row in audit)
    finally:
        auth.get_optional_principal = original
        client.close()
        override_container(None)


def test_mcp_search_returns_citations_and_list_notes_audits(tmp_path: Path):
    _database, _vault = _bootstrap(tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        payload = {
            "jsonrpc": "2.0", "id": "3", "method": "tools/call",
            "params": {"name": "mindgraph_search", "arguments": {"query": "报销", "top_k": 5, "strategy": "hybrid"}},
        }
        resp = client.post("/api/v1/mcp", json=payload)
        assert resp.status_code == 200
        result = json.loads(resp.json()["result"]["content"][0]["text"])
        assert result["citations"]
        assert result["query"] == "报销"
        audit = _database.fetch_all("SELECT action FROM access_audit WHERE action='mcp_search'")
        assert audit
    finally:
        client.close()
        override_container(None)


def test_mcp_search_audit_never_logs_question_body(tmp_path: Path):
    """隐私保证：搜索审计元数据不得包含问题正文，中间件也不得记录请求体。"""
    database, _vault = _bootstrap(tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        sensitive_query = "报销单据丢失后的机密处理流程"
        payload = {
            "jsonrpc": "2.0", "id": "4", "method": "tools/call",
            "params": {"name": "mindgraph_search", "arguments": {"query": sensitive_query, "top_k": 5, "strategy": "hybrid"}},
        }
        resp = client.post("/api/v1/mcp", json=payload)
        assert resp.status_code == 200

        search_rows = database.fetch_all("SELECT metadata_json FROM access_audit WHERE action='mcp_search'")
        assert search_rows
        for row in search_rows:
            assert sensitive_query not in row["metadata_json"]

        all_audit = database.fetch_all("SELECT metadata_json FROM access_audit WHERE action='mcp_call'")
        assert all_audit
        for row in all_audit:
            assert sensitive_query not in row["metadata_json"]
    finally:
        client.close()
        override_container(None)


def test_batch_confirmation_is_rejected(tmp_path: Path):
    """人工确认必须逐条审阅，批量端点不得接受 confirm。"""
    database, _vault = _bootstrap(tmp_path)
    _insert_proposed_relation(database)
    original = auth.get_optional_principal
    original_auth_mode = auth.AUTH_MODE
    auth.AUTH_MODE = "api_key"
    auth.get_optional_principal = lambda _request: _admin_principal()
    app.dependency_overrides[auth.get_required_principal] = lambda: _admin_principal()
    client = TestClient(app, raise_server_exceptions=False)
    try:
        resp = client.post(
            "/api/v1/mindgraph/relations/resolve-batch",
            json={"ids": ["rel-in-scope"], "decision": "confirm", "reason": "逐条证据已核对"},
        )
        assert resp.status_code == 422
        row = database.fetch_one("SELECT status FROM note_relations WHERE relation_id='rel-in-scope'")
        assert row["status"] == "proposed"
    finally:
        auth.get_optional_principal = original
        auth.AUTH_MODE = original_auth_mode
        app.dependency_overrides.pop(auth.get_required_principal, None)
        client.close()
        override_container(None)


def test_relation_resolution_requires_admin_role(tmp_path: Path):
    database, _vault = _bootstrap(tmp_path)
    _insert_proposed_relation(database)
    original = auth.get_optional_principal
    original_auth_mode = auth.AUTH_MODE
    auth.AUTH_MODE = "api_key"
    auth.get_optional_principal = lambda _request: _finance_principal()
    app.dependency_overrides[auth.get_required_principal] = lambda: _finance_principal()
    client = TestClient(app, raise_server_exceptions=False)
    try:
        resp = client.post(
            "/api/v1/mindgraph/relations/rel-in-scope/resolve",
            json={"decision": "confirm", "reason": "证据与关系类型一致"},
        )
        assert resp.status_code == 403
        row = database.fetch_one("SELECT status FROM note_relations WHERE relation_id='rel-in-scope'")
        assert row["status"] == "proposed"
    finally:
        auth.get_optional_principal = original
        auth.AUTH_MODE = original_auth_mode
        app.dependency_overrides.pop(auth.get_required_principal, None)
        client.close()
        override_container(None)


def test_relation_resolution_uses_authenticated_actor_and_records_reason(tmp_path: Path):
    database, _vault = _bootstrap(tmp_path)
    _insert_proposed_relation(database)
    original = auth.get_optional_principal
    original_auth_mode = auth.AUTH_MODE
    auth.AUTH_MODE = "api_key"
    auth.get_optional_principal = lambda _request: _admin_principal()
    app.dependency_overrides[auth.get_required_principal] = lambda: _admin_principal()
    client = TestClient(app, raise_server_exceptions=False)
    try:
        resp = client.post(
            "/api/v1/mindgraph/relations/rel-in-scope/resolve",
            json={
                "decision": "reject",
                "reason": "证据只说明相似性，不能支持业务关系",
            },
        )
        assert resp.status_code == 200
        row = database.fetch_one("SELECT status, resolved_by FROM note_relations WHERE relation_id='rel-in-scope'")
        assert row == {"status": "rejected", "resolved_by": "relation_reviewer"}
        audits = database.fetch_all(
            "SELECT actor, metadata_json FROM access_audit WHERE action='resolve_relation' AND resource='note_relations/rel-in-scope'"
        )
        assert any(
            audit["actor"] == "relation_reviewer"
            and json.loads(audit["metadata_json"])["reason"] == "证据只说明相似性，不能支持业务关系"
            for audit in audits
        )
    finally:
        auth.get_optional_principal = original
        auth.AUTH_MODE = original_auth_mode
        app.dependency_overrides.pop(auth.get_required_principal, None)
        client.close()
        override_container(None)


def test_relation_resolution_rejects_client_controlled_reviewer_identity(tmp_path: Path):
    database, _vault = _bootstrap(tmp_path)
    _insert_proposed_relation(database)
    original = auth.get_optional_principal
    original_auth_mode = auth.AUTH_MODE
    auth.AUTH_MODE = "api_key"
    auth.get_optional_principal = lambda _request: _admin_principal()
    app.dependency_overrides[auth.get_required_principal] = lambda: _admin_principal()
    client = TestClient(app, raise_server_exceptions=False)
    try:
        resp = client.post(
            "/api/v1/mindgraph/relations/rel-in-scope/resolve",
            json={
                "decision": "confirm",
                "reason": "证据与关系类型一致",
                "resolved_by": "spoofed-client-value",
            },
        )
        assert resp.status_code == 422
        row = database.fetch_one("SELECT status, resolved_by FROM note_relations WHERE relation_id='rel-in-scope'")
        assert row == {"status": "proposed", "resolved_by": None}
    finally:
        auth.get_optional_principal = original
        auth.AUTH_MODE = original_auth_mode
        app.dependency_overrides.pop(auth.get_required_principal, None)
        client.close()
        override_container(None)


def test_relation_extraction_requires_admin_and_is_audited(tmp_path: Path):
    database, _vault = _bootstrap(tmp_path)
    extraction = SimpleNamespace(extract=lambda **_kwargs: {"ok": True, "created": 2, "dry_run": False})
    override_container(SimpleNamespace(
        database=database,
        relation_extraction=extraction,
        mindgraph_graph_store=SimpleNamespace(related_note_ids=lambda *_args, **_kwargs: [], note_titles=lambda _ids: {}),
    ))
    original = auth.get_optional_principal
    original_auth_mode = auth.AUTH_MODE
    auth.AUTH_MODE = "api_key"
    client = TestClient(app, raise_server_exceptions=False)
    try:
        auth.get_optional_principal = lambda _request: _finance_principal()
        app.dependency_overrides[auth.get_required_principal] = lambda: _finance_principal()
        denied = client.post("/api/v1/mindgraph/relations/extract", json={"dry_run": False})
        assert denied.status_code == 403

        auth.get_optional_principal = lambda _request: _admin_principal()
        app.dependency_overrides[auth.get_required_principal] = lambda: _admin_principal()
        allowed = client.post("/api/v1/mindgraph/relations/extract", json={"dry_run": False})
        assert allowed.status_code == 200
        audits = database.fetch_all(
            "SELECT actor, decision, metadata_json FROM access_audit WHERE action='extract_relations'"
        )
        assert any(
            row["actor"] == "relation_reviewer"
            and row["decision"] == "allow"
            and json.loads(row["metadata_json"])["created"] == 2
            for row in audits
        )
    finally:
        auth.get_optional_principal = original
        auth.AUTH_MODE = original_auth_mode
        app.dependency_overrides.pop(auth.get_required_principal, None)
        client.close()
        override_container(None)


def test_mcp_rejects_arguments_that_violate_declared_schema(tmp_path: Path):
    _database, _vault = _bootstrap(tmp_path)
    payloads = [
        {"name": "mindgraph_search", "arguments": {"query": "", "top_k": 5}},
        {"name": "mindgraph_search", "arguments": {"query": "报销", "top_k": 21}},
        {"name": "mindgraph_search", "arguments": {"query": "报销", "top_k": "5"}},
        {"name": "mindgraph_search", "arguments": {"query": "报销", "unexpected": True}},
    ]
    for index, params in enumerate(payloads):
        response = handle_jsonrpc(
            {"jsonrpc": "2.0", "id": index, "method": "tools/call", "params": params},
            principal=_finance_principal(),
        )
        assert response is not None
        assert response["error"] == {"code": -32602, "message": "invalid tool arguments"}
    override_container(None)


def test_mcp_internal_error_does_not_leak_exception_text(tmp_path: Path):
    database, _vault = _bootstrap(tmp_path)
    container = SimpleNamespace(
        database=database,
        mindgraph_pipeline=lambda **_kwargs: SimpleNamespace(
            retrieve=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("private-database-path"))
        ),
    )
    override_container(container)
    try:
        response = handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "safe-error",
                "method": "tools/call",
                "params": {"name": "mindgraph_search", "arguments": {"query": "报销"}},
            },
            principal=_finance_principal(),
        )
        assert response is not None
        assert response["error"] == {"code": -32603, "message": "tool execution failed"}
        assert "private-database-path" not in json.dumps(response, ensure_ascii=False)
    finally:
        override_container(None)


def test_mcp_stdio_style_call_without_principal_fails_closed(tmp_path: Path):
    _database, _vault = _bootstrap(tmp_path)
    try:
        response = handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": "anonymous-stdio",
                "method": "tools/call",
                "params": {"name": "mindgraph_list_notes", "arguments": {}},
            },
            principal=None,
        )
        assert response is not None
        assert response["error"] == {"code": -32001, "message": "authentication required"}
    finally:
        override_container(None)
