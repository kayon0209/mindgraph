from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UsageSource(str, Enum):
    provider_reported = "provider_reported"
    locally_estimated = "locally_estimated"
    unavailable = "unavailable"


class ResultState(str, Enum):
    answered = "answered"
    insufficient_evidence = "insufficient_evidence"
    out_of_scope = "out_of_scope"
    model_unavailable = "model_unavailable"
    retrieval_unavailable = "retrieval_unavailable"
    system_error = "system_error"


class Citation(BaseModel):
    citation_id: str
    document_id: str
    document_name: str
    chunk_id: str
    section_path: str | None = None
    excerpt: str
    final_rank: int
    retrieval_score: float | None = None
    reranker_score: float | None = None
    document_version: str | None = None
    owner: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    policy_status: str | None = None
    authority_level: str | None = None
    knowledge_category: str | None = None
    authority_adjustment: float = 0.0
    vault_path: str | None = None


class RetrievalTraceModel(BaseModel):
    requested_strategy: str
    actual_strategy: str
    candidate_counts: dict[str, int] = Field(default_factory=dict)
    dense_results: list[dict[str, Any]] = Field(default_factory=list)
    sparse_results: list[dict[str, Any]] = Field(default_factory=list)
    fusion_results: list[dict[str, Any]] = Field(default_factory=list)
    reranked_results: list[dict[str, Any]] = Field(default_factory=list)
    final_chunks: list[dict[str, Any]] = Field(default_factory=list)
    stage_latency_ms: dict[str, float] = Field(default_factory=dict)
    degraded: bool = False
    degradation_reason: str | None = None
    index_version: str | None = None
    applied_filters: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    graph_enabled: bool = False
    graph_links: list[dict[str, Any]] = Field(default_factory=list)


class UsageMetrics(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost: float | None = None
    currency: str | None = None
    usage_source: UsageSource = UsageSource.unavailable


class TimingMetrics(BaseModel):
    embedding_ms: float | None = None
    dense_retrieval_ms: float | None = None
    sparse_retrieval_ms: float | None = None
    fusion_ms: float | None = None
    rerank_ms: float | None = None
    generation_ms: float | None = None
    ttft_ms: float | None = None
    total_ms: float


class AnswerResult(BaseModel):
    request_id: str
    question: str
    answer: str
    result_state: ResultState
    citations: list[Citation] = Field(default_factory=list)
    retrieval_trace: RetrievalTraceModel | None = None
    usage: UsageMetrics = Field(default_factory=UsageMetrics)
    timing: TimingMetrics
    requested_strategy: str
    actual_strategy: str
    degraded: bool = False
    degradation_reason: str | None = None
    model: str
    requested_provider: str | None = None
    actual_provider: str | None = None
    index_version: str | None = None
    prompt_version: str = "expense-policy-v1"
    created_at: datetime = Field(default_factory=utc_now)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    retrieval_strategy: Literal["dense", "bm25", "hybrid", "hybrid_rerank"] = "hybrid"
    chat_model: str | None = None
    chat_provider: str | None = None
    final_top_k: int = Field(default=5, ge=1, le=10)
    include_retrieval_trace: bool = True
    conversation_id: str | None = Field(default=None, max_length=100)
    query_date: str | None = None
    knowledge_categories: list[str] = Field(default_factory=list, max_length=10)
    include_historical: bool = False
    graph_enabled: bool = True

    @model_validator(mode="after")
    def strip_question(self):
        self.question = self.question.strip()
        if not self.question:
            raise ValueError("question must not be blank")
        return self


class FeedbackRecord(BaseModel):
    feedback_id: str
    request_id: str
    rating: Literal["helpful", "not_helpful"]
    reason_codes: list[str] = Field(default_factory=list)
    comment: str | None = Field(default=None, max_length=2000)
    created_at: datetime = Field(default_factory=utc_now)


class FeedbackCreate(BaseModel):
    request_id: str
    rating: Literal["helpful", "not_helpful"]
    reason_codes: list[str] = Field(default_factory=list)
    comment: str | None = Field(default=None, max_length=2000)


class BadCase(BaseModel):
    bad_case_id: str
    request_id: str
    question: str | None = None
    answer: str | None = None
    retrieved_chunks: list[dict[str, Any]] = Field(default_factory=list)
    error_category: str = "unclassified"
    status: str = "new"
    reviewer_note: str | None = None
    resolution: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class BadCaseUpdate(BaseModel):
    error_category: Literal["knowledge_gap", "chunking_error", "retrieval_error", "rerank_error", "generation_error", "citation_error", "false_reject", "missed_reject", "provider_error", "system_error", "unclassified"] | None = None
    status: Literal["new", "triaged", "in_progress", "resolved", "wont_fix"] | None = None
    reviewer_note: str | None = Field(default=None, max_length=4000)
    resolution: str | None = Field(default=None, max_length=4000)


class EvaluationRun(BaseModel):
    run_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled", "interrupted"]
    dataset_name: str
    dataset_version: str
    retrieval_strategy: str
    chat_model: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    configuration: dict[str, Any] = Field(default_factory=dict)
    summary_metrics: dict[str, Any] = Field(default_factory=dict)
    category_metrics: dict[str, Any] = Field(default_factory=dict)
    failed_cases: list[dict[str, Any]] = Field(default_factory=list)
    result_files: list[str] = Field(default_factory=list)
    progress_messages: list[str] = Field(default_factory=list)
    error: str | None = None
    index_version: str | None = None
    prompt_version: str | None = None
    provider: str | None = None


class EvaluationRunCreate(BaseModel):
    dataset_name: str = "expense_qa_v1"
    retrieval_strategies: list[Literal["dense", "bm25", "hybrid", "hybrid_rerank"]] = Field(default_factory=lambda: ["hybrid"])
    chat_model: str | None = None
    repetitions: int = Field(default=1, ge=1, le=5)
    warmups: int = Field(default=1, ge=0, le=3)
    evaluate_generation: bool = False
    prompt_version: str = "expense-policy-v1"
    chat_provider: str | None = None


class DocumentRecord(BaseModel):
    document_id: str
    document_name: str
    knowledge_category: str
    version: str
    chunk_count: int
    index_version: str | None = None
    index_status: str
    embedding_model: str | None = None
    uploaded_at: datetime
    last_indexed_at: datetime | None = None
    error: str | None = None
    pending_reindex: bool = False


class IndexStatus(BaseModel):
    index_version: str | None
    status: str
    embedding_model: str | None
    vector_dimension: int | None
    chunk_count: int
    created_at: str | None
    pending_changes: bool
    error: str | None = None


class ProviderCapability(BaseModel):
    provider: str
    model: str
    configured: bool
    verified: bool
    streaming_support: bool
    usage_support: bool
    health_status: str
    last_health_check: datetime | None = None
    pricing_metadata_available: bool = False


class ParsedElement(BaseModel):
    element_type: Literal["heading", "paragraph", "list_item", "numbered_clause", "table", "page_break", "section", "metadata"]
    text: str
    order: int
    page_number: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    clause_number: str | None = None
    table_id: str | None = None
    table_rows: list[list[str]] | None = None
    source_ref: str | None = None
    ocr_derived: bool = False


class ParsedDocument(BaseModel):
    document_id: str
    document_name: str
    file_type: str
    checksum: str
    parser_name: str
    parser_version: str
    elements: list[ParsedElement]
    warnings: list[str] = Field(default_factory=list)
    ocr_required_pages: list[int] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StructuredChunk(BaseModel):
    child_chunk_id: str
    parent_chunk_id: str
    document_id: str
    text: str
    parent_text: str
    heading_path: list[str] = Field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None
    clause_numbers: list[str] = Field(default_factory=list)
    table_ids: list[str] = Field(default_factory=list)
    checksum: str


class DocumentVersionModel(BaseModel):
    document_id: str
    logical_document_id: str
    version: str
    title: str
    file_type: str
    knowledge_category: str
    authority_level: Literal["official_policy", "official_guideline", "approved_faq", "user_uploaded_reference", "external_reference"]
    effective_date: str | None = None
    expiration_date: str | None = None
    status: Literal["draft", "pending_index", "active", "expired", "replaced", "deleted", "parse_failed", "index_failed"]
    checksum: str
    supersedes_version: str | None = None
    parsing_diagnostics: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    indexed_at: datetime | None = None
    created_by: str | None = None
