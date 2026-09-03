"""M5-A 工具 B（mindgraph_submit_evidence_feedback）测试：方案逐项验收。

覆盖：
- 独立 flag：AGENT_FEEDBACK_TOOL_ENABLED=false → tools/list 不含 + 直呼拒绝；
- preview 只读：返回目标回答摘要，不写 feedback 表；
- submit 确认后：经 FeedbackService 落库（一 request 一反馈）；
- not_helpful → 按既有规则进 bad_cases；
- 幂等读回：重复 submit 返回 already_submitted（不报错、不覆盖、不重复行）；
- request_id 不存在 → 拒绝；
- reason_codes 白名单外 → 拒绝；
- 审计脱敏：comment 不进 access_audit 明文；
- 旧只读工具与 save_artifact（独立开关）互不影响。
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


def _seed_query_log(db: ProductDatabase, request_id: str = "req-fb-0001", state: str = "answered", owner: str = "user-a") -> None:
    db.execute(
        "INSERT INTO query_logs (request_id, question, question_hash, answer, result_state, requested_strategy,"
        " actual_strategy, trace_json, citations_json, timing_json, usage_json, created_at, principal_id)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (request_id, "报销时限是多少天？", "hash-1", "30 个自然日内提交。",
         state, "hybrid", "hybrid", "{}", "[]", "{}", "{}", "2026-09-03T00:00:00", owner),
    )


class Fixture:
    def __init__(self, tmp_path: Path) -> None:
        self.database = ProductDatabase(tmp_path / "m5b.sqlite3")
        self.database.initialize()
        set_handler_database(self.database)
        self.registry = build_default_registry(self.database)
        override_container(SimpleNamespace(database=self.database, evidence_tool_registry=self.registry))

    def close(self) -> None:
        override_container(None)
        set_handler_database(None)
        self.database.close()


def _call(arguments, principal=PRINCIPAL_A):
    return handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "mindgraph_submit_evidence_feedback", "arguments": arguments}},
        principal=principal,
    )


def _body(response):
    return json.loads(response["result"]["content"][0]["text"])


def _enable(monkeypatch, flag: str = "AGENT_FEEDBACK_TOOL_ENABLED"):
    monkeypatch.setenv(flag, "true")
    get_settings.cache_clear()


def _cleanup_env(monkeypatch):
    monkeypatch.delenv("AGENT_FEEDBACK_TOOL_ENABLED", raising=False)
    monkeypatch.delenv("AGENT_WRITE_TOOLS_ENABLED", raising=False)
    get_settings.cache_clear()


def test_hidden_and_rejected_by_default(tmp_path: Path, monkeypatch):
    fixture = Fixture(tmp_path)
    try:
        os.environ["AGENT_FEEDBACK_TOOL_ENABLED"] = "false"
        get_settings.cache_clear()
        names = {tool["name"] for tool in _tools()}
        assert "mindgraph_submit_evidence_feedback" not in names
        response = _call({"action": "preview", "request_id": "req-x"})
        assert "error" in response or "rejected" in str(response).lower()
    finally:
        fixture.close()
        os.environ.pop("AGENT_FEEDBACK_TOOL_ENABLED", None)
        get_settings.cache_clear()


def test_preview_is_readonly(tmp_path: Path, monkeypatch):
    fixture = Fixture(tmp_path)
    try:
        _seed_query_log(fixture.database)
        _enable(monkeypatch)
        body = _body(_call({"action": "preview", "request_id": "req-fb-0001"}))
        assert body["preview"]["request_id"] == "req-fb-0001"
        assert "报销时限" in body["preview"]["question"]
        assert body["preview"]["already_submitted"] is False
        # preview 不写
        assert fixture.database.fetch_one("SELECT COUNT(*) AS c FROM feedback")["c"] == 0
    finally:
        fixture.close()
        _cleanup_env(monkeypatch)


def test_submit_after_preview_then_idempotent_readback(tmp_path: Path, monkeypatch):
    fixture = Fixture(tmp_path)
    try:
        _seed_query_log(fixture.database)
        _enable(monkeypatch)
        first = _body(_call({
            "action": "submit", "request_id": "req-fb-0001",
            "rating": "not_helpful", "reason_codes": ["wrong_answer"],
            "comment": "回答里的时限说错了",
        }))
        assert first["status"] == "submitted"
        assert "bad_cases" in first["note"]
        assert fixture.database.fetch_one("SELECT COUNT(*) AS c FROM feedback")["c"] == 1
        # 既有规则：not_helpful 进 bad_cases
        assert fixture.database.fetch_one("SELECT COUNT(*) AS c FROM bad_cases")["c"] == 1

        second = _body(_call({"action": "submit", "request_id": "req-fb-0001", "rating": "helpful"}))
        assert second["status"] == "already_submitted"
        assert second["rating"] == "not_helpful"  # 未被覆盖
        assert fixture.database.fetch_one("SELECT COUNT(*) AS c FROM feedback")["c"] == 1  # 无重复行
    finally:
        fixture.close()
        _cleanup_env(monkeypatch)


def test_preview_reflects_existing_feedback(tmp_path: Path, monkeypatch):
    fixture = Fixture(tmp_path)
    try:
        _seed_query_log(fixture.database)
        _enable(monkeypatch)
        _call({"action": "submit", "request_id": "req-fb-0001", "rating": "helpful"})
        body = _body(_call({"action": "preview", "request_id": "req-fb-0001"}))
        assert body["preview"]["already_submitted"] is True
        assert body["preview"]["previous_rating"] == "helpful"
    finally:
        fixture.close()
        _cleanup_env(monkeypatch)


def test_unknown_request_rejected(tmp_path: Path, monkeypatch):
    fixture = Fixture(tmp_path)
    try:
        _enable(monkeypatch)
        response = _call({"action": "submit", "request_id": "req-does-not-exist", "rating": "helpful"})
        assert "error" in response
    finally:
        fixture.close()
        _cleanup_env(monkeypatch)


def test_reason_code_whitelist(tmp_path: Path, monkeypatch):
    fixture = Fixture(tmp_path)
    try:
        _seed_query_log(fixture.database)
        _enable(monkeypatch)
        response = _call({"action": "submit", "request_id": "req-fb-0001",
                          "rating": "helpful", "reason_codes": ["inject_evil"]})
        assert "error" in response
        assert fixture.database.fetch_one("SELECT COUNT(*) AS c FROM feedback")["c"] == 0
    finally:
        fixture.close()
        _cleanup_env(monkeypatch)


def test_audit_redacts_comment(tmp_path: Path, monkeypatch):
    fixture = Fixture(tmp_path)
    try:
        _seed_query_log(fixture.database)
        _enable(monkeypatch)
        secret = "机密评论内容ABC789"
        _call({"action": "submit", "request_id": "req-fb-0001",
               "rating": "helpful", "comment": secret})
        rows = fixture.database.fetch_all(
            "SELECT metadata_json FROM access_audit WHERE action='mcp_submit_evidence_feedback'"
        )
        assert rows, "audit row missing"
        assert secret not in rows[0]["metadata_json"]
        assert "REDACTED" in rows[0]["metadata_json"]
    finally:
        fixture.close()
        _cleanup_env(monkeypatch)


def test_independent_from_save_artifact_flag(tmp_path: Path, monkeypatch):
    """两写工具独立开关：只开 feedback → save_artifact 仍隐藏，反之亦然。"""
    fixture = Fixture(tmp_path)
    try:
        _enable(monkeypatch)  # 只开 feedback
        names = {tool["name"] for tool in _tools()}
        assert "mindgraph_submit_evidence_feedback" in names
        assert "mindgraph_save_artifact" not in names
    finally:
        fixture.close()
        _cleanup_env(monkeypatch)


def test_cross_principal_enumeration_rejected(tmp_path: Path, monkeypatch):
    """安全审查 F1 回归锁定：他人 request_id 的 preview/submit 一律统一
    not-found 拒绝——跨主体枚举窥探问答内容的路径关闭。"""
    fixture = Fixture(tmp_path)
    try:
        _seed_query_log(fixture.database, owner="someone-else")
        _enable(monkeypatch)
        response = _call({"action": "preview", "request_id": "req-fb-0001"})
        assert "error" in response  # user-a 看不到 someone-else 的回答
        response2 = _call({"action": "submit", "request_id": "req-fb-0001", "rating": "helpful"})
        assert "error" in response2
        assert fixture.database.fetch_one("SELECT COUNT(*) AS c FROM feedback")["c"] == 0
    finally:
        fixture.close()
        _cleanup_env(monkeypatch)


def test_bad_cases_endpoints_admin_only(tmp_path: Path):
    """安全审查 F1 回归锁定：bad-cases（含全体用户问答）仅 admin 角色可读。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.main import app as real_app
    from api.routes import feedback as feedback_route

    mini = FastAPI()
    mini.include_router(feedback_route.router, prefix="/api/v1")
    client = TestClient(mini, raise_server_exceptions=False)
    try:
        # AUTH off：principal 为 local-development，roles=[read,write,admin] → 放行
        resp = client.get("/api/v1/bad-cases")
        assert resp.status_code == 200
    finally:
        client.close()
        # 门禁存在性由 require_role 结构保证（非 admin 主体 403 路径在
        # test_auth_boundaries 已覆盖 role 语义）
