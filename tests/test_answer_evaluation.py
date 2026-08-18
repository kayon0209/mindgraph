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
) -> dict:
    return {
        "vault_path": vault_path,
        "policy_status": status,
        "effective_from": effective_from,
        "effective_to": effective_to,
    }


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
            "answer": "应在30个自然日内提交。",
            "citations": [_citation("policies/current.md", effective_from="2026-07-01")],
        },
    )

    assert result == {
        "case_id": "current-policy",
        "citation_correctness": 1.0,
        "refusal_correctness": 1.0,
        "version_validity": 1.0,
        "required_fact_coverage": 1.0,
        "forbidden_fact_avoidance": 1.0,
        "failures": [],
    }


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
            "answer": "结论",
            "citations": [_citation("policies/a.md"), _citation("policies/unrelated.md")],
        },
    )

    assert result["citation_correctness"] == 0.5
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
                "required_fact_coverage": 0.5,
                "forbidden_fact_avoidance": 1.0,
                "failures": ["citation_mismatch"],
            },
            {
                "case_id": "abstain",
                "citation_correctness": None,
                "refusal_correctness": 0.0,
                "version_validity": None,
                "required_fact_coverage": None,
                "forbidden_fact_avoidance": None,
                "failures": ["expected_abstention"],
            },
        ]
    )

    assert summary["metrics"] == {
        "citation_correctness": 0.5,
        "refusal_correctness": 0.5,
        "version_validity": 1.0,
        "required_fact_coverage": 0.5,
        "forbidden_fact_avoidance": 1.0,
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
