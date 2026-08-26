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


def test_resolve_relations_batch_enforces_per_relation_acl(tmp_path: Path):
    """批量确认必须逐条 ACL 校验：越权关系被跳过且状态不变。"""
    database, _vault = _bootstrap(tmp_path)
    now = "2026-08-26T00:00:00Z"
    database.execute(
        "INSERT INTO note_relations "
        "(relation_id, source_note_id, target_note_id, relation_type, direction, status, evidence_chunk_id, confidence, proposed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("rel-in-scope", "finance-note", "finance-note", "related_to", "outgoing", "proposed", "finance-note::0", 0.9, now),
    )
    database.execute(
        "INSERT INTO note_relations "
        "(relation_id, source_note_id, target_note_id, relation_type, direction, status, evidence_chunk_id, confidence, proposed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("rel-cross-tenant", "finance-note", "hr-note", "related_to", "outgoing", "proposed", "finance-note::0", 0.9, now),
    )
    original = auth.get_optional_principal
    original_auth_mode = auth.AUTH_MODE
    auth.AUTH_MODE = "api_key"
    auth.get_optional_principal = lambda _request: _finance_principal()
    app.dependency_overrides[auth.get_required_principal] = lambda: _finance_principal()
    client = TestClient(app, raise_server_exceptions=False)
    try:
        resp = client.post(
            "/api/v1/mindgraph/relations/resolve-batch",
            json={"ids": ["rel-in-scope", "rel-cross-tenant"], "decision": "confirm"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["processed"] == 1
        assert body["skipped"] == 1

        in_scope = database.fetch_one("SELECT status FROM note_relations WHERE relation_id='rel-in-scope'")
        assert in_scope["status"] == "confirmed"
        cross_tenant = database.fetch_one("SELECT status FROM note_relations WHERE relation_id='rel-cross-tenant'")
        assert cross_tenant["status"] == "proposed"

        denied_audit = database.fetch_all(
            "SELECT reason FROM access_audit WHERE action='resolve_relation' AND resource='note_relations/rel-cross-tenant'"
        )
        assert any(row["reason"] == "out_of_scope_or_not_proposed" for row in denied_audit)
    finally:
        auth.get_optional_principal = original
        auth.AUTH_MODE = original_auth_mode
        app.dependency_overrides.pop(auth.get_required_principal, None)
        client.close()
        override_container(None)
