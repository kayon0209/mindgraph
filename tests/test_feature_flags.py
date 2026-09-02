"""M0-5：特性开关模式 + golden off 态测试。

守则：新增能力一律默认关闭；off 态下 REST/SSE/MCP/Chat 输出必须与基线
（冻结记录）字节兼容。本文件把 ChatService.stream 的既有事件序列固化为
golden 参考，任何未来改动若在默认配置下改变输出，会在此显式失败。
"""

from __future__ import annotations

from pathlib import Path

from application.chat_service import ChatService
from domain.contracts import SSE_ENVELOPE_KEYS, SSE_EVENT_NAMES, STREAM_MODE_VALUES
from domain.models import ChatRequest, ResultState
from infrastructure.database import ProductDatabase
from retrieval.types import Chunk, RetrievalCandidate, RetrievalTrace


class FakeProvider:
    provider_name = "fake"
    model_name = "fake-model"
    available = True

    def complete(self, _messages):
        return ("依据《差旅费报销管理办法》[citation-1]，报销应在 10 个工作日内提交。", {"total_tokens": 12})

    def stream(self, _messages):
        yield {"delta": "依据制度规定"}
        yield {"delta": "，10 个工作日内提交。[citation-1]"}
        yield {"usage": {"total_tokens": 12, "input_tokens": 6, "output_tokens": 6}}


class StubPipeline:
    """固定返回一条引用（final_rank=1）的检索管线。"""

    def __init__(self) -> None:
        chunk = Chunk(
            chunk_id="policy.md::0",
            text="报销应在 10 个工作日内提交。",
            document_id="policy.md",
            chunk_index=0,
            section_path="时限",
            metadata={
                "document_title": "差旅费报销管理办法",
                "title": "差旅费报销管理办法",
                "vault_path": "policies/travel.md",
                "document_version": "v2",
                "effective_from": "2026-01-01",
                "policy_key": "travel.meal",
                "policy_status": "active",
                "owner": "财务部",
                "ai_access_level": "official_policy",
            },
        )
        self.trace = RetrievalTrace(
            query="报销时限",
            requested_strategy="hybrid",
            actual_strategy="hybrid",
            candidate_counts={"dense": 1, "sparse": 1, "final": 1},
            final_selected_chunks=[RetrievalCandidate(chunk=chunk, final_rank=1, dense_score=0.9, rrf_score=0.8)],
            latency_ms={"query_embedding_ms": 1.0, "bm25_retrieval_ms": 1.0, "total_retrieval_ms": 2.0},
            index_version="test-index-1",
            applied_filters={"query_date": None, "knowledge_categories": []},
            warnings=["query_understanding:default:no_query_understanding_required"],
            graph_enabled=False,
        )

    def retrieve(
        self,
        query,
        strategy,
        query_date=None,
        categories=None,
        include_historical=False,
        graph_enabled=False,
        graph_hops=1,
        access_scope=None,
    ):
        self.trace.query = query
        return self.trace


def build_chat_service(tmp_path: Path, question: str) -> tuple[ChatService, ProductDatabase]:
    database = ProductDatabase(tmp_path / "flags.sqlite3")
    database.initialize()
    pipeline = StubPipeline()
    service = ChatService(
        database,
        lambda top_k: pipeline,
        FakeProvider(),
        privacy_log_questions=False,
    )
    return service, database


def _event_names(events) -> list[str]:
    return [item["event"] for item in events]


def test_golden_answer_stream_off_state_is_byte_compatible_with_baseline(tmp_path: Path):
    """默认配置（全部新 flag off）下，回答流的事件序列与冻结基线一致。"""
    service, database = build_chat_service(tmp_path, "报销时限是多少天？")
    events = list(service.stream(ChatRequest(question="报销时限是多少天？", retrieval_strategy="hybrid")))

    assert _event_names(events) == [
        "request_started",
        "scope_check_completed",
        "retrieval_routed",
        "retrieval_started",
        "retrieval_completed",
        "generation_started",
        "answer_delta",
        "answer_delta",
        "citations",
        "usage",
        "completed",
    ]
    # 契约面：事件名全部在冻结集合内；信封键严格一致；stream_mode 取值合法
    assert {item["event"] for item in events} <= set(SSE_EVENT_NAMES)
    for item in events:
        assert set(item.keys()) == set(SSE_ENVELOPE_KEYS)
    for item in events:
        if item["event"] == "answer_delta":
            assert item["data"]["stream_mode"] in STREAM_MODE_VALUES

    completed = events[-1]["data"]
    assert completed["result_state"] == "answered"
    # M0 契约扩展（additive）：机器可判定错误码 + 引用保真字段随 completed 暴露
    assert completed["error_code"] == "answered"
    assert completed["citation_fidelity"] is True


def test_golden_out_of_scope_stream_is_deterministic_and_unchanged(tmp_path: Path):
    service, _database = build_chat_service(tmp_path, "公司股票怎么买？")
    events = list(service.stream(ChatRequest(question="公司股票怎么买？", retrieval_strategy="hybrid")))

    assert _event_names(events) == [
        "request_started",
        "scope_check_completed",
        "answer_delta",
        "citations",
        "usage",
        "completed",
    ]
    completed = events[-1]["data"]
    assert completed["result_state"] == "out_of_scope"
    assert completed["error_code"] == "out_of_scope"
    assert completed["citation_fidelity"] is None  # 无引用且无标注 → 不可判定


def test_golden_sync_answer_exposes_error_code_and_fidelity(tmp_path: Path):
    service, _database = build_chat_service(tmp_path, "报销时限是多少天？")
    result = service.answer(ChatRequest(question="报销时限是多少天？", retrieval_strategy="hybrid"))

    assert result.result_state == ResultState.answered
    assert result.error_code == "answered"
    assert result.citation_fidelity is True
    assert "citation-1" in result.answer
