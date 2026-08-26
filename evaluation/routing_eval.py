"""Deterministic evaluation for the adaptive retrieval routing contract."""

from __future__ import annotations

from collections import Counter
from statistics import fmean
from typing import Any

from application.adaptive_retrieval_router import AdaptiveRetrievalRouter


REQUIRED_FIELDS = (
    "case_id",
    "question",
    "expected_route",
    "expected_strategy",
    "expected_graph_enabled",
)


class RoutingEvaluationError(ValueError):
    pass


def _validate_cases(cases: list[dict[str, Any]]) -> None:
    if not cases:
        raise RoutingEvaluationError("routing dataset must contain at least one case")
    seen: set[str] = set()
    for case in cases:
        for field in REQUIRED_FIELDS:
            if field not in case:
                raise RoutingEvaluationError(f"routing case is missing {field}")
        case_id = str(case["case_id"])
        if case_id in seen:
            raise RoutingEvaluationError(f"duplicate case_id: {case_id}")
        seen.add(case_id)


def evaluate_routing_cases(
    cases: list[dict[str, Any]],
    router: AdaptiveRetrievalRouter,
) -> dict[str, Any]:
    _validate_cases(cases)
    results = []
    distribution: Counter[str] = Counter()
    for case in cases:
        decision = router.decide(
            str(case["question"]),
            requested_strategy="auto",
            graph_allowed=bool(case.get("graph_allowed", True)),
        )
        distribution[decision.route] += 1
        failures = []
        route_correct = decision.route == case["expected_route"]
        strategy_correct = decision.selected_strategy == case["expected_strategy"]
        graph_correct = decision.graph_enabled is case["expected_graph_enabled"]
        if not route_correct:
            failures.append("route_mismatch")
        if not strategy_correct:
            failures.append("strategy_mismatch")
        if not graph_correct:
            failures.append("graph_policy_mismatch")
        results.append(
            {
                "case_id": str(case["case_id"]),
                "route_correct": route_correct,
                "strategy_correct": strategy_correct,
                "graph_policy_correct": graph_correct,
                "actual": decision.to_dict(),
                "failures": failures,
                "route": decision.route,
                "selected_strategy": decision.selected_strategy,
                "graph_enabled": decision.graph_enabled,
                "reasons": list(decision.reasons),
                "route_group": _route_group(decision.route),
            }
        )

    sample_size = len(results)
    metric = lambda key: fmean(float(item[key]) for item in results) if results else 0.0
    failed_cases = [item for item in results if item["failures"]]
    grouped = _group_metrics(results)
    return {
        "metrics": {
            "route_accuracy": metric("route_correct"),
            "strategy_accuracy": metric("strategy_correct"),
            "graph_policy_accuracy": metric("graph_policy_correct"),
            "rerank_route_rate": sum(item["selected_strategy"] == "hybrid_rerank" for item in results) / sample_size if sample_size else 0.0,
            "graph_route_rate": sum(bool(item["graph_enabled"]) for item in results) / sample_size if sample_size else 0.0,
            "bm25_route_rate": sum(item["selected_strategy"] == "bm25" for item in results) / sample_size if sample_size else 0.0,
        },
        "sample_size": sample_size,
        "failed_case_count": len(failed_cases),
        "failed_cases": failed_cases,
        "route_distribution": dict(sorted(distribution.items())),
        "group_metrics": grouped,
    }


def _route_group(route: str) -> str:
    if route == "exact_title":
        return "title"
    if route == "clarification_required":
        return "clarification"
    if route == "exception_or_conflict":
        return "exception"
    if route == "cross_policy":
        return "cross_policy"
    if route == "structured_fallback":
        return "structured"
    return "factual"


def _group_metrics(results: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        groups.setdefault(item["route_group"], []).append(item)
    summary: dict[str, dict[str, float]] = {}
    for name, rows in sorted(groups.items()):
        summary[name] = {
            "sample_size": float(len(rows)),
            "route_accuracy": fmean(float(row["route_correct"]) for row in rows),
            "strategy_accuracy": fmean(float(row["strategy_correct"]) for row in rows),
            "graph_policy_accuracy": fmean(float(row["graph_policy_correct"]) for row in rows),
        }
    return summary
