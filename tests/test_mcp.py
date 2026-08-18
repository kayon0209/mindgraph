"""MCP 只读工具测试（Phase 5-3）。"""
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
        chunk_id="finance-note::0", text="报销应在 30 日内提交。", document_id="finance-note",
        chunk_index=0, section_path="费用制度",
        metadata={
            "mindgraph_id": "finance-note", "vault_path": "finance.md", "title": "费用制度",
            "doc_name": "finance.md", "workspace": "corp", "department": "finance",
            "acl_json": "{}", "acl_public": False, "document_status": "active",
        },
    )
    pipeline = SimpleNamespace(
        retrieve=lambda query, strategy, categories=None, include_historical=False, access_scope=None: RetrievalTrace(
            query=query, requested_strategy=strategy, actual_strategy=strategy,
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


def _finance_principal():
    return {
        "authenticated": True,
        "name": "finance_mcp",
        "roles": ["read"],
        "departments": ["finance"],
    }


def test_mcp_tools_list_exposes_readonly_tools(tmp_path: Path):
    _database, _vault = _bootstrap(tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        resp = client.get("/api/v1/mcp/tools")
        assert resp.status_code == 200
        tool_names = {tool["name"] for tool in resp.json()["tools"]}
        assert {"mindgraph_list_notes", "mindgraph_get_note", "mindgraph_search"}.issubset(tool_names)
    finally:
        client.close()
        override_container(None)


def test_mcp_list_notes_applies_acl_and_audits(tmp_path: Path):
    database, _vault = _bootstrap(tmp_path)
    original = auth.get_optional_principal
    auth.get_optional_principal = lambda _request: _finance_principal()
    client = TestClient(app, raise_server_exceptions=False)
    try:
        payload = {
            "jsonrpc": "2.0", "id": "1", "method": "tools/call",
            "params": {"name": "mindgraph_list_notes", "arguments": {"limit": 10}},
        }
        resp = client.post("/api/v1/mcp", json=payload)
        assert resp.status_code == 200
        result = json.loads(resp.json()["result"]["content"][0]["text"])
        assert any(item["id"] == "finance-note" for item in result["items"])
        assert all(item["id"] != "hr-note" for item in result["items"])
        audit = database.fetch_all("SELECT action, resource, decision FROM access_audit WHERE action='mcp_list_notes'")
        assert audit
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


def test_mcp_search_returns_citations(tmp_path: Path):
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
    finally:
        client.close()
        override_container(None)
