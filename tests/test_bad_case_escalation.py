"""PR-14 Bad-case 全链路快照与重复失败升级。

任务书测试矩阵：重复阈值、误聚类、跨会话/跨主体、脱敏、幂等、回归导出。

现场核对修正后的设计（不建第二事实源）：
- 归因数据从 query_logs.trace_json JOIN 读出——trace 已含 route/variants/
  各阶段候选/版本/degraded；不往 bad_cases 加快照列；
- 重复检测用 query_logs.question_hash（带盐、已存在）跨 request_id 匹配；
- 升级信号写 additive 新表 bad_case_escalations，bad_cases schema 不动；
- 写入端位置式 INSERT 先改显式列名（修正 2 地雷）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from infrastructure.database import ProductDatabase


@pytest.fixture
def escalation_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """升级检测需要显式开 flag（默认关 = 回滚开关语义）。"""
    monkeypatch.setenv("BAD_CASE_ESCALATION_ENABLED", "true")


@pytest.fixture
def db(tmp_path: Path) -> ProductDatabase:
    database = ProductDatabase(tmp_path / "badcase.sqlite3")
    database.initialize()
    return database


def _seed_query_log(
    db: ProductDatabase, request_id: str, *, question: str = "差旅费怎么报",
    question_hash: str | None = None, answer: str = "依据不足。",
    trace: dict | None = None, result_state: str = "insufficient_evidence",
) -> None:
    # principal_id 必须写：PR-02 后 create_feedback 按归属校验，NULL fail-closed。
    db.execute(
        "INSERT INTO query_logs (request_id, question, question_hash, answer, result_state, requested_strategy,"
        " actual_strategy, trace_json, citations_json, timing_json, usage_json, created_at, principal_id)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (request_id, question, question_hash or f"hash-{question}", answer, result_state,
         "hybrid", "hybrid", json.dumps(trace or {}), "[]", "{}", "{}", "2026-09-12T00:00:00", "user-a"),
    )


def _seed_bad_case(db: ProductDatabase, request_id: str, *, status: str = "new") -> str:
    from application.feedback_service import FeedbackService

    FeedbackService(db).create_feedback(
        __import__("domain.models", fromlist=["FeedbackCreate"]).FeedbackCreate(
            request_id=request_id, rating="not_helpful",
        ),
        principal_id="user-a",
    )
    bad_case_id = db.fetch_one("SELECT bad_case_id FROM bad_cases WHERE request_id=?", (request_id,))["bad_case_id"]
    db.execute("UPDATE bad_cases SET status=? WHERE bad_case_id=?", (status, bad_case_id))
    return bad_case_id


# ── 归因：从 trace_json JOIN 读出全链路证据 ──────────────────────────


def test_bad_case_attribution_joins_query_logs_trace(db: ProductDatabase):
    """归因视图：route/variants/候选阶段/版本全部可读出（数据已在 trace）。"""
    from application.bad_case_attribution import attribute_bad_case

    trace = {
        "route_decision": {"route": "factual", "selected_strategy": "hybrid"},
        "query_variants": ["差旅费怎么报"],
        "candidate_counts": {"dense": 14, "sparse": 20, "fused": 20, "final": 5},
        "index_version": "mg-test",
        "degraded": False,
    }
    _seed_query_log(db, "req-attr-1", trace=trace)
    _seed_bad_case(db, "req-attr-1")

    report = attribute_bad_case(db, "req-attr-1")
    assert report["route"] == "factual"
    assert report["candidate_counts"]["dense"] == 14
    assert report["index_version"] == "mg-test"
    assert report["query_variants"] == ["差旅费怎么报"]


def test_attribution_marks_missing_trace_as_unknown_not_garbage(db: ProductDatabase):
    """无 trace（历史/旧记录）→ unknown 显式标记，不假装可归因。"""
    from application.bad_case_attribution import attribute_bad_case

    _seed_query_log(db, "req-attr-2", trace=None)
    _seed_bad_case(db, "req-attr-2")
    report = attribute_bad_case(db, "req-attr-2")
    assert report["route"] is None
    assert report["attribution_available"] is False


# ── 重复失败升级 ──────────────────────────────────────────────────────


def test_two_not_helpful_same_question_escalates(db: ProductDatabase, escalation_flag):
    """同 question_hash 两路 not_helpful → escalation_suggested（跨 request_id）。"""
    from application.bad_case_attribution import check_escalation

    _seed_query_log(db, "req-e1", question="差旅费怎么报", question_hash="hash-same-q")
    _seed_query_log(db, "req-e2", question="差旅费如何报销", question_hash="hash-same-q")
    _seed_bad_case(db, "req-e1")
    _seed_bad_case(db, "req-e2")

    decision = check_escalation(db, "req-e2")
    assert decision.escalation_suggested is True
    assert decision.reason == "repeated_not_helpful"
    assert decision.related_request_ids == ["req-e1", "req-e2"]


def test_single_failure_no_escalation(db: ProductDatabase):
    """单次失败不升级（阈值语义：≥2 才升级）。"""
    from application.bad_case_attribution import check_escalation

    _seed_query_log(db, "req-e3", question_hash="hash-uniq-1")
    _seed_bad_case(db, "req-e3")
    decision = check_escalation(db, "req-e3")
    assert decision.escalation_suggested is False


def test_three_same_question_logs_escalate(db: ProductDatabase, escalation_flag):
    """同一问句三路问答（即使只有一次 not_helpful）→ escalation（3 次相近提问）。"""
    from application.bad_case_attribution import check_escalation

    for i in range(3):
        _seed_query_log(db, f"req-q{i}", question_hash="hash-three-times")
    _seed_bad_case(db, "req-q0")
    decision = check_escalation(db, "req-q0")
    assert decision.escalation_suggested is True
    assert decision.reason == "repeated_question"


def test_different_questions_not_clustered(db: ProductDatabase):
    """误聚类防线：不同 question_hash 不算重复（各自独立）。"""
    from application.bad_case_attribution import check_escalation

    _seed_query_log(db, "req-a", question="差旅费", question_hash="hash-a")
    _seed_query_log(db, "req-b", question="餐补标准", question_hash="hash-b")
    _seed_bad_case(db, "req-a")
    _seed_bad_case(db, "req-b")
    assert check_escalation(db, "req-a").escalation_suggested is False
    assert check_escalation(db, "req-b").escalation_suggested is False


def test_escalation_idempotent(db: ProductDatabase, escalation_flag):
    """升级信号幂等：同一 request 重复检查只落一条 escalation 记录。"""
    from application.bad_case_attribution import record_escalation_if_needed

    _seed_query_log(db, "req-idem", question_hash="hash-idem")
    _seed_query_log(db, "req-idem-2", question_hash="hash-idem")
    _seed_bad_case(db, "req-idem")
    _seed_bad_case(db, "req-idem-2")

    first = record_escalation_if_needed(db, "req-idem")
    second = record_escalation_if_needed(db, "req-idem")
    assert first == second  # 同一升级（同键幂等，不重复落）
    count = db.fetch_one("SELECT COUNT(*) AS c FROM bad_case_escalations")["c"]
    assert count == 1


def test_escalation_respects_flag(db: ProductDatabase, monkeypatch: pytest.MonkeyPatch):
    """flag 关闭（默认）→ 不升级（回滚即关）。"""
    from application import bad_case_attribution as bca

    monkeypatch.delenv("BAD_CASE_ESCALATION_ENABLED", raising=False)
    _seed_query_log(db, "req-flag", question_hash="hash-flag")
    _seed_query_log(db, "req-flag-2", question_hash="hash-flag")
    _seed_bad_case(db, "req-flag")
    _seed_bad_case(db, "req-flag-2")
    decision = bca.check_escalation(db, "req-flag")
    assert decision.escalation_suggested is False


# ── 写入端显式列名（修正 2 地雷）+ 导出兼容 ──────────────────────────


def test_bad_case_insert_uses_explicit_columns(db: ProductDatabase):
    """位置式 INSERT 改显式列名后，新增列不再破坏写入（schema 演进安全）。"""
    import inspect

    from application import feedback_service as fs

    source = inspect.getsource(fs.FeedbackService.create_feedback)
    assert "INSERT OR IGNORE INTO bad_cases (" in source, \
        "bad_cases 写入必须用显式列名（位置式插入会让任何 ADD COLUMN 直接报错）"


def test_resolved_export_regression_candidate_unchanged(db: ProductDatabase):
    """回归导出骨架保持：resolved → regression_candidate=True（修正 4）。"""
    from application.feedback_service import FeedbackService

    _seed_query_log(db, "req-exp", answer="旧回答")
    bad_case_id = _seed_bad_case(db, "req-exp")
    db.execute("UPDATE bad_cases SET status='resolved' WHERE bad_case_id=?", (bad_case_id,))
    export = FeedbackService(db).export_bad_cases()
    lines = export.splitlines()
    assert "regression_candidate" in lines[0]
    assert "True" in lines[1]
