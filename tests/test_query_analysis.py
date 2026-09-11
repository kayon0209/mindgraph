"""PR-10｜QueryAnalyzer Shadow 模式（契约 + 确定性分析器）。

## 为什么是 shadow

``QueryUnderstandingService`` **已经在生产路径上**（``chat_service.py:91``），
它做的是规则版查询改写/拆解，直接影响检索变体。本 PR 的分析器是**独立的观测层**：
输出只写 trace，**不参与路由、检索或生成**——否则就不是 shadow 而是改生产路由了。

## 与现有 confidence 的区别

``adaptive_retrieval_router.py:287`` 的 confidence 是**两档硬编码**（0.85 / 0.95），
不代表真实置信度。本分析器的 confidence 由实际识别到的信号**计算**得出，
并可在信号互相矛盾时下调。

测试矩阵（任务书）：单目标、多目标、缺槽、指代、版本、多个问号但语义单一、越界问题。
"""
from __future__ import annotations

from typing import Any

import pytest

from application.query_analysis import (
    QueryAnalysisService,
    analyze_disagreement,
)


@pytest.fixture()
def analyzer() -> QueryAnalysisService:
    return QueryAnalysisService()


# ── 矩阵 1：单目标 ────────────────────────────────────────────────────────


def test_single_target_is_simple(analyzer: QueryAnalysisService):
    result = analyzer.analyze("差旅费报销的时限是几天？")
    assert result.target_count == 1
    assert result.intent == "factual"
    assert result.complexity["target_count"] == 1
    assert not result.missing_slots or "referent" not in result.missing_slots


# ── 矩阵 2：多目标 ────────────────────────────────────────────────────────


def test_multiple_targets_raise_complexity(analyzer: QueryAnalysisService):
    result = analyzer.analyze("请分别说明住宿和交通的审批要求。")
    assert result.target_count >= 2
    assert result.complexity["target_count"] >= 2


def test_multiple_question_marks_but_semantically_single(analyzer: QueryAnalysisService):
    """多个问号但语义单一：问号数≠目标数，不能简单按问号切。"""
    result = analyzer.analyze("报销时限是几天？怎么算？")
    # 两个问号 → 2 个子问；但都在问同一件事 → 语义目标仍算 1。
    # 子问数与目标数分开暴露，正是为了不让"问号数"被当成"复杂度"。
    assert result.sub_question_count == 2
    assert result.target_count == 1


# ── 矩阵 3：缺槽 ──────────────────────────────────────────────────────────


def test_missing_subject_slot_detected(analyzer: QueryAnalysisService):
    """问"多少钱"却没说清是什么费用 → 缺主体槽。"""
    result = analyzer.analyze("这个能报多少钱？")
    assert "policy_subject" in result.missing_slots


# ── 矩阵 4：指代 ──────────────────────────────────────────────────────────


def test_anaphora_detected(analyzer: QueryAnalysisService):
    result = analyzer.analyze("该制度规定的时限是多久？")
    assert result.has_anaphora is True
    assert "referent" in result.missing_slots
    # 有指代 → 置信度必须被拉低（不能假装很确定）
    assert result.confidence < 0.9


def test_no_anaphora_when_question_is_self_contained(analyzer: QueryAnalysisService):
    result = analyzer.analyze("差旅费报销管理办法规定的报销时限是几天？")
    assert result.has_anaphora is False


# ── 矩阵 5：版本 ──────────────────────────────────────────────────────────


def test_version_intent_detected(analyzer: QueryAnalysisService):
    result = analyzer.analyze("新旧报销时限规则矛盾时以哪个为准？")
    assert result.intent == "conflict"
    assert result.has_date_version is True


def test_date_version_signal(analyzer: QueryAnalysisService):
    result = analyzer.analyze("2026年之后的差旅标准是什么？")
    assert result.has_date_version is True


# ── 矩阵 6：跨制度 ────────────────────────────────────────────────────────


def test_cross_policy_detected(analyzer: QueryAnalysisService):
    result = analyzer.analyze("同一餐次既申报客户招待费又领差旅餐补是怎么规定的？")
    assert result.is_cross_policy is True


# ── 矩阵 7：越界 ──────────────────────────────────────────────────────────


def test_out_of_scope_detected(analyzer: QueryAnalysisService):
    """与生产词表（``scope_terms.OUT_OF_SCOPE_TERMS``）保持一致——shadow 必须与生产同源。"""
    result = analyzer.analyze("公司wifi密码是多少？")
    assert result.intent == "out_of_scope"


def test_off_domain_flagged_when_no_policy_signal(analyzer: QueryAnalysisService):
    """生产词表只有 13 个 HR 类词，**不含「天气」这类完全无关的问题**。

    这类问题当前不会被拦截（会走检索然后答非所问）。分析器不假装它是正常
    factual，而是单独标 ``off_domain`` 风险，让词表缺口可见。
    """
    result = analyzer.analyze("今天天气怎么样？")
    assert result.risk["off_domain"] is True
    assert result.intent != "out_of_scope"


# ── 契约：不可变 + 可序列化 + confidence 真计算 ───────────────────────────


def test_analysis_is_frozen(analyzer: QueryAnalysisService):
    result = analyzer.analyze("差旅费报销时限？")
    with pytest.raises(Exception):  # noqa: B017 -- frozen dataclass 抛 FrozenInstanceError
        result.intent = "changed"  # type: ignore[misc]


def test_analysis_serializable(analyzer: QueryAnalysisService):
    payload = analyzer.analyze("差旅费报销时限？").to_dict()
    assert set(payload) >= {"intent", "entities", "missing_slots", "complexity", "risk", "confidence", "reasons"}
    # 必须能被 JSON 序列化（要写进 trace）
    import json

    json.dumps(payload, ensure_ascii=False)


def test_confidence_is_computed_not_hardcoded(analyzer: QueryAnalysisService):
    """router 的 0.85/0.95 是硬编码；分析器必须随信号变化。"""
    weak = analyzer.analyze("这个怎么弄？")          # 指代 + 缺主体
    strong = analyzer.analyze("差旅费报销管理办法规定的报销时限是几天？")
    assert weak.confidence < strong.confidence
    assert 0.0 < weak.confidence < 1.0


def test_sensitive_text_not_stored_by_default(analyzer: QueryAnalysisService):
    """范围外：不把敏感原文写入默认日志 → 默认输出不带原始问题全文。"""
    payload = analyzer.analyze("差旅费报销时限？").to_dict()
    assert "差旅费报销时限？" not in str(payload)


# ── 分歧报告：与旧 route 对比，且分歧必须可解释 ───────────────────────────


def test_disagreement_report_is_explainable(analyzer: QueryAnalysisService):
    report = analyze_disagreement([
        {"case_id": "a", "question": "该制度规定的时限是多久？", "route": "factual"},
        {"case_id": "b", "question": "今天天气怎么样？", "route": "factual"},
    ], analyzer)
    assert report["case_count"] == 2
    assert report["disagreement_count"] >= 1
    for item in report["disagreements"]:
        assert item["reasons"], "分歧必须给出理由，否则无法判断谁对"


def test_disagreement_reports_agreement_too(analyzer: QueryAnalysisService):
    report = analyze_disagreement([
        {"case_id": "a", "question": "差旅费报销的时限是几天？", "route": "factual"},
    ], analyzer)
    assert report["agreement_count"] == 1


# ── shadow 的安全契约：它绝不能影响生产 ────────────────────────────────────


class _FakeTrace:
    """只提供 shadow 允许触碰的两个容器。"""

    def __init__(self) -> None:
        self.latency_ms: dict[str, float] = {}
        self.query_analysis: dict[str, Any] = {}


def _service_with(analyzer) -> Any:
    """绕过 ``ChatService.__init__``（它需要 DB / provider / pipeline）。"""
    from application.chat_service import ChatService

    service = ChatService.__new__(ChatService)
    service.query_analysis = analyzer
    return service


def test_shadow_only_writes_analysis_fields():
    """shadow 只能往 trace 写 query_analysis 与自己的耗时，别的字段一概不碰。"""
    trace = _FakeTrace()
    _service_with(QueryAnalysisService())._attach_query_analysis(trace, "该制度规定的时限是多久？")
    assert trace.query_analysis["has_anaphora"] is True
    assert set(trace.latency_ms) == {"query_analysis_ms"}  # 不许动 routing_ms 等


def test_shadow_failure_is_swallowed_and_never_breaks_production():
    """观测层挂了可以接受，因为它拖垮生产问答不可接受。"""
    class _Boom:
        def analyze(self, question: str):
            raise RuntimeError("boom")

    trace = _FakeTrace()
    _service_with(_Boom())._attach_query_analysis(trace, "差旅费报销时限？")  # 不抛
    assert trace.query_analysis == {}
    assert trace.latency_ms == {}
