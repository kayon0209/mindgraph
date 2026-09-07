"""M5-A 工具 C（mindgraph_propose_relation）测试：高风险逐项验收。

覆盖：
- 独立 flag：默认隐藏 + 直呼拒绝；
- preview 只读：三端标题/类型/影响范围展示，不写 note_relations；
- submit：创建 **proposed**（绝不 confirmed）；方向/状态字段正确；
- 三端 ACL：source 可见 target 不可见 → 统一拒绝（不提示哪端）；
- evidence_note 不可见 → 同样统一拒绝；
- 幂等：正向已存在（任意状态）→ already_exists 不重复写；反向 pair 同样去重；
- relation_type 白名单外拒绝；source==target 拒绝；
- 审计脱敏：evidence 文本不进明文；
- 与 B 工具独立开关互不影响。
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

PRINCIPAL_ADMIN = {"authenticated": True, "name": "admin-user", "roles": ["admin"]}
PRINCIPAL_PUBLIC = {"authenticated": True, "name": "public-user", "roles": ["read"]}


def _seed_notes(db: ProductDatabase) -> None:
    def _note(note_id: str, title: str, acl_public: int, department: str | None = None):
        db.execute(
            "INSERT INTO notes (note_id, vault_path, title, content_hash, document_version, effective_from,"
            " policy_status, policy_key, owner, acl_public, department, acl_json, chunk_count, index_status, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (note_id, f"policies/{note_id}.md", title, f"h-{note_id}", "v1", "2026-01-01",
             "active", "expense.general", "财务部", acl_public, department, "{}", 1, "active",
             "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )

    _note("note-pub-a", "差旅报销管理办法", 1)
    _note("note-pub-b", "差旅餐补标准", 1)
    _note("note-priv-c", "内部审批权限说明", 0, department="finance")  # 仅 finance 可见


class Fixture:
    def __init__(self, tmp_path: Path) -> None:
        self.database = ProductDatabase(tmp_path / "m5c.sqlite3")
        self.database.initialize()
        _seed_notes(self.database)
        set_handler_database(self.database)
        self.registry = build_default_registry(self.database)
        override_container(SimpleNamespace(database=self.database, evidence_tool_registry=self.registry))

    def close(self) -> None:
        override_container(None)
        set_handler_database(None)
        self.database.close()


def _call(arguments, principal=PRINCIPAL_ADMIN):
    return handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "mindgraph_propose_relation", "arguments": arguments}},
        principal=principal,
    )


def _body(response):
    return json.loads(response["result"]["content"][0]["text"])


def _enable(monkeypatch):
    monkeypatch.setenv("AGENT_PROPOSE_RELATION_TOOL_ENABLED", "true")
    get_settings.cache_clear()


def _cleanup(monkeypatch):
    monkeypatch.delenv("AGENT_PROPOSE_RELATION_TOOL_ENABLED", raising=False)
    monkeypatch.delenv("AGENT_FEEDBACK_TOOL_ENABLED", raising=False)
    monkeypatch.delenv("AGENT_WRITE_TOOLS_ENABLED", raising=False)
    get_settings.cache_clear()


BASE_ARGS = {
    "action": "submit",
    "source_note_id": "note-pub-a",
    "target_note_id": "note-pub-b",
    "relation_type": "references",
    "confidence": 0.8,
}


def test_hidden_by_default(tmp_path: Path, monkeypatch):
    fixture = Fixture(tmp_path)
    try:
        os.environ["AGENT_PROPOSE_RELATION_TOOL_ENABLED"] = "false"
        get_settings.cache_clear()
        assert "mindgraph_propose_relation" not in {t["name"] for t in _tools()}
        response = _call({**BASE_ARGS, "action": "preview"})
        assert "error" in response or "rejected" in str(response).lower()
    finally:
        fixture.close()
        os.environ.pop("AGENT_PROPOSE_RELATION_TOOL_ENABLED", None)
        get_settings.cache_clear()


def test_preview_readonly_and_three_endpoints_visible(tmp_path: Path, monkeypatch):
    fixture = Fixture(tmp_path)
    try:
        _enable(monkeypatch)
        body = _body(_call({
            **BASE_ARGS, "action": "preview",
            "evidence_note_id": "note-pub-b", "evidence": "餐补标准引用了报销总办法",
        }))
        assert body["preview"]["source"]["title"] == "差旅报销管理办法"
        assert body["preview"]["target"]["title"] == "差旅餐补标准"
        assert body["preview"]["visibility_check"].startswith("passed")
        assert "人工确认" in body["impact"]
        assert fixture.database.fetch_one("SELECT COUNT(*) AS c FROM note_relations")["c"] == 0
    finally:
        fixture.close()
        _cleanup(monkeypatch)


def test_submit_creates_proposed_only(tmp_path: Path, monkeypatch):
    fixture = Fixture(tmp_path)
    try:
        _enable(monkeypatch)
        result = _body(_call(BASE_ARGS))
        assert result["status"] == "proposed_created"
        row = fixture.database.fetch_one("SELECT status, relation_type, direction FROM note_relations")
        assert row["status"] == "proposed"  # 绝不自动 confirmed
        assert row["relation_type"] == "references"
        assert "绝不自动生效" in result["note"]
    finally:
        fixture.close()
        _cleanup(monkeypatch)


def test_idempotent_both_directions(tmp_path: Path, monkeypatch):
    fixture = Fixture(tmp_path)
    try:
        _enable(monkeypatch)
        _call(BASE_ARGS)
        # 正向重复
        again = _body(_call(BASE_ARGS))
        assert again["status"] == "already_exists"
        # 反向 pair 同样去重（与既有抽取入口同语义）
        reversed_args = {**BASE_ARGS, "source_note_id": "note-pub-b", "target_note_id": "note-pub-a"}
        reversed_result = _body(_call(reversed_args))
        assert reversed_result["status"] == "already_exists"
        assert fixture.database.fetch_one("SELECT COUNT(*) AS c FROM note_relations")["c"] == 1
    finally:
        fixture.close()
        _cleanup(monkeypatch)


def test_acl_three_endpoint_uniform_rejection(tmp_path: Path, monkeypatch):
    """受限主体：source 可见 target 不可见 / evidence 不可见 → 统一拒绝
    （不提示哪端、不暴露存在性）。"""
    fixture = Fixture(tmp_path)
    try:
        _enable(monkeypatch)
        # public-user（无 finance 部门）看不到 note-priv-c
        resp = _call({**BASE_ARGS, "target_note_id": "note-priv-c"}, principal=PRINCIPAL_PUBLIC)
        assert "error" in resp
        resp2 = _call({
            **BASE_ARGS, "action": "preview", "source_note_id": "note-pub-a", "target_note_id": "note-pub-b",
            "evidence_note_id": "note-priv-c",
        }, principal=PRINCIPAL_PUBLIC)
        assert "error" in resp2
        assert fixture.database.fetch_one("SELECT COUNT(*) AS c FROM note_relations")["c"] == 0
    finally:
        fixture.close()
        _cleanup(monkeypatch)


def test_relation_type_whitelist_and_self_loop(tmp_path: Path, monkeypatch):
    fixture = Fixture(tmp_path)
    try:
        _enable(monkeypatch)
        bad_type = _call({**BASE_ARGS, "relation_type": "destroys_policy"})
        assert "error" in bad_type
        self_loop = _call({**BASE_ARGS, "target_note_id": "note-pub-a"})
        assert "error" in self_loop
    finally:
        fixture.close()
        _cleanup(monkeypatch)


def test_audit_redacts_evidence_text(tmp_path: Path, monkeypatch):
    fixture = Fixture(tmp_path)
    try:
        _enable(monkeypatch)
        secret = "机密证据说明XYZ321"
        _call({**BASE_ARGS, "evidence": secret})
        rows = fixture.database.fetch_all(
            "SELECT metadata_json FROM access_audit WHERE action='mcp_propose_relation'"
        )
        assert rows, "audit missing"
        assert secret not in rows[0]["metadata_json"]
        assert "REDACTED" in rows[0]["metadata_json"]
    finally:
        fixture.close()
        _cleanup(monkeypatch)


def test_independent_flags(tmp_path: Path, monkeypatch):
    """三个写工具各自独立开关：只开 C 时 A/B 仍隐藏。"""
    fixture = Fixture(tmp_path)
    try:
        _enable(monkeypatch)  # 只开 propose
        names = {t["name"] for t in _tools()}
        assert "mindgraph_propose_relation" in names
        assert "mindgraph_save_artifact" not in names
        assert "mindgraph_submit_evidence_feedback" not in names
    finally:
        fixture.close()
        _cleanup(monkeypatch)
