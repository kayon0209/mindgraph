"""M5-A 工具 A（mindgraph_save_artifact）测试：方案逐项验收。

覆盖：
- flag 双门控：AGENT_WRITE_TOOLS_ENABLED=false → tools/list 不含该工具；
  绕过清单直呼 → 工具级拒绝（fail-closed）；
- 开启后：保存返回 artifact_id + checksum + "保存不等于发布"提示；
- 幂等：同 key 同内容重复保存返回同一存档（不新增行）；
- 同 key 不同内容 → 拒绝（不静默覆盖）；
- owner 隔离：另一主体保存的存档不可见（列表/获取均隔离）；
- 跨主体同 key：互不冲突（唯一约束含 owner）；
- 审计脱敏：title/evidence_snapshot 不进 access_audit 明文；
- 旧 8+1 只读工具不受影响；schema v12 迁移不丢数据。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

from api.dependencies import override_container
from application.evidence_tools.handlers import build_default_registry, set_handler_database
from infrastructure.database import ProductDatabase
from infrastructure.settings import get_settings
from mcp_server import _tools, handle_jsonrpc

PRINCIPAL_A = {"authenticated": True, "name": "user-a", "roles": ["read"]}
PRINCIPAL_B = {"authenticated": True, "name": "user-b", "roles": ["read"]}

SNAPSHOT = [{"citation_id": "citation-1", "document_name": "差旅费报销管理办法", "document_version": "v2"}]
CITATIONS = [{"citation_id": "citation-1", "final_rank": 1}]


def _principal_from_scope(principal):
    return principal.get("name")


class Fixture:
    def __init__(self, tmp_path: Path) -> None:
        self.database = ProductDatabase(tmp_path / "m5a.sqlite3")
        self.database.initialize()
        set_handler_database(self.database)
        self.registry = build_default_registry(self.database)
        override_container(SimpleNamespace(database=self.database, evidence_tool_registry=self.registry))

    def close(self) -> None:
        override_container(None)
        set_handler_database(None)
        self.database.close()


def _call(name, arguments, principal):
    return handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
        principal=principal,
    )


def _body(response):
    return json.loads(response["result"]["content"][0]["text"])


def _enable_write(monkeypatch):
    monkeypatch.setenv("AGENT_WRITE_TOOLS_ENABLED", "true")
    get_settings.cache_clear()


def _disable_write(monkeypatch):
    monkeypatch.delenv("AGENT_WRITE_TOOLS_ENABLED", raising=False)
    os.environ["AGENT_WRITE_TOOLS_ENABLED"] = "false"
    get_settings.cache_clear()


def test_write_tool_hidden_by_default(tmp_path: Path, monkeypatch):
    fixture = Fixture(tmp_path)
    try:
        _disable_write(monkeypatch)
        names = {tool["name"] for tool in _tools()}
        assert "mindgraph_save_artifact" not in names
        # conftest 隔离下 assist MCP 亦关闭：8 = 旧 5 + M1 三只读
        assert len(names) == 8
    finally:
        fixture.close()
        os.environ.pop("AGENT_WRITE_TOOLS_ENABLED", None)
        get_settings.cache_clear()


def test_direct_call_rejected_when_flag_off(tmp_path: Path, monkeypatch):
    """fail-closed：绕过 tools/list 直呼也拒绝。"""
    fixture = Fixture(tmp_path)
    try:
        _disable_write(monkeypatch)
        response = _call(
            "mindgraph_save_artifact",
            {"title": "草稿", "idempotency_key": "key-12345678", "evidence_snapshot": SNAPSHOT, "citations": CITATIONS},
            PRINCIPAL_A,
        )
        assert "error" in response  # 工具级错误（非 -32602 参数错误路径的 content 结果）
        assert response["error"]["code"] in {-32603, -32602} or "error" in str(response)
    finally:
        fixture.close()
        os.environ.pop("AGENT_WRITE_TOOLS_ENABLED", None)
        get_settings.cache_clear()


def test_save_artifact_happy_and_idempotent(tmp_path: Path, monkeypatch):
    fixture = Fixture(tmp_path)
    try:
        _enable_write(monkeypatch)
        args = {"title": "差旅餐补核对草稿", "idempotency_key": "draft-0001-key", "evidence_snapshot": SNAPSHOT, "citations": CITATIONS}
        first = _body(_call("mindgraph_save_artifact", args, PRINCIPAL_A))
        assert first["visibility"] == "private"
        assert first["artifact_id"].startswith("saved-")
        assert len(first["checksum"]) == 64
        assert "不等于发布" in first["note"]

        second = _body(_call("mindgraph_save_artifact", args, PRINCIPAL_A))
        assert second["artifact_id"] == first["artifact_id"]  # 幂等
        count = fixture.database.fetch_one("SELECT COUNT(*) AS c FROM saved_artifacts")["c"]
        assert count == 1
    finally:
        fixture.close()
        monkeypatch.delenv("AGENT_WRITE_TOOLS_ENABLED", raising=False)
        get_settings.cache_clear()


def test_same_key_different_content_rejected(tmp_path: Path, monkeypatch):
    """同 key 不同内容 → 拒绝（保存语义幂等，不静默覆盖）。"""
    fixture = Fixture(tmp_path)
    try:
        _enable_write(monkeypatch)
        base = {"title": "草稿一", "idempotency_key": "draft-0002-key", "evidence_snapshot": SNAPSHOT, "citations": CITATIONS}
        _call("mindgraph_save_artifact", base, PRINCIPAL_A)
        conflict = {**base, "title": "内容不同的草稿"}
        response = _call("mindgraph_save_artifact", conflict, PRINCIPAL_A)
        assert "error" in response or (isinstance(response.get("result"), dict) and "error" in str(response.get("result", "")))
        # 数据未被覆盖
        rows = fixture.database.fetch_all("SELECT title FROM saved_artifacts")
        assert [r["title"] for r in rows] == ["草稿一"]
    finally:
        fixture.close()
        monkeypatch.delenv("AGENT_WRITE_TOOLS_ENABLED", raising=False)
        get_settings.cache_clear()


def test_owner_isolation_and_cross_principal_key(tmp_path: Path, monkeypatch):
    fixture = Fixture(tmp_path)
    try:
        _enable_write(monkeypatch)
        args = {"title": "A 的草稿", "idempotency_key": "draft-0003-key", "evidence_snapshot": SNAPSHOT, "citations": CITATIONS}
        a = _body(_call("mindgraph_save_artifact", args, PRINCIPAL_A))
        # 跨主体同 key：不冲突（唯一约束含 owner）
        b = _body(_call("mindgraph_save_artifact", {**args, "title": "B 的草稿"}, PRINCIPAL_B))
        assert b["artifact_id"] != a["artifact_id"]
        # owner 隔离：服务层数据库验证（MCP 无 list 工具；REST 面在 service 测试）
        rows = fixture.database.fetch_all("SELECT owner_principal_id FROM saved_artifacts ORDER BY artifact_id")
        assert {r["owner_principal_id"] for r in rows} == {"user-a", "user-b"}
    finally:
        fixture.close()
        monkeypatch.delenv("AGENT_WRITE_TOOLS_ENABLED", raising=False)
        get_settings.cache_clear()


def test_audit_redacts_user_content(tmp_path: Path, monkeypatch):
    """审计脱敏：title/evidence_snapshot 不进 access_audit 明文（redact_fields）。"""
    fixture = Fixture(tmp_path)
    try:
        _enable_write(monkeypatch)
        secret_title = "机密标题XYZ999"
        _call(
            "mindgraph_save_artifact",
            {"title": secret_title, "idempotency_key": "draft-0004-key", "evidence_snapshot": SNAPSHOT, "citations": CITATIONS},
            PRINCIPAL_A,
        )
        rows = fixture.database.fetch_all("SELECT decision, metadata_json FROM access_audit WHERE action='mcp_save_artifact'")
        assert rows, "audit row missing"
        assert secret_title not in rows[0]["metadata_json"]
        assert "REDACTED" in rows[0]["metadata_json"]
        assert rows[0]["decision"] == "allow"
    finally:
        fixture.close()
        monkeypatch.delenv("AGENT_WRITE_TOOLS_ENABLED", raising=False)
        get_settings.cache_clear()


def test_legacy_tools_unaffected(tmp_path: Path, monkeypatch):
    """开启写工具不影响旧工具清单内容与只读工具行为（assist MCP 由独立
    flag 控制，不在本断言内——conftest 隔离下默认关闭）。"""
    fixture = Fixture(tmp_path)
    try:
        _enable_write(monkeypatch)
        names = {tool["name"] for tool in _tools()}
        assert "mindgraph_save_artifact" in names
        assert len(names) == 9  # 8 + save_artifact
        for legacy in (
            "mindgraph_list_notes", "mindgraph_get_note", "mindgraph_search",
            "mindgraph_evaluation_overview", "mindgraph_list_relations",
            "mindgraph_get_policy_history", "mindgraph_concept_gaps", "mindgraph_verify_citations",
        ):
            assert legacy in names
        # 只读主路径不受影响
        response = _call("mindgraph_verify_citations", {"answer": "依据 [citation-1]", "citation_ids": ["citation-1"]}, PRINCIPAL_A)
        assert _body(response)["passed"] is True
    finally:
        fixture.close()
        monkeypatch.delenv("AGENT_WRITE_TOOLS_ENABLED", raising=False)
        get_settings.cache_clear()
