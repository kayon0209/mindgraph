import pytest

from application.adaptive_retrieval_router import AdaptiveRetrievalRouter
from application.chat_service import ChatService
from domain.models import ChatRequest
from infrastructure.database import ProductDatabase
from retrieval.types import Chunk, RetrievalCandidate, RetrievalTrace


@pytest.mark.parametrize(
    ("question", "route", "strategy", "graph_enabled", "reason"),
    [
        ("差旅费最晚多久提交？", "factual", "hybrid", False, "default_factual_query"),
        ("《费用报销管理办法》里如何定义业务招待？", "exact_title", "bm25", False, "explicit_document_title"),
        ("无发票费用有什么例外，和票据制度冲突时怎么办？", "exception_or_conflict", "hybrid_rerank", True, "exception_or_conflict_terms"),
        ("客户晚餐和差旅餐补能同时报销吗？", "cross_policy", "hybrid_rerank", True, "cross_policy_terms"),
        ("费用报销管理办法 V2 中，超过 5000 元需要谁审批？", "structured_fallback", "hybrid", False, "structured_clause_store_unavailable"),
    ],
)
def test_auto_router_uses_only_real_supported_retrieval_paths(
    question: str,
    route: str,
    strategy: str,
    graph_enabled: bool,
    reason: str,
) -> None:
    decision = AdaptiveRetrievalRouter().decide(
        question,
        requested_strategy="auto",
        graph_allowed=True,
    )

    assert decision.route == route
    assert decision.selected_strategy == strategy
    assert decision.graph_enabled is graph_enabled
    assert reason in decision.reasons


def test_manual_strategy_is_respected_and_never_claimed_as_adaptive() -> None:
    decision = AdaptiveRetrievalRouter().decide(
        "无发票例外怎么办？",
        requested_strategy="dense",
        graph_allowed=True,
    )

    assert decision.to_dict() == {
        "mode": "manual",
        "route": "manual",
        "requested_strategy": "dense",
        "selected_strategy": "dense",
        "graph_enabled": False,
        "reasons": ["user_selected_strategy"],
    }


def test_graph_policy_is_a_hard_upper_bound_for_auto_routing() -> None:
    decision = AdaptiveRetrievalRouter().decide(
        "新旧制度冲突时适用哪个例外？",
        requested_strategy="auto",
        graph_allowed=False,
    )

    assert decision.selected_strategy == "hybrid_rerank"
    assert decision.graph_enabled is False
    assert "graph_expansion_disabled_by_request" in decision.reasons


class _CapturingPipeline:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def retrieve(
        self,
        query,
        strategy,
        query_date=None,
        categories=None,
        include_historical=False,
        graph_enabled=None,
    ):
        self.calls.append({"strategy": strategy, "graph_enabled": graph_enabled})
        chunk = Chunk("policy::0", "三十日内提交。", "policy", 0, "时限", {"title": "费用制度"})
        return RetrievalTrace(
            query=query,
            requested_strategy=strategy,
            actual_strategy=strategy,
            final_selected_chunks=[RetrievalCandidate(chunk=chunk, final_rank=1)],
        )


class _Provider:
    available = True
    model_name = "test-model"
    provider_name = "test-provider"

    def complete(self, _messages):
        return "三十日内提交。[citation-1]", {}

    def stream(self, _messages):
        yield {"delta": "三十日内提交。[citation-1]"}


def test_chat_resolves_auto_before_retrieval_and_exposes_the_decision(tmp_path) -> None:
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    pipeline = _CapturingPipeline()
    service = ChatService(database, lambda _top_k: pipeline, _Provider())

    result = service.answer(ChatRequest(question="差旅费最晚多久提交？"))

    assert pipeline.calls == [{"strategy": "hybrid", "graph_enabled": False}]
    assert result.requested_strategy == "auto"
    assert result.actual_strategy == "hybrid"
    assert result.retrieval_trace is not None
    assert result.retrieval_trace.route_decision["route"] == "factual"
    assert result.retrieval_trace.route_decision["selected_strategy"] == "hybrid"
    assert result.retrieval_trace.stage_latency_ms["routing_ms"] >= 0


def test_stream_announces_route_before_retrieval_starts(tmp_path) -> None:
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    pipeline = _CapturingPipeline()
    service = ChatService(database, lambda _top_k: pipeline, _Provider())

    events = list(service.stream(ChatRequest(question="无发票例外怎么办？")))
    names = [item["event"] for item in events]
    routed = events[names.index("retrieval_routed")]["data"]

    assert names.index("retrieval_routed") < names.index("retrieval_started")
    assert routed["selected_strategy"] == "hybrid_rerank"
    assert routed["graph_enabled"] is True
    assert pipeline.calls == [{"strategy": "hybrid_rerank", "graph_enabled": True}]

