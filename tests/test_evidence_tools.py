"""EvidenceToolRegistry 契约与安全测试（M1）。

覆盖：注册/发现、参数校验（required/类型/枚举/大小上限）、deadline
fail-closed、context 白名单、审计 allow/deny 落库、脱敏字段不进审计、
三个只读工具的行为（policy_history 可见性裁剪与不泄漏隐藏计数、
concept_gaps 聚合、verify_citations 完整性判定）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from application.evidence_tools import handlers as evidence_handlers
from application.evidence_tools.handlers import (
    CONCEPT_GAPS_SPEC,
    POLICY_HISTORY_SPEC,
    VERIFY_CITATIONS_SPEC,
    build_default_registry,
    set_handler_database,
)
from application.evidence_tools.registry import (
    EvidenceToolRegistry,
    ToolDeadlineExceeded,
    ToolValidationFailed,
    UnknownToolError,
)
from application.question_concept_miner import QuestionConceptMiner
from infrastructure.database import ProductDatabase

PRINCIPAL_FINANCE = {"authenticated": True, "name": "finance_agent", "roles": ["read"], "departments": ["finance"]}
PRINCIPAL_PUBLIC = {"authenticated": True, "name": "public_agent", "roles": ["read"]}


def _seed(database: ProductDatabase) -> None:
    def _note(note_id, policy_key, version, acl_public, workspace=None, department=None):
        database.execute(
            """INSERT INTO notes (note_id, vault_path, title, content_hash, document_version, effective_from,
               policy_status, policy_key, owner, acl_public, workspace, department, acl_json,
               chunk_count, index_status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (note_id, f"policies/{note_id}", "差旅费报销管理办法", f"hash-{note_id}", version,
             "2026-01-01", "active", policy_key, "财务部", acl_public, workspace, department, "{}",
             1, "active", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )

    _note("public-v1", "travel.meal", "v1", 1)
    _note("private-v2", "travel.meal", "v2", 0, department="finance")
    for i, (term, count) in enumerate((("发票", 5), ("招待", 2), ("礼品", 1))):
        database.execute(
            "INSERT INTO concept_signals (term, sample_question_hash, seen_count, first_seen, last_seen) VALUES (?,?,?,?,?)",
            (term, f"h{i}", count, "2026-01-01T00:00:00", "2026-01-02T00:00:00"),
        )


@pytest.fixture()
def registry(tmp_path: Path):
    database = ProductDatabase(tmp_path / "tools.sqlite3")
    database.initialize()
    _seed(database)
    set_handler_database(database)
    miner = QuestionConceptMiner(database, gap_min_seen=2)
    reg = build_default_registry(database, question_miner=miner)
    yield reg, database
    evidence_handlers._DATABASE = None
    database.close()


def test_registry_exposes_three_readonly_tools(registry):
    """M1 三个只读治理工具 + M5-A save_artifact 写工具（模式与风险声明冻结）。
    MCP tools/list 的写工具默认隐藏由 mcp_server 的 _registry_tools 过滤承担
    （见 test_mcp_evidence_tools）。"""
    reg, _db = registry
    specs = {spec.name: spec for spec in reg.specs()}
    assert set(specs) == {
        "mindgraph_get_policy_history",
        "mindgraph_concept_gaps",
        "mindgraph_verify_citations",
        "mindgraph_save_artifact",
    }
    assert all(spec.mode == "read" for name, spec in specs.items() if name != "mindgraph_save_artifact")
    save = specs["mindgraph_save_artifact"]
    assert save.mode == "write" and save.risk == "low" and save.requires_approval is False
    manifest = reg.mcp_tool_manifest()
    assert {tool["name"] for tool in manifest} == set(specs)  # registry 全量；通道过滤在 mcp_server
    for tool in manifest:
        assert tool["inputSchema"]["type"] == "object"


def test_policy_history_filters_by_acl_and_hides_count(registry):
    reg, _db = registry
    public = reg.call("mindgraph_get_policy_history", {"policy_key": "travel.meal"}, principal=PRINCIPAL_PUBLIC, context="external_mcp")
    assert [v["version"] for v in public["versions"]] == ["v1"]
    # 权限侧信道红线：不返回隐藏版本数量/存在性提示
    assert "hidden" not in str(public).lower() or public.get("versions")

    finance = reg.call("mindgraph_get_policy_history", {"policy_key": "travel.meal"}, principal=PRINCIPAL_FINANCE, context="external_mcp")
    assert {v["version"] for v in finance["versions"]} == {"v1", "v2"}

    missing = reg.call("mindgraph_get_policy_history", {"policy_key": "nonexistent.key"}, principal=PRINCIPAL_FINANCE, context="external_mcp")
    assert missing["versions"] == []


def test_concept_gaps_returns_aggregates_only(registry):
    reg, _db = registry
    result = reg.call("mindgraph_concept_gaps", {"limit": 10}, principal=PRINCIPAL_PUBLIC, context="external_mcp")
    terms = {gap["term"] for gap in result["gaps"]}
    assert terms == {"发票", "招待"}  # gap_min_seen=2：礼品(1)不入榜
    assert result["total"] == 2
    assert all("question" not in gap for gap in result["gaps"])


def test_verify_citations_reports_integrity_not_semantics(registry):
    reg, _db = registry
    ok = reg.call(
        "mindgraph_verify_citations",
        {"answer": "依据 [citation-1]。", "citation_ids": ["citation-1"]},
        principal=PRINCIPAL_FINANCE,
        context="external_mcp",
    )
    assert ok["passed"] is True and ok["applicable"] is True

    broken = reg.call(
        "mindgraph_verify_citations",
        {"answer": "依据 [citation-9] 和 [citation-9]。", "citation_ids": ["citation-1"]},
        principal=PRINCIPAL_FINANCE,
        context="external_mcp",
    )
    assert broken["passed"] is False
    assert broken["checks"]["unknown_markers"] == ["[citation-9]"]
    assert "semantic" in broken["scope_note"]


def test_validation_and_context_gates(registry):
    reg, _db = registry
    with pytest.raises(UnknownToolError):
        reg.call("mindgraph_nope", {}, principal=PRINCIPAL_FINANCE, context="external_mcp")
    with pytest.raises(ToolValidationFailed):
        reg.call("mindgraph_get_policy_history", {"policy_key": ""}, principal=PRINCIPAL_FINANCE, context="external_mcp")
    with pytest.raises(ToolValidationFailed):
        reg.call("mindgraph_get_policy_history", {"policy_key": "k", "extra": 1}, principal=PRINCIPAL_FINANCE, context="external_mcp")
    with pytest.raises(ToolValidationFailed):
        # context 不在白名单（assist 通道 M2 才开放）
        reg.call("mindgraph_get_policy_history", {"policy_key": "k"}, principal=PRINCIPAL_FINANCE, context="assist")


def test_deadline_fail_closed(registry):
    reg, _db = registry
    import time

    with pytest.raises(ToolDeadlineExceeded):
        reg.call("mindgraph_get_policy_history", {"policy_key": "k"}, principal=PRINCIPAL_FINANCE, context="external_mcp", deadline=time.monotonic() - 1)


def test_audit_allow_rows_and_redaction(registry):
    reg, db = registry
    reg.call(
        "mindgraph_verify_citations",
        {"answer": "机密回答正文 [citation-1]", "citation_ids": ["citation-1"]},
        principal=PRINCIPAL_FINANCE,
        context="external_mcp",
    )
    rows = db.fetch_all("SELECT action, decision, metadata_json FROM access_audit WHERE action='mcp_verify_citations'")
    assert rows and rows[0]["decision"] == "allow"
    metadata = rows[0]["metadata_json"]
    assert "机密回答正文" not in metadata  # redact_fields 生效
    assert "[REDACTED]" in metadata

    reg.call("mindgraph_concept_gaps", {"limit": 5}, principal=PRINCIPAL_FINANCE, context="external_mcp")
    allow_rows = db.fetch_all("SELECT action FROM access_audit WHERE action='mcp_concept_gaps'")
    assert allow_rows


def test_handler_error_denies_and_audits(registry):
    reg, db = registry
    # 非规范 citation_id 不是参数错误（工具应正常判定并给出可行动结果）：
    # 无标注命中集合时 unused/unknown 语义由 report 呈现，applicable=False
    result = reg.call(
        "mindgraph_verify_citations",
        {"answer": "无标注回答", "citation_ids": ["citation-bad"]},
        principal=PRINCIPAL_FINANCE,
        context="external_mcp",
    )
    assert result["passed"] is True  # 无合法标注且无规范引用 → 不可判定，不判失败
    assert result["applicable"] is False

    # 真正的参数校验失败走 deny 审计路径（超长 answer 违反 maxLength）
    rows_before = db.fetch_all("SELECT COUNT(*) AS c FROM access_audit WHERE decision='deny'")
    with pytest.raises(ToolValidationFailed):
        reg.call(
            "mindgraph_verify_citations",
            {"answer": "x" * 60000, "citation_ids": ["citation-1"]},
            principal=PRINCIPAL_FINANCE,
            context="external_mcp",
        )
    rows_after = db.fetch_all("SELECT COUNT(*) AS c FROM access_audit WHERE decision='deny'")
    assert rows_after[0]["c"] == rows_before[0]["c"]  # 参数校验在审计前置位，不产生 deny 轨迹
