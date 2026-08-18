import pytest

from application.adaptive_retrieval_router import AdaptiveRetrievalRouter
from evaluation.routing_eval import evaluate_routing_cases


def test_routing_evaluation_reports_quality_and_cost_path_mix() -> None:
    cases = [
        {
            "case_id": "fact",
            "question": "差旅费多久提交？",
            "expected_route": "factual",
            "expected_strategy": "hybrid",
            "expected_graph_enabled": False,
        },
        {
            "case_id": "cross-policy",
            "question": "客户晚餐和差旅餐补能同时报销吗？",
            "expected_route": "cross_policy",
            "expected_strategy": "hybrid_rerank",
            "expected_graph_enabled": True,
        },
        {
            "case_id": "wrong-label",
            "question": "《费用制度》如何定义业务招待？",
            "expected_route": "factual",
            "expected_strategy": "hybrid",
            "expected_graph_enabled": False,
        },
    ]

    result = evaluate_routing_cases(cases, AdaptiveRetrievalRouter())

    assert result["metrics"] == {
        "route_accuracy": pytest.approx(2 / 3),
        "strategy_accuracy": pytest.approx(2 / 3),
        "graph_policy_accuracy": 1.0,
        "rerank_route_rate": pytest.approx(1 / 3),
        "graph_route_rate": pytest.approx(1 / 3),
        "bm25_route_rate": pytest.approx(1 / 3),
    }
    assert result["sample_size"] == 3
    assert result["failed_case_count"] == 1
    assert result["failed_cases"][0]["case_id"] == "wrong-label"
    assert result["route_distribution"] == {
        "cross_policy": 1,
        "exact_title": 1,
        "factual": 1,
    }


def test_routing_evaluation_rejects_duplicate_or_incomplete_cases() -> None:
    duplicate = {
        "case_id": "same",
        "question": "问题",
        "expected_route": "factual",
        "expected_strategy": "hybrid",
        "expected_graph_enabled": False,
    }
    with pytest.raises(ValueError, match="duplicate case_id"):
        evaluate_routing_cases([duplicate, duplicate], AdaptiveRetrievalRouter())
    with pytest.raises(ValueError, match="missing expected_strategy"):
        evaluate_routing_cases(
            [{"case_id": "missing", "question": "问题", "expected_route": "factual", "expected_graph_enabled": False}],
            AdaptiveRetrievalRouter(),
        )
