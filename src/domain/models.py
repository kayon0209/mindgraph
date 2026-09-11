from __future__ import annotations

from datetime import UTC, date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class UsageSource(str, Enum):
    provider_reported = "provider_reported"
    locally_estimated = "locally_estimated"
    unavailable = "unavailable"


class ResultState(str, Enum):
    answered = "answered"
    insufficient_evidence = "insufficient_evidence"
    permission_denied = "permission_denied"
    conflicting_evidence = "conflicting_evidence"
    out_of_scope = "out_of_scope"
    model_unavailable = "model_unavailable"
    retrieval_unavailable = "retrieval_unavailable"
    system_error = "system_error"


class ErrorCode(str, Enum):
    """机器可判定的错误/判定码（M0 契约基线）。

    取值与现有 REST/SSE/MCP 使用的 code 字符串一致，避免引入第二套命名；
    Assist 等 agent 面向通道以本枚举作为 `verdict` 的取值面。只做加法：
    新增值不影响既有客户端（客户端按名称 switch，未知值安全忽略）。
    """

    answered = "answered"
    out_of_scope = "out_of_scope"
    insufficient_evidence = "insufficient_evidence"
    permission_denied = "permission_denied"
    conflicting_evidence = "conflicting_evidence"
    model_unavailable = "model_unavailable"
    retrieval_unavailable = "retrieval_unavailable"
    system_error = "system_error"
    # ── 传输 / 生成层错误（SSE error 事件与 HTTP 错误面） ──
    aborted = "aborted"
    stream_error = "stream_error"
    provider_error = "provider_error"
    provider_unavailable = "provider_unavailable"
    quota_exhausted = "quota_exhausted"
    rate_limited = "rate_limited"
    authentication_failed = "authentication_failed"
    model_not_found = "model_not_found"
    invalid_request = "invalid_request"
    timeout = "timeout"


# ResultState → ErrorCode：取值一一对应，避免两套字符串漂移。
_RESULT_STATE_TO_ERROR_CODE: dict[ResultState, ErrorCode] = {
    ResultState.answered: ErrorCode.answered,
    ResultState.out_of_scope: ErrorCode.out_of_scope,
    ResultState.insufficient_evidence: ErrorCode.insufficient_evidence,
    ResultState.permission_denied: ErrorCode.permission_denied,
    ResultState.conflicting_evidence: ErrorCode.conflicting_evidence,
    ResultState.model_unavailable: ErrorCode.model_unavailable,
    ResultState.retrieval_unavailable: ErrorCode.retrieval_unavailable,
    ResultState.system_error: ErrorCode.system_error,
}


def error_code_for_result_state(state: ResultState) -> ErrorCode:
    """由终态推导机器可判定的错误码（全部终态均有一一对应值）。"""
    return _RESULT_STATE_TO_ERROR_CODE[state]


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
    policy_key: str | None = None


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
    graph_hops: int = Field(default=1, ge=1, le=2)
    graph_links: list[dict[str, Any]] = Field(default_factory=list)
    policy_conflicts: list[dict[str, Any]] = Field(default_factory=list)
    route_decision: dict[str, Any] = Field(default_factory=dict)
    query_variants: list[str] = Field(default_factory=list)
    original_query: str | None = None


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
    # M0 契约基线：机器可判定的错误码（由 result_state 自动推导，additive）
    error_code: ErrorCode | None = None
    # M0 契约基线：引用保真检查结果（回答中 [citation-N] 全部命中引用集为 True；
    # 无引用且无标注时为 None；M2 前仅提示不阻断，见 ADR-003）
    citation_fidelity: bool | None = None
    # P0 契约澄清：``citations`` 是「本次提供给模型的候选证据」（检索 top-k），
    # 不代表模型都用了；``cited_citation_ids`` 才是「答案正文实际标注引用的证据」，
    # 由 [citation-N] 标注与 citation_id 对应得出。评测的引用正确性以本字段为准。
    citations: list[Citation] = Field(default_factory=list)
    cited_citation_ids: list[str] = Field(default_factory=list)
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

    @model_validator(mode="after")
    def fill_error_code(self) -> AnswerResult:
        if self.error_code is None:
            self.error_code = error_code_for_result_state(self.result_state)
        return self


RetrievalStrategy = Literal["auto", "dense", "bm25", "hybrid", "hybrid_rerank"]


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    retrieval_strategy: RetrievalStrategy = "auto"
    chat_model: str | None = None
    chat_provider: str | None = None
    final_top_k: int = Field(default=5, ge=1, le=10)
    include_retrieval_trace: bool = True
    conversation_id: str | None = Field(default=None, max_length=100)
    query_date: str | None = None
    knowledge_categories: list[str] = Field(default_factory=list, max_length=10)
    include_historical: bool = False
    graph_enabled: bool = False
    # 调用方可显式声明问题类型以驱动路由（计划 3.3）：
    # "compound_question"/"clarification" 触发 clarification 路由与子问题拆解，
    # "versioned_policy" 触发版本过滤。多问号不自动触发——见
    # test_multiple_question_marks_do_not_force_clarification_without_missing_context。
    query_type: str | None = Field(default=None, max_length=40)
    # 计划 4.4：版本继承/条件/例外/冲突问题可配置最多 2 跳图扩展。
    graph_hops: int = Field(default=1, ge=1, le=2)
    # PR-12：服务端续问解析的结果（指代/槽位/纠错已展开的完整问句）。
    # 路由与检索用它；``question`` 保持原文用于落库与审计。None = 单轮语义。
    resolved_query: str | None = Field(default=None, max_length=4000)

    @field_validator("query_date")
    @classmethod
    def validate_query_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("query_date must be a valid ISO date (YYYY-MM-DD)") from exc
        return value

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


_DEFAULT_RETRIEVAL_STRATEGIES: list[Literal["dense", "bm25", "hybrid", "hybrid_rerank"]] = ["hybrid"]


class EvaluationRunCreate(BaseModel):
    dataset_name: str = "expense_qa_v1"
    retrieval_strategies: list[Literal["dense", "bm25", "hybrid", "hybrid_rerank"]] = Field(default_factory=lambda: list(_DEFAULT_RETRIEVAL_STRATEGIES))
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


ElementType = Literal["heading", "paragraph", "list_item", "numbered_clause", "table", "page_break", "section", "metadata"]
AuthorityLevel = Literal["official_policy", "official_guideline", "approved_faq", "user_uploaded_reference", "external_reference"]
DocumentStatus = Literal["draft", "pending_index", "active", "expired", "replaced", "deleted", "parse_failed", "index_failed"]


class ParsedElement(BaseModel):
    element_type: ElementType
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
    authority_level: AuthorityLevel
    effective_date: str | None = None
    expiration_date: str | None = None
    status: DocumentStatus
    checksum: str
    supersedes_version: str | None = None
    parsing_diagnostics: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    indexed_at: datetime | None = None
    created_by: str | None = None
    workspace: str | None = None
    department: str | None = None
    acl_json: str = "{}"
    acl_public: bool = False
