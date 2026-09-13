"""PR-05 拍板落地（A+B）的回归测试。

**为什么这个文件必须存在**：改动前 ``tests/test_answer_evaluation.py`` 里
``conflict_accuracy`` 的断言**全是 None**（都是非 conflict 案例）——
也就是说旧口径"所有 conflict 案例一律要求 conflicting_evidence"
**没有任何一条测试覆盖**。判据改错、改没，测试都会全绿。

这里的每条断言都指向一个具体会发生的误判：

- 把"不适用"记成 0（旧口径，等于宣称系统答错）；
- 把"适用却没报"记成 None（指标悄悄消失）；
- 分母不可见（没人能区分"没有冲突案例"与"都不适用"）。
"""
from __future__ import annotations

import pytest

from evaluation.answer_eval import evaluate_answer_case, summarize_answer_evaluations
from evaluation.conflict_attribution import (
    CROSS_POLICY_CONFLICT,
    NOT_A_CONFLICT,
    VERSION_CONFLICT,
    classify_conflict_kind,
    conflict_expectation,
)

_BASE_CASE = {
    "case_id": "conflict-1",
    "category": "conflict",
    "query_type": "conflict",
    "expected_behavior": "answer",
    "evaluation_date": "2026-08-18",
    "required_facts": [],
    "forbidden_facts": [],
    "historical_vault_paths": [],
}


def _case(**overrides):
    return {**_BASE_CASE, **overrides}


def _prediction(**overrides):
    base = {
        "case_id": "conflict-1",
        "result_state": "answered",
        "answer": "以生效日期最新的版本为准。",
        "citations": [],
        "policy_conflicts": [],
    }
    return {**base, **overrides}


# ── 类别推断 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("gold_paths", "expected"),
    [
        # 同一制度 v1/v2 并存 → 版本冲突
        (
            ["policies/expense-general-v1.md", "policies/expense-general-v2.md"],
            VERSION_CONFLICT,
        ),
        # 两份不同制度 → 跨制度冲突
        (
            ["policies/travel-meal-v2.md", "policies/client-entertainment-v2.md"],
            CROSS_POLICY_CONFLICT,
        ),
        # 只有一份制度 → 不构成冲突（数据集里存在这样的误标）
        (["policies/expense-general-v2.md"], NOT_A_CONFLICT),
        ([], NOT_A_CONFLICT),
    ],
)
def test_classify_conflict_kind_from_gold_paths(gold_paths: list[str], expected: str) -> None:
    assert classify_conflict_kind(_case(gold_vault_paths=gold_paths)) == expected


def test_classify_is_deterministic_and_ignores_directory() -> None:
    """分类只看制度标识，不受目录层级影响——否则换个目录就会改变指标口径。"""
    a = classify_conflict_kind(_case(gold_vault_paths=["policies/x-v1.md", "policies/x-v2.md"]))
    b = classify_conflict_kind(_case(gold_vault_paths=["a/b/nested/x-v1.md", "a/b/nested/x-v2.md"]))
    assert a == b == VERSION_CONFLICT


def test_version_conflict_is_the_only_kind_that_expects_a_conflict_state() -> None:
    """只有版本冲突期望系统**报出**冲突状态；其余类别不得被要求（否则恒 0）。"""
    version = conflict_expectation(_case(gold_vault_paths=["x-v1.md", "x-v2.md"]))
    cross = conflict_expectation(_case(gold_vault_paths=["a-v2.md", "b-v2.md"]))
    none = conflict_expectation(_case(gold_vault_paths=["a-v2.md"]))

    assert version["applicable"] is True
    assert version["expected_state"] == "conflicting_evidence"
    assert (cross["applicable"], cross["expected_state"]) == (False, None)
    assert (none["applicable"], none["expected_state"]) == (False, None)


def test_conflict_expectation_notes_when_no_multi_active_version_exists() -> None:
    """旧版本已归档时，系统按现行版本作答是正确行为 —— 要记进 notes，不能当漏报。"""
    expectation = conflict_expectation(
        _case(gold_vault_paths=["policies/expense-general-v1.md", "policies/expense-general-v2.md"]),
        policy_versions={
            "expense.general": [
                {"version": "1.0", "policy_status": "archived"},
                {"version": "2.0", "policy_status": "active"},
            ]
        },
    )
    assert expectation["applicable"] is True  # 指标仍适用：问答要求系统说明存在版本冲突
    assert any("历史版本对比检索" in note for note in expectation["notes"])


# ── 判分契约 ──────────────────────────────────────────────────────────────

def test_version_conflict_reported_scores_one() -> None:
    case = _case(gold_vault_paths=["x-v1.md", "x-v2.md"])
    result = evaluate_answer_case(case, _prediction(result_state="conflicting_evidence"))
    assert result["conflict_accuracy"] == 1.0
    assert result["conflict_kind"] == VERSION_CONFLICT
    assert result["conflict_applicable"] is True
    assert "conflict_not_intercepted" not in result["failures"]


def test_version_conflict_missed_scores_zero_and_is_a_failure() -> None:
    """适用却没报 = 真失败，必须体现在 failures 里（不能因为记为 None 而消失）。"""
    case = _case(gold_vault_paths=["x-v1.md", "x-v2.md"])
    result = evaluate_answer_case(case, _prediction(result_state="answered"))
    assert result["conflict_accuracy"] == 0.0
    assert "conflict_not_intercepted" in result["failures"]


def test_cross_policy_case_is_not_applicable_and_not_a_failure() -> None:
    """跨制度冲突期望"识别并解释"，不要求报冲突状态 —— 记 None，不记 0、不记失败。

    这正是旧口径下 conflict_accuracy 恒为 0 的来源：6 个案例里有这类语义，
    系统答案其实正确（"不得重复申领，扣 60 元"），却被判成全错。
    """
    case = _case(gold_vault_paths=["travel-meal-v2.md", "client-entertainment-v2.md"])
    result = evaluate_answer_case(case, _prediction(result_state="answered"))
    assert result["conflict_accuracy"] is None
    assert result["conflict_kind"] == CROSS_POLICY_CONFLICT
    assert result["conflict_applicable"] is False
    assert "conflict_not_intercepted" not in result["failures"]


def test_case_without_version_coexistence_is_not_applicable() -> None:
    case = _case(gold_vault_paths=["expense-general-v2.md"])
    result = evaluate_answer_case(case, _prediction(result_state="answered"))
    assert result["conflict_accuracy"] is None
    assert result["conflict_kind"] == NOT_A_CONFLICT


def test_non_conflict_case_still_reports_none() -> None:
    """回归：非 conflict 案例不得因为本次改动开始计分。"""
    case = {"case_id": "a", "category": "factual", "expected_behavior": "answer",
            "evaluation_date": "2026-08-18", "gold_vault_paths": ["x-v1.md", "x-v2.md"],
            "required_facts": [], "forbidden_facts": [], "historical_vault_paths": []}
    result = evaluate_answer_case(case, _prediction(result_state="conflicting_evidence"))
    assert result["conflict_accuracy"] is None
    assert result["conflict_kind"] is None


def test_summary_exposes_the_denominator() -> None:
    """分母必须可见：否则"不适用记 None"会让指标从报表上悄悄消失。

    3 条 conflict：1 条适用且报对、2 条不适用 → conflict_accuracy 应为 1.0
    而不是 0.333（旧口径）也不是 None。
    """
    results = [
        evaluate_answer_case(_case(case_id="c1", gold_vault_paths=["x-v1.md", "x-v2.md"]),
                             _prediction(case_id="c1", result_state="conflicting_evidence")),
        evaluate_answer_case(_case(case_id="c2", gold_vault_paths=["a-v2.md", "b-v2.md"]),
                             _prediction(case_id="c2", result_state="answered")),
        evaluate_answer_case(_case(case_id="c3", gold_vault_paths=["a-v2.md"]),
                             _prediction(case_id="c3", result_state="answered")),
    ]
    summary = summarize_answer_evaluations(results)
    assert summary["metrics"]["conflict_accuracy"] == 1.0
    assert summary["conflict_breakdown"] == {
        "conflict_case_count": 3,
        "by_kind": {VERSION_CONFLICT: 1, CROSS_POLICY_CONFLICT: 1, NOT_A_CONFLICT: 1},
        "applicable_count": 1,
        "scored_denominator": 1,
    }
    # 不适用案例不得因为冲突被判失败 —— 否则"口径不适用"会被读成系统做错
    # （此处不断言 failures 为空：无引用的 fixture 会触发 invalid_policy_version 等其它码）
    assert not any(
        "conflict_not_intercepted" in item["failures"] for item in summary["failed_cases"]
    )


def test_summary_reports_zero_when_applicable_case_is_missed() -> None:
    results = [
        evaluate_answer_case(_case(case_id="c1", gold_vault_paths=["x-v1.md", "x-v2.md"]),
                             _prediction(case_id="c1", result_state="answered")),
    ]
    summary = summarize_answer_evaluations(results)
    assert summary["metrics"]["conflict_accuracy"] == 0.0
    assert summary["conflict_breakdown"]["scored_denominator"] == 1


def test_breakdown_is_none_when_no_conflict_cases() -> None:
    results = [
        evaluate_answer_case(
            {"case_id": "a", "category": "factual", "expected_behavior": "answer",
             "evaluation_date": "2026-08-18", "gold_vault_paths": ["x.md"],
             "required_facts": [], "forbidden_facts": [], "historical_vault_paths": []},
            _prediction(case_id="a"),
        )
    ]
    assert summarize_answer_evaluations(results)["conflict_breakdown"] is None
