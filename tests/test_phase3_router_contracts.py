import pytest

from application.adaptive_retrieval_router import AdaptiveRetrievalRouter
from evaluation.routing_eval import evaluate_routing_cases


@pytest.mark.parametrize(
    ("question", "expected_route", "expected_strategy", "expected_graph_enabled"),
    [
        ("差旅费最晚多久提交？", "factual", "hybrid", False),
        ("《费用报销管理办法》中如何定义业务目的？", "exact_title", "bm25", False),
        ("无发票费用有什么例外流程？", "exception_or_conflict", "hybrid_rerank", True),
        ("客户晚餐和差旅餐补能同时报销吗？", "cross_policy", "hybrid_rerank", True),
        ("费用报销管理办法 V2 的提交时限是什么？", "structured_fallback", "hybrid", False),
    ],
)
def test_router_expected_paths_remain_deterministic(question, expected_route, expected_strategy, expected_graph_enabled):
    decision = AdaptiveRetrievalRouter().decide(question, requested_strategy="auto", graph_allowed=True)
    assert decision.route == expected_route
    assert decision.selected_strategy == expected_strategy
    assert decision.graph_enabled is expected_graph_enabled


def test_routing_metrics_are_reported_without_hiding_failed_cases():
    cases = [
        {"case_id": "ok", "question": "差旅费最晚多久提交？", "expected_route": "factual", "expected_strategy": "hybrid", "expected_graph_enabled": False},
        {"case_id": "bad", "question": "《费用报销管理办法》中如何定义业务目的？", "expected_route": "factual", "expected_strategy": "hybrid", "expected_graph_enabled": False},
    ]

    result = evaluate_routing_cases(cases, AdaptiveRetrievalRouter())

    assert result["sample_size"] == 2
    assert result["failed_case_count"] == 1
    assert result["failed_cases"][0]["case_id"] == "bad"
    assert result["route_distribution"]["exact_title"] == 1
    assert result["metrics"]["route_accuracy"] == pytest.approx(0.5)
