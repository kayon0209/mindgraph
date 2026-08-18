"""Deterministic answer-level trust evaluation for MindGraph Golden cases."""

from __future__ import annotations

from datetime import date
from statistics import fmean
from typing import Any


REFUSAL_STATES = {"insufficient_evidence", "out_of_scope"}
ANSWER_METRICS = (
    "citation_correctness",
    "refusal_correctness",
    "version_validity",
    "required_fact_coverage",
    "forbidden_fact_avoidance",
)
INACTIVE_POLICY_STATUSES = {"archived", "expired", "superseded", "replaced"}


def _citation_f1(expected_paths: set[str], actual_paths: set[str]) -> float:
    if not expected_paths or not actual_paths:
        return 0.0
    overlap = len(expected_paths & actual_paths)
    precision = overlap / len(actual_paths)
    recall = overlap / len(expected_paths)
    return 2 * precision * recall / (precision + recall) if overlap else 0.0


def _is_version_valid(citation: dict[str, Any], case: dict[str, Any]) -> bool:
    vault_path = citation.get("vault_path")
    status = str(citation.get("policy_status") or "").lower()
    effective_from = citation.get("effective_from")
    evaluation_date = case.get("evaluation_date")
    if not status or not effective_from or not evaluation_date:
        return False

    try:
        target = date.fromisoformat(evaluation_date)
        starts = date.fromisoformat(effective_from)
        ends = date.fromisoformat(citation["effective_to"]) if citation.get("effective_to") else None
    except (TypeError, ValueError):
        return False

    if vault_path in set(case.get("historical_vault_paths", [])):
        return status in INACTIVE_POLICY_STATUSES and ends is not None and starts <= ends < target
    if status in INACTIVE_POLICY_STATUSES:
        return False
    return status == "active" and starts <= target and (ends is None or target <= ends)


def _fact_coverage(answer: str, facts: list[str]) -> float:
    if not facts:
        return 1.0
    return sum(fact in answer for fact in facts) / len(facts)


def evaluate_answer_case(case: dict[str, Any], prediction: dict[str, Any]) -> dict[str, Any]:
    """Score one answer without using an LLM judge or runtime relation labels."""
    failures: list[str] = []
    expected_behavior = case["expected_behavior"]
    result_state = prediction.get("result_state")
    refused = result_state in REFUSAL_STATES
    refusal_correctness = float(refused == (expected_behavior == "abstain"))
    if not refusal_correctness:
        failures.append("expected_abstention" if expected_behavior == "abstain" else "unexpected_refusal")

    if expected_behavior == "abstain":
        return {
            "case_id": case["case_id"],
            "citation_correctness": None,
            "refusal_correctness": refusal_correctness,
            "version_validity": None,
            "required_fact_coverage": None,
            "forbidden_fact_avoidance": None,
            "failures": failures,
        }

    citations = prediction.get("citations") or []
    expected_paths = set(case.get("gold_vault_paths", []))
    actual_paths = {item.get("vault_path") for item in citations if item.get("vault_path")}
    citation_correctness = _citation_f1(expected_paths, actual_paths)
    if citation_correctness < 1:
        failures.append("citation_mismatch")

    version_validity = float(bool(citations) and all(_is_version_valid(item, case) for item in citations))
    if not version_validity:
        failures.append("invalid_policy_version")

    answer = str(prediction.get("answer") or "")
    required_fact_coverage = _fact_coverage(answer, case.get("required_facts", []))
    if required_fact_coverage < 1:
        failures.append("missing_required_fact")
    forbidden_fact_avoidance = float(not any(fact in answer for fact in case.get("forbidden_facts", [])))
    if not forbidden_fact_avoidance:
        failures.append("forbidden_fact_present")

    return {
        "case_id": case["case_id"],
        "citation_correctness": citation_correctness,
        "refusal_correctness": refusal_correctness,
        "version_validity": version_validity,
        "required_fact_coverage": required_fact_coverage,
        "forbidden_fact_avoidance": forbidden_fact_avoidance,
        "failures": failures,
    }


def evaluate_answer_predictions(cases: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate a complete prediction set without permitting cherry-picked cases."""
    predictions_by_id: dict[str, dict[str, Any]] = {}
    for prediction in predictions:
        case_id = prediction.get("case_id")
        if not case_id:
            raise ValueError("prediction is missing case_id")
        if case_id in predictions_by_id:
            raise ValueError(f"duplicate prediction case_id: {case_id}")
        predictions_by_id[case_id] = prediction

    case_ids = {case["case_id"] for case in cases}
    missing = sorted(case_ids - predictions_by_id.keys())
    unknown = sorted(predictions_by_id.keys() - case_ids)
    if missing:
        raise ValueError(f"missing predictions: {', '.join(missing)}")
    if unknown:
        raise ValueError(f"unknown prediction case_id: {', '.join(unknown)}")

    results = [evaluate_answer_case(case, predictions_by_id[case["case_id"]]) for case in cases]
    return summarize_answer_evaluations(results)


def summarize_answer_evaluations(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate applicable metrics while preserving auditable failed cases."""
    metrics = {}
    for name in ANSWER_METRICS:
        values = [float(item[name]) for item in results if item.get(name) is not None]
        metrics[name] = fmean(values) if values else None
    failed_cases = [item for item in results if item.get("failures")]
    return {
        "metrics": metrics,
        "sample_size": len(results),
        "failed_case_count": len(failed_cases),
        "failed_cases": failed_cases,
    }
