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


def evaluate_routing_cases(
    cases: list[dict[str, Any]],
    router: AdaptiveRetrievalRouter,
) -> dict[str, Any]:
    seen: set[str] = set()
    results = []
    distribution: Counter[str] = Counter()
    for case in cases:
        for field in REQUIRED_FIELDS:
            if field not in case:
                raise ValueError(f"routing case is missing {field}")
        case_id = str(case["case_id"])
        if case_id in seen:
            raise ValueError(f"duplicate case_id: {case_id}")
        seen.add(case_id)

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
                "case_id": case_id,
                "route_correct": route_correct,
                "strategy_correct": strategy_correct,
                "graph_policy_correct": graph_correct,
                "actual": decision.to_dict(),
                "failures": failures,
            }
        )

    sample_size = len(results)
    metric = lambda key: fmean(float(item[key]) for item in results) if results else 0.0
    failed_cases = [item for item in results if item["failures"]]
    return {
        "metrics": {
            "route_accuracy": metric("route_correct"),
            "strategy_accuracy": metric("strategy_correct"),
            "graph_policy_accuracy": metric("graph_policy_correct"),
            "rerank_route_rate": sum(item["actual"]["selected_strategy"] == "hybrid_rerank" for item in results) / sample_size if sample_size else 0.0,
            "graph_route_rate": sum(bool(item["actual"]["graph_enabled"]) for item in results) / sample_size if sample_size else 0.0,
            "bm25_route_rate": sum(item["actual"]["selected_strategy"] == "bm25" for item in results) / sample_size if sample_size else 0.0,
        },
        "sample_size": sample_size,
        "failed_case_count": len(failed_cases),
        "failed_cases": failed_cases,
        "route_distribution": dict(sorted(distribution.items())),
    }
