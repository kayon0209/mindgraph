from __future__ import annotations

import pytest

from evaluation.ablation_runner import evaluate_ablation, evaluate_graph_gate


def _row(name: str, r5: float, mrr: float = 0.5, latency: float = 10.0) -> dict[str, object]:
    return {
        "retrieval_strategy": name,
        "recall_at_1": 0.2,
        "recall_at_3": 0.4,
        "recall_at_5": r5,
        "mrr": mrr,
        "document_hit_rate": r5,
        "chunk_hit_rate": r5,
        "mean_retrieval_latency_ms": latency,
    }


def test_ablation_comparison_reports_graph_delta_without_claiming_significance():
    result = evaluate_ablation([_row("bm25_vector", 0.5), _row("bm25_vector_graph", 0.6)], graph_strategy="bm25_vector_graph", baseline_strategy="bm25_vector")

    assert result["baseline_strategy"] == "bm25_vector"
    assert result["graph_strategy"] == "bm25_vector_graph"
    assert result["deltas"]["recall_at_5"] == pytest.approx(0.1)
    assert result["decision"]["statistical_significance"] is False
    assert result["decision"]["default_route_recommendation"] == "conditional_only"


def test_graph_gate_requires_minimum_gain_and_no_regression():
    passing = evaluate_graph_gate({"recall_at_5": 0.6, "mrr": 0.6, "mean_retrieval_latency_ms": 20}, {"recall_at_5": 0.5, "mrr": 0.5, "mean_retrieval_latency_ms": 10}, min_recall_gain=0.05, max_latency_multiplier=3.0)
    assert passing["eligible"] is True
    assert passing["default_route_recommendation"] == "conditional_only"

    failing = evaluate_graph_gate({"recall_at_5": 0.51, "mrr": 0.5, "mean_retrieval_latency_ms": 100}, {"recall_at_5": 0.5, "mrr": 0.5, "mean_retrieval_latency_ms": 10}, min_recall_gain=0.05, max_latency_multiplier=3.0)
    assert failing["eligible"] is False
    assert "recall_gain_below_threshold" in failing["reasons"]
    assert "latency_regression" in failing["reasons"]


def test_graph_gate_rejects_missing_comparable_strategies():
    with pytest.raises(ValueError, match="strategy rows"):
        evaluate_ablation([_row("bm25_vector", 0.5)], graph_strategy="bm25_vector_graph", baseline_strategy="bm25_vector")
