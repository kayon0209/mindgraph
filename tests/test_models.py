"""API 域模型的单元测试。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from domain.models import ChatRequest, Citation, FeedbackCreate, ResultState, UsageMetrics


class TestChatRequest:
    def test_valid_chat_request(self):
        req = ChatRequest(question="差旅费怎么报销？")
        assert req.question == "差旅费怎么报销？"
        assert req.retrieval_strategy == "auto"
        assert req.final_top_k == 5

    def test_question_too_short(self):
        with pytest.raises(ValidationError):
            ChatRequest(question="")

    def test_question_too_long(self):
        with pytest.raises(ValidationError):
            ChatRequest(question="A" * 2001)

    def test_question_stripped(self):
        req = ChatRequest(question="  出差标准  ")
        assert req.question == "出差标准"

    def test_invalid_strategy(self):
        with pytest.raises(ValidationError):
            ChatRequest(question="test", retrieval_strategy="invalid_strategy")

    def test_top_k_out_of_range(self):
        with pytest.raises(ValidationError):
            ChatRequest(question="test", final_top_k=0)
        with pytest.raises(ValidationError):
            ChatRequest(question="test", final_top_k=11)

    def test_valid_top_k_boundaries(self):
        assert ChatRequest(question="test", final_top_k=1).final_top_k == 1
        assert ChatRequest(question="test", final_top_k=10).final_top_k == 10

    def test_query_date_must_be_an_iso_calendar_date(self):
        with pytest.raises(ValidationError):
            ChatRequest(question="test", query_date="2026-02-30")
        with pytest.raises(ValidationError):
            ChatRequest(question="test", query_date="08/18/2026")

        assert ChatRequest(question="test", query_date="2026-08-18").query_date == "2026-08-18"

    def test_defaults(self):
        req = ChatRequest(question="test")
        assert req.include_retrieval_trace is True
        assert req.knowledge_categories == []
        assert req.include_historical is False


class TestCitation:
    def test_citation_creation(self):
        cit = Citation(
            citation_id="citation-1",
            document_id="doc-1",
            document_name="policy.md",
            chunk_id="policy.md::0",
            excerpt="差旅费报销时限为10个工作日",
            final_rank=1,
        )
        assert cit.citation_id == "citation-1"
        assert cit.document_name == "policy.md"
        assert cit.final_rank == 1

    def test_optional_fields_default(self):
        cit = Citation(
            citation_id="c-1", document_id="d-1", document_name="d", chunk_id="c", excerpt="e", final_rank=1
        )
        assert cit.retrieval_score is None
        assert cit.reranker_score is None


class TestUsageMetrics:
    def test_default_usage(self):
        usage = UsageMetrics()
        assert usage.input_tokens is None
        assert usage.total_tokens is None

    def test_usage_with_tokens(self):
        usage = UsageMetrics(input_tokens=100, output_tokens=50, total_tokens=150)
        assert usage.total_tokens == 150


class TestFeedbackCreate:
    def test_valid_feedback(self):
        fb = FeedbackCreate(request_id="req-1", rating="helpful")
        assert fb.rating == "helpful"

    def test_invalid_rating(self):
        with pytest.raises(ValidationError):
            FeedbackCreate(request_id="req-1", rating="bad_rating")

    def test_reason_codes(self):
        fb = FeedbackCreate(request_id="req-1", rating="not_helpful", reason_codes=["irrelevant", "outdated"])
        assert len(fb.reason_codes) == 2

    def test_comment_max_length(self):
        with pytest.raises(ValidationError):
            FeedbackCreate(request_id="req-1", rating="helpful", comment="A" * 2001)
