"""M1：MCP 新工具接入与旧工具兼容测试。

覆盖：
- tools/list 在容器装配 registry 后暴露 8 个工具（旧 5 + 新 3 只读治理工具；
  mindgraph_assist 仍按 ASSIST_MCP_ENABLED 门控）；
- 新工具经 handle_jsonrpc 全链路可调用（JSON-RPC envelope → registry → handler
  → 审计），工具级参数校验失败映射 -32602；
- 旧 5 工具的 schema 与响应形状不变（回归护栏）。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from api.dependencies import override_container
from infrastructure.database import ProductDatabase
from mcp_server import _tools, handle_jsonrpc


def _build_registry(tmp_path: Path):
    from application.evidence_tools.handlers import build_default_registry, set_handler_database

    database = ProductDatabase(tmp_path / "mcp-tools.sqlite3")
    database.initialize()
    database.execute(
        """INSERT INTO notes (note_id, vault_path, title, content_hash, document_version, effective_from,
           policy_status, policy_key, owner, acl_public, department, acl_json, chunk_count, index_status,
           created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("n1", "policies/n1.md", "差旅费报销管理办法", "h1", "v1", "2026-01-01",
         "active", "travel.meal", "财务部", 1, "finance", "{}", 1, "active",
         "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
    )
    set_handler_database(database)
    registry = build_default_registry(database)
    return registry, database


def _with_registry(tmp_path: Path):
    registry, database = _build_registry(tmp_path)
    override_container(SimpleNamespace(database=database, evidence_tool_registry=registry))
    return database


def test_tools_list_exposes_eight_tools_by_default(tmp_path: Path):
    database = _with_registry(tmp_path)
    try:
        names = {tool["name"] for tool in _tools()}
        assert names == {
            # 旧 5 工具
            "mindgraph_list_notes",
            "mindgraph_get_note",
            "mindgraph_search",
            "mindgraph_evaluation_overview",
            "mindgraph_list_relations",
            # M1 新增 3 只读治理工具（经共享 registry）
            "mindgraph_get_policy_history",
            "mindgraph_concept_gaps",
            "mindgraph_verify_citations",
        }
        # mindgraph_assist 仍默认隐藏（flag off）
        assert "mindgraph_assist" not in names
    finally:
        override_container(None)


def test_new_tools_callable_via_jsonrpc(tmp_path: Path):
    database = _with_registry(tmp_path)
    try:
        principal = {"authenticated": True, "name": "agent", "roles": ["read"], "departments": ["finance"]}

        # policy history 全链路
        response = handle_jsonrpc(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "mindgraph_get_policy_history", "arguments": {"policy_key": "travel.meal"}}},
            principal=principal,
        )
        assert "error" not in response
        import json

        body = json.loads(response["result"]["content"][0]["text"])
        assert body["policy_key"] == "travel.meal"
        assert [v["version"] for v in body["versions"]] == ["v1"]

        # verify citations 全链路
        response = handle_jsonrpc(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "mindgraph_verify_citations",
                        "arguments": {"answer": "依据 [citation-1]", "citation_ids": ["citation-1"]}}},
            principal=principal,
        )
        body = json.loads(response["result"]["content"][0]["text"])
        assert body["passed"] is True

        # concept gaps 全链路（无概念数据时返回空聚合，不报错）
        response = handle_jsonrpc(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "mindgraph_concept_gaps", "arguments": {"limit": 5}}},
            principal=principal,
        )
        body = json.loads(response["result"]["content"][0]["text"])
        assert body["gaps"] == []

        # registry 审计已落库（区别于 mcp_server 直落审计动作）
        actions = {row["action"] for row in database.fetch_all("SELECT DISTINCT action FROM access_audit")}
        assert {"mcp_get_policy_history", "mcp_verify_citations", "mcp_concept_gaps"} <= actions
    finally:
        override_container(None)


def test_new_tool_invalid_arguments_maps_to_32602(tmp_path: Path):
    _with_registry(tmp_path)
    try:
        response = handle_jsonrpc(
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "mindgraph_get_policy_history", "arguments": {"policy_key": ""}}},
            principal={"authenticated": True, "name": "agent", "roles": ["read"]},
        )
        assert response["error"]["code"] == -32602
    finally:
        override_container(None)


def test_legacy_five_tools_schema_unchanged(tmp_path: Path):
    """旧 5 工具回归护栏：name/inputSchema 关键字段与冻结面一致。"""
    database = _with_registry(tmp_path)
    try:
        tools = {tool["name"]: tool for tool in _tools()}
        # 旧工具仍来自本文件静态定义（不经 registry），schema 形状不变
        search = tools["mindgraph_search"]["inputSchema"]
        assert search["properties"]["top_k"] == {"type": "integer", "default": 5, "minimum": 1, "maximum": 20}
        assert set(search["properties"]["strategy"]["enum"]) == {"dense", "bm25", "hybrid", "hybrid_rerank"}
        assert tools["mindgraph_list_notes"]["inputSchema"]["properties"]["limit"]["maximum"] == 200
        assert "note_id" in tools["mindgraph_get_note"]["inputSchema"]["required"]
        assert tools["mindgraph_evaluation_overview"]["inputSchema"] == {"type": "object", "properties": {}, "additionalProperties": False}
    finally:
        override_container(None)
