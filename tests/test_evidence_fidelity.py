"""M0-3：确定性引用保真检查器测试。

纯函数层：extract_citation_marks / check_citation_fidelity / fidelity_warning。
接线层：ChatService._persist 对含越界标注的终态结果追加 trace warning 并
设置 citation_fidelity 字段（warning-first，不阻断）。
"""

from __future__ import annotations

from types import SimpleNamespace

from application.evidence_fidelity import (
    check_citation_fidelity,
    extract_citation_marks,
    fidelity_warning,
)
from application.chat_service import ChatService
from domain.models import (
    AnswerResult,
    Citation,
    ResultState,
    RetrievalTraceModel,
    TimingMetrics,
)


def test_extract_citation_marks_keeps_order_and_duplicates():
    marks = extract_citation_marks("见 [citation-1] 与 [citation-3]，再核对 [citation-1]。")
    assert marks == [1, 3, 1]


def test_extract_citation_marks_ignores_unrelated_brackets():
    assert extract_citation_marks("普通括号 [1] 和 [citation] 不算标注。") == []
    assert extract_citation_marks("") == []


def test_check_ok_when_all_marks_resolve():
    report = check_citation_fidelity("依据 [citation-2] 执行。", [1, 2, 3])
    assert report.ok is True
    assert report.missing == []
    assert report.applicable is True
    assert report.referenced == [2]


def test_check_flags_missing_citation():
    report = check_citation_fidelity("依据 [citation-1] 与 [citation-9] 执行。", [1])
    assert report.ok is False
    assert report.missing == [9]
    assert report.referenced == [1, 9]


def test_check_not_applicable_without_citations_or_marks():
    report = check_citation_fidelity("纯文本回答。", [])
    assert report.applicable is False
    assert report.ok is True


def test_check_with_citations_but_no_marks_is_fine():
    report = check_citation_fidelity("依据制度执行。", [1, 2])
    assert report.applicable is True
    assert report.ok is True


def test_fidelity_warning_message_format():
    report = check_citation_fidelity("[citation-1] 与 [citation-4]", [1])
    assert fidelity_warning(report) == "citation_fidelity:missing_marks=4"
    assert fidelity_warning(check_citation_fidelity("[citation-1]", [1])) is None


def test_persist_sets_fidelity_and_appends_trace_warning(tmp_path):
    database = SimpleNamespace(
        execute=lambda *_args, **_kwargs: None,
        fetch_one=lambda *_a, **_k: None,
    )
    service = ChatService(database, pipeline_factory=None, provider=SimpleNamespace(model_name="m"))

    result = AnswerResult(
        request_id="r1",
        question="报销时限？",
        answer="见 [citation-9]。",  # 越界标注
        result_state=ResultState.answered,
        citations=[
            Citation(
                citation_id="citation-1",
                document_id="d1",
                document_name="制度",
                chunk_id="c1",
                excerpt="报销时限",
                final_rank=1,
            )
        ],
        retrieval_trace=RetrievalTraceModel(
            requested_strategy="hybrid",
            actual_strategy="hybrid",
        ),
        timing=TimingMetrics(total_ms=1.0),
        requested_strategy="hybrid",
        actual_strategy="hybrid",
        model="m",
    )
    service._persist(result)

    assert result.citation_fidelity is False
    assert "citation_fidelity:missing_marks=9" in result.retrieval_trace.warnings


def test_persist_leaves_fidelity_none_when_not_applicable(tmp_path):
    database = SimpleNamespace(
        execute=lambda *_args, **_kwargs: None,
        fetch_one=lambda *_a, **_k: None,
    )
    service = ChatService(database, pipeline_factory=None, provider=SimpleNamespace(model_name="m"))

    result = AnswerResult(
        request_id="r2",
        question="你好？",
        answer="抱歉，我只能回答公司报销相关问题。",
        result_state=ResultState.out_of_scope,
        timing=TimingMetrics(total_ms=1.0),
        requested_strategy="auto",
        actual_strategy="scope_check",
        model="m",
    )
    service._persist(result)

    assert result.citation_fidelity is None
