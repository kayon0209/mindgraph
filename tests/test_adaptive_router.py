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
        ("费用报销管理办法 V2 中，超过 5000 元需要谁审批？", "structured_fallback", "hybrid", False, "structured_clause_query_selected"),
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

    payload = decision.to_dict()

    assert payload["mode"] == "manual"
    assert payload["route"] == "manual"
    assert payload["requested_strategy"] == "dense"
    assert payload["selected_strategy"] == "dense"
    assert payload["graph_enabled"] is False
    assert payload["reasons"] == ["user_selected_strategy"]


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
        access_scope=None,
    ):
        self.calls.append({
            "strategy": strategy,
            "graph_enabled": graph_enabled,
            "query_date": query_date,
            "access_scope": access_scope,
        })
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

    assert pipeline.calls == [{"strategy": "hybrid", "graph_enabled": False, "query_date": None, "access_scope": None}]
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
    assert routed["graph_enabled"] is False
    assert pipeline.calls == [{"strategy": "hybrid_rerank", "graph_enabled": False, "query_date": None, "access_scope": None}]


def test_exact_title_rewrite_preserves_the_requested_clause_semantics() -> None:
    decision = AdaptiveRetrievalRouter().decide(
        "《费用报销管理办法》中如何定义业务目的？",
        requested_strategy="auto",
        graph_allowed=False,
    )

    assert decision.route == "exact_title"
    assert "费用报销管理办法" in decision.search_query
    assert "业务目的" in decision.search_query


def test_multiple_question_marks_do_not_force_clarification_without_missing_context() -> None:
    decision = AdaptiveRetrievalRouter().decide(
        "报销需要发票吗？电子发票可以吗？",
        requested_strategy="auto",
        graph_allowed=False,
    )

    assert decision.route == "factual"
    assert decision.selected_strategy == "hybrid"


def test_version_question_infers_effective_date_and_applies_it_to_retrieval(tmp_path) -> None:
    question = "2026年7月1日之后西安出差住宿上限是多少？"
    decision = AdaptiveRetrievalRouter().decide(
        question,
        requested_strategy="auto",
        graph_allowed=False,
        query_type="versioned_policy",
    )

    assert decision.filters["effective_at"] == "2026-07-01"
    assert "version_constraint" in decision.reasons

    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    pipeline = _CapturingPipeline()
    service = ChatService(database, lambda _top_k: pipeline, _Provider())
    service.answer(ChatRequest(question=question))

    assert pipeline.calls == [{"strategy": "hybrid", "graph_enabled": False, "query_date": "2026-07-01", "access_scope": None}]


def test_empty_access_scope_is_forwarded_for_fail_closed_retrieval(tmp_path) -> None:
    database = ProductDatabase(tmp_path / "product.sqlite3")
    database.initialize()
    pipeline = _CapturingPipeline()
    service = ChatService(database, lambda _top_k: pipeline, _Provider())

    service.answer(ChatRequest(question="差旅费最晚多久提交？"), access_scope={})

    assert pipeline.calls[0]["access_scope"] == {}

