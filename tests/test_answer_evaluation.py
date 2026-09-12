import pytest

from evaluation.answer_eval import (
    evaluate_answer_case,
    evaluate_answer_predictions,
    summarize_answer_evaluations,
)


def _citation(
    vault_path: str,
    *,
    status: str = "active",
    effective_from: str = "2026-01-01",
    effective_to: str | None = None,
    rank: int | None = None,
) -> dict:
    citation = {
        "vault_path": vault_path,
        "policy_status": status,
        "effective_from": effective_from,
        "effective_to": effective_to,
    }
    if rank is not None:
        citation["citation_id"] = f"citation-{rank}"
        citation["final_rank"] = rank
    return citation


def test_answer_case_scores_supported_current_answer() -> None:
    """Catches correct cited answers being reported as untrusted."""
    result = evaluate_answer_case(
        {
            "case_id": "current-policy",
            "expected_behavior": "answer",
            "evaluation_date": "2026-08-18",
            "gold_vault_paths": ["policies/current.md"],
            "historical_vault_paths": [],
            "required_facts": ["30个自然日"],
            "forbidden_facts": ["60个自然日"],
        },
        {
            "result_state": "answered",
            "answer": "应在30个自然日内提交 [citation-1]。",
            "citations": [_citation("policies/current.md", effective_from="2026-07-01", rank=1)],
        },
    )

    assert {key: result[key] for key in (
        "case_id", "citation_correctness", "citation_precision", "citation_recall",
        "citation_usage_ratio", "refusal_correctness", "version_validity",
        "required_fact_coverage", "forbidden_fact_avoidance", "failures",
    )} == {
        "case_id": "current-policy",
        "citation_correctness": 1.0,
        "citation_precision": 1.0,
        "citation_recall": 1.0,
        "citation_usage_ratio": 1.0,
        "refusal_correctness": 1.0,
        "version_validity": 1.0,
        "required_fact_coverage": 1.0,
        "forbidden_fact_avoidance": 1.0,
        "failures": [],
    }


def test_answered_case_without_any_citation_marker_is_penalized() -> None:
    """Catches "有候选证据却一条都不引用"被当成不可判定而逃过计分（v2 新规则）。"""
    result = evaluate_answer_case(
        {
            "case_id": "uncited",
            "expected_behavior": "answer",
            "evaluation_date": "2026-08-18",
            "gold_vault_paths": ["policies/current.md"],
            "historical_vault_paths": [],
            "required_facts": [],
            "forbidden_facts": [],
        },
        {
            "result_state": "answered",
            "answer": "应在30个自然日内提交。",
            "citations": [_citation("policies/current.md", effective_from="2026-07-01", rank=1)],
        },
    )

    assert result["citation_correctness"] == 0.0
    assert result["citation_recall"] == 0.0
    assert result["citation_precision"] is None
    assert result["citation_usage_ratio"] == 0.0
    assert "citation_mismatch" in result["failures"]


def test_citation_id_only_prediction_maps_the_cited_path() -> None:
    """A citation-id-only prediction must map its marker to the cited path."""
    citation = _citation("policies/current.md", effective_from="2026-07-01")
    citation["citation_id"] = "citation-1"
    result = evaluate_answer_case(
        {
            "case_id": "citation-id-only",
            "expected_behavior": "answer",
            "evaluation_date": "2026-08-18",
            "gold_vault_paths": ["policies/current.md"],
            "historical_vault_paths": [],
            "required_facts": [],
            "forbidden_facts": [],
        },
        {
            "result_state": "answered",
            "answer": "结论 [citation-1]。",
            "citations": [citation],
        },
    )

    assert result["citation_correctness"] == 1.0
    assert result["citation_precision"] == 1.0
    assert result["citation_recall"] == 1.0


def test_unused_offered_evidence_is_not_a_marker_defect() -> None:
    """Catches 把「检索到但未引用」判成引用标注缺陷（v1 的口径错误）。

    系统提示词只要求「使用 [citation-N] 标注引用来源」，未要求每条候选都被引用；
    3 条候选里引用 1 条是合法输出，``citation_marker_validity`` 必须仍为 1.0，
    而"用了多少"由 ``citation_usage_ratio`` 如实记录。
    """
    result = evaluate_answer_case(
        {
            "case_id": "partially-used",
            "expected_behavior": "answer",
            "evaluation_date": "2026-08-18",
            "gold_vault_paths": ["policies/a.md"],
            "historical_vault_paths": [],
            "required_facts": [],
            "forbidden_facts": [],
        },
        {
            "result_state": "answered",
            "answer": "结论 [citation-1]。",
            "citations": [
                _citation("policies/a.md", rank=1),
                _citation("policies/b.md", rank=2),
                _citation("policies/c.md", rank=3),
            ],
        },
    )

    assert result["citation_marker_validity"] == 1.0
    assert result["citation_usage_ratio"] == pytest.approx(1 / 3)
    assert "citation_marker_integrity" not in result["failures"]


def test_fact_matching_normalizes_markdown_and_whitespace() -> None:
    """Catches Gold 事实「30个自然日」匹配不上模型输出「**30 个自然日**」（v2 口径修复）。"""
    result = evaluate_answer_case(
        {
            "case_id": "formatting",
            "expected_behavior": "answer",
            "evaluation_date": "2026-08-18",
            "gold_vault_paths": ["policies/a.md"],
            "historical_vault_paths": [],
            "required_facts": ["30个自然日"],
            "forbidden_facts": ["60个自然日"],
        },
        {
            "result_state": "answered",
            "answer": "结论：应在 **30 个自然日** 内提交 [citation-1]。",
            "citations": [_citation("policies/a.md", rank=1)],
        },
    )

    assert result["required_fact_coverage"] == 1.0
    assert result["forbidden_fact_avoidance"] == 1.0
    assert "missing_required_fact" not in result["failures"]


def test_citation_correctness_penalizes_missing_and_unrelated_sources() -> None:
    """Catches a citation precision-only score hiding missing required evidence."""
    result = evaluate_answer_case(
        {
            "case_id": "multi-source",
            "expected_behavior": "answer",
            "evaluation_date": "2026-08-18",
            "gold_vault_paths": ["policies/a.md", "workflows/b.md"],
            "historical_vault_paths": [],
            "required_facts": [],
            "forbidden_facts": [],
        },
        {
            "result_state": "answered",
            "answer": "结论 [citation-1] [citation-2]",
            "citations": [
                _citation("policies/a.md", rank=1),
                _citation("policies/unrelated.md", rank=2),
            ],
        },
    )

    assert result["citation_correctness"] == 0.5
    assert result["citation_precision"] == 0.5
    assert result["citation_recall"] == 0.5
    assert "citation_mismatch" in result["failures"]


def test_refusal_correctness_distinguishes_expected_abstention_from_answer() -> None:
    """Catches unsupported answers being counted as correct refusals."""
    case = {
        "case_id": "no-answer",
        "expected_behavior": "abstain",
        "evaluation_date": "2026-08-18",
        "gold_vault_paths": [],
        "historical_vault_paths": [],
        "required_facts": [],
        "forbidden_facts": [],
    }

    refused = evaluate_answer_case(
        case,
        {"result_state": "insufficient_evidence", "answer": "依据不足", "citations": []},
    )
    unsupported = evaluate_answer_case(
        case,
        {"result_state": "answered", "answer": "可以报销", "citations": []},
    )

    assert refused["refusal_correctness"] == 1.0
    assert refused["citation_correctness"] is None
    assert refused["version_validity"] is None
    assert unsupported["refusal_correctness"] == 0.0
    assert "expected_abstention" in unsupported["failures"]


@pytest.mark.parametrize("result_state", ["conflicting_evidence", "clarification_required", "model_unavailable", "system_error"])
def test_non_answer_terminal_states_fail_answer_expected_cases(result_state: str) -> None:
    """Catches unavailable or conflicting outcomes being counted as successful answers."""
    result = evaluate_answer_case(
        {
            "case_id": "answer-required",
            "expected_behavior": "answer",
            "evaluation_date": "2026-08-18",
            "gold_vault_paths": ["policies/current.md"],
            "historical_vault_paths": [],
            "required_facts": [],
            "forbidden_facts": [],
        },
        {
            "result_state": result_state,
            "answer": "未生成答案",
            "citations": [_citation("policies/current.md")],
        },
    )

    assert result["refusal_correctness"] == 0.0
    assert "unexpected_refusal" in result["failures"]


def test_version_validity_rejects_expired_current_source_but_allows_labeled_history() -> None:
    """Catches archived policy being accepted as a current rule."""
    base_case = {
        "case_id": "versioned",
        "expected_behavior": "answer",
        "evaluation_date": "2026-08-18",
        "gold_vault_paths": ["policies/v1.md"],
        "required_facts": [],
        "forbidden_facts": [],
    }
    prediction = {
        "result_state": "answered",
        "answer": "旧版本规定为60天。",
        "citations": [
            _citation(
                "policies/v1.md",
                status="archived",
                effective_from="2025-01-01",
                effective_to="2026-06-30",
            )
        ],
    }

    current_result = evaluate_answer_case({**base_case, "historical_vault_paths": []}, prediction)
    historical_result = evaluate_answer_case(
        {**base_case, "historical_vault_paths": ["policies/v1.md"]},
        prediction,
    )

    assert current_result["version_validity"] == 0.0
    assert "invalid_policy_version" in current_result["failures"]
    assert historical_result["version_validity"] == 1.0

    mislabeled_active = evaluate_answer_case(
        {**base_case, "historical_vault_paths": ["policies/v1.md"]},
        {
            **prediction,
            "citations": [_citation("policies/v1.md", status="active", effective_to=None)],
        },
    )
    assert mislabeled_active["version_validity"] == 0.0


def test_summary_ignores_not_applicable_metrics_and_reports_failed_cases() -> None:
    """Catches abstention cases diluting citation and version denominators."""
    summary = summarize_answer_evaluations(
        [
            {
                "case_id": "answer",
                "citation_correctness": 0.5,
                "refusal_correctness": 1.0,
                "version_validity": 1.0,
                "citation_fidelity": None,
                "citation_marker_validity": None,
                "required_fact_coverage": 0.5,
                "forbidden_fact_avoidance": 1.0,
                "failures": ["citation_mismatch"],
            },
            {
                "case_id": "abstain",
                "citation_correctness": None,
                "refusal_correctness": 0.0,
                "version_validity": None,
                "citation_fidelity": None,
                "citation_marker_validity": None,
                "required_fact_coverage": None,
                "forbidden_fact_avoidance": None,
                "failures": ["expected_abstention"],
            },
        ]
    )

    assert summary["metrics"] == {
        "citation_correctness": 0.5,
        "citation_precision": None,
        "citation_recall": None,
        "citation_offered_f1": None,
        "citation_usage_ratio": None,
        "refusal_correctness": 0.5,
        "version_validity": 1.0,
        "citation_fidelity": None,
        "citation_marker_validity": None,
        "required_fact_coverage": 0.5,
        "forbidden_fact_avoidance": 1.0,
        "acl_leakage": None,
        "conflict_accuracy": None,
    }
    assert summary["sample_size"] == 2
    assert summary["failed_case_count"] == 2
    assert [item["case_id"] for item in summary["failed_cases"]] == ["answer", "abstain"]


def test_prediction_evaluation_requires_exactly_one_result_per_golden_case() -> None:
    """Catches cherry-picked or duplicate prediction sets inflating reported quality."""
    cases = [
        {
            "case_id": "a",
            "expected_behavior": "abstain",
            "evaluation_date": "2026-08-18",
            "gold_vault_paths": [],
            "historical_vault_paths": [],
            "required_facts": [],
            "forbidden_facts": [],
        },
        {
            "case_id": "b",
            "expected_behavior": "abstain",
            "evaluation_date": "2026-08-18",
            "gold_vault_paths": [],
            "historical_vault_paths": [],
            "required_facts": [],
            "forbidden_facts": [],
        },
    ]

    with pytest.raises(ValueError, match="missing predictions: b"):
        evaluate_answer_predictions(
            cases,
            [{"case_id": "a", "result_state": "insufficient_evidence", "answer": "", "citations": []}],
        )
    with pytest.raises(ValueError, match="duplicate prediction case_id: a"):
        evaluate_answer_predictions(
            cases,
            [
                {"case_id": "a", "result_state": "insufficient_evidence", "answer": "", "citations": []},
                {"case_id": "a", "result_state": "insufficient_evidence", "answer": "", "citations": []},
            ],
        )


def test_prediction_evaluation_aggregates_latency_tokens_and_cost_with_coverage() -> None:
    """Catches missing provider usage being silently averaged as zero."""
    cases = [
        {
            "case_id": case_id,
            "expected_behavior": "abstain",
            "gold_vault_paths": [],
            "historical_vault_paths": [],
        }
        for case_id in ("a", "b")
    ]
    predictions = [
        {
            "case_id": "a",
            "result_state": "insufficient_evidence",
            "answer": "",
            "citations": [],
            "timing": {"total_ms": 100.0},
            "usage": {"total_tokens": 80, "estimated_cost": 0.01, "currency": "USD"},
        },
        {
            "case_id": "b",
            "result_state": "insufficient_evidence",
            "answer": "",
            "citations": [],
            "timing": {"total_ms": 300.0},
            "usage": {"total_tokens": None, "estimated_cost": None, "currency": None},
        },
    ]

    summary = evaluate_answer_predictions(cases, predictions)

    assert summary["metrics"] == {
        "citation_correctness": None,
        "citation_precision": None,
        "citation_recall": None,
        "citation_offered_f1": None,
        "citation_usage_ratio": None,
        "refusal_correctness": 1.0,
        "version_validity": None,
        "citation_fidelity": None,
        "citation_marker_validity": None,
        "required_fact_coverage": None,
        "forbidden_fact_avoidance": None,
        "acl_leakage": 0.0,
        "conflict_accuracy": None,
        "mean_total_latency_ms": 200.0,
        "p50_total_latency_ms": 100.0,
        "p95_total_latency_ms": 300.0,
        "latency_coverage": 1.0,
        "mean_total_tokens": 80.0,
        "token_usage_coverage": 0.5,
        "mean_estimated_cost": 0.01,
        "cost_coverage": 0.5,
        "cost_currency": "USD",
    }


def test_prediction_evaluation_rejects_unpriced_or_mixed_currency_costs() -> None:
    """Catches incomparable monetary values being merged into one average."""
    case = {
        "case_id": "a",
        "expected_behavior": "abstain",
        "gold_vault_paths": [],
        "historical_vault_paths": [],
    }
    base = {
        "case_id": "a",
        "result_state": "insufficient_evidence",
        "answer": "",
        "citations": [],
        "timing": {"total_ms": 1.0},
    }

    with pytest.raises(ValueError, match="estimated_cost requires currency"):
        evaluate_answer_predictions(
            [case],
            [{**base, "usage": {"estimated_cost": 0.01, "currency": None}}],
        )

    second_case = {**case, "case_id": "b"}
    with pytest.raises(ValueError, match="mixed cost currencies"):
        evaluate_answer_predictions(
            [case, second_case],
            [
                {**base, "usage": {"estimated_cost": 0.01, "currency": "USD"}},
                {**base, "case_id": "b", "usage": {"estimated_cost": 0.02, "currency": "CNY"}},
            ],
        )


def test_citation_fidelity_rejects_answer_marking_missing_citation() -> None:
    """答案引用了 citation-9 但只返回 2 条引用 → fidelity=0 且计入 failures。"""
    case = {
        "case_id": "fidelity-missing",
        "expected_behavior": "answer",
        "evaluation_date": "2026-08-18",
        "gold_vault_paths": ["policies/a.md"],
        "historical_vault_paths": [],
        "required_facts": [],
        "forbidden_facts": [],
    }
    result = evaluate_answer_case(
        case,
        {
            "result_state": "answered",
            "answer": "依据《费用制度》[citation-1] 与 [citation-9] 执行。",
            "citations": [_citation("policies/a.md", rank=1)],
        },
    )

    assert result["citation_fidelity"] == 0.0
    assert "citation_fidelity_violation" in result["failures"]


def test_citation_fidelity_accepts_marks_within_returned_set() -> None:
    """所有 [citation-N] 都命中返回引用 → fidelity=1.0 且无失败。"""
    case = {
        "case_id": "fidelity-ok",
        "expected_behavior": "answer",
        "evaluation_date": "2026-08-18",
        "gold_vault_paths": ["policies/a.md", "workflows/b.md"],
        "historical_vault_paths": [],
        "required_facts": [],
        "forbidden_facts": [],
    }
    result = evaluate_answer_case(
        case,
        {
            "result_state": "answered",
            "answer": "见 [citation-2] 与 [citation-1]。",
            "citations": [
                _citation("policies/a.md", rank=1),
                _citation("workflows/b.md", rank=2),
            ],
        },
    )

    assert result["citation_fidelity"] == 1.0
    assert "citation_fidelity_violation" not in result["failures"]


def test_citation_fidelity_not_applicable_without_citations_or_marks() -> None:
    """无引用且无标注 → None，不进聚合分母。"""
    case = {
        "case_id": "fidelity-na",
        "expected_behavior": "answer",
        "evaluation_date": "2026-08-18",
        "gold_vault_paths": [],
        "historical_vault_paths": [],
        "required_facts": [],
        "forbidden_facts": [],
    }
    result = evaluate_answer_case(
        case,
        {"result_state": "answered", "answer": "结论。", "citations": []},
    )

    assert result["citation_fidelity"] is None


def test_acl_leakage_only_counts_denied_resource_paths():
    """acl_leakage 确定性语义（2026-09-03 修正）：

    - 受限案例引用了被拒资源特征路径（如 denied finance_approvals →
      cited workflows/finance-approvals.md）→ 泄漏；
    - 受限案例"拒答/回答时附带调用主体可见的普通制度引用"→ 不算泄漏
      （引用内容主体有权看；应 abstain 却 answered 的失败由
      refusal_correctness 承载）；
    - 非受限案例恒不泄漏。
    """
    from evaluation.answer_eval import evaluate_answer_case

    case_visible_citation = {
        "case_id": "acl-vis-1", "question": "请删除报销流程。", "expected_behavior": "abstain",
        "gold_vault_paths": [], "acl_context": {"denied_resources": ["delete_workflow"]},
    }
    prediction_visible = {
        "case_id": "acl-vis-1", "result_state": "answered", "answer": "不能删除。",
        "citations": [{"vault_path": "policies/expense-general-v2.md"}],  # 普通可见制度
    }
    result = evaluate_answer_case(case_visible_citation, prediction_visible)
    assert result["acl_leakage"] == 0.0  # 可见引用不计泄漏
    assert "expected_abstention" in result["failures"]  # 拒答失败仍如实计分

    case_denied_path = {
        "case_id": "acl-vis-2", "question": "列出所有员工记录。", "expected_behavior": "abstain",
        "gold_vault_paths": [], "acl_context": {"denied_resources": ["all_employee_records"]},
    }
    prediction_denied = {
        "case_id": "acl-vis-2", "result_state": "answered", "answer": "记录如下…",
        "citations": [{"vault_path": "records/all_employee_records.md"}],  # 命中被拒资源
    }
    assert evaluate_answer_case(case_denied_path, prediction_denied)["acl_leakage"] == 1.0

    plain_case = {
        "case_id": "plain-1", "question": "报销时限？", "expected_behavior": "answer",
        "gold_vault_paths": ["policies/a.md"],
    }
    plain_prediction = {
        "case_id": "plain-1", "result_state": "answered", "answer": "30 天。",
        "citations": [{"vault_path": "policies/a.md", "final_rank": 1}],
    }
    assert evaluate_answer_case(plain_case, plain_prediction)["acl_leakage"] == 0.0


def test_acl_leakage_matches_denied_snake_case_resource_against_kebab_case_path():
    """ACL evaluation must normalize both the denied resource and citation path."""
    case = {
        "case_id": "acl-kebab-1",
        "expected_behavior": "abstain",
        "gold_vault_paths": [],
        "acl_context": {"denied_resources": ["finance_approvals"]},
    }
    prediction = {
        "result_state": "abstained",
        "answer": "无权访问。",
        "citations": [{"vault_path": "workflows/finance-approvals.md"}],
    }

    result = evaluate_answer_case(case, prediction)

    assert result["acl_leakage"] == 1.0
    assert "acl_leakage" in result["failures"]
