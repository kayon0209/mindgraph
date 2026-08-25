from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str
    document_id: str
    chunk_index: int
    section_path: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalCandidate:
    chunk: Chunk
    dense_score: float | None = None
    dense_rank: int | None = None
    sparse_score: float | None = None
    sparse_rank: int | None = None
    rrf_score: float | None = None
    fused_rank: int | None = None
    reranker_score: float | None = None
    final_rank: int | None = None
    original_score: float | None = None
    authority_adjustment: float = 0.0
    adjusted_score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class GovernancePrefilterResult:
    allowed_chunk_ids: frozenset[str]
    corpus_count: int
    eligible_count: int
    excluded_reason_counts: dict[str, int]
    as_of: str
    mode: str

    def trace_dict(self) -> dict[str, Any]:
        return {
            "corpus_count": self.corpus_count,
            "eligible_count": self.eligible_count,
            "excluded_reason_counts": dict(self.excluded_reason_counts),
            "as_of": self.as_of,
            "mode": self.mode,
        }


@dataclass
class RetrievalTrace:
    query: str
    requested_strategy: str
    actual_strategy: str
    degraded: bool = False
    degradation_reason: str | None = None
    candidate_counts: dict[str, int] = field(default_factory=dict)
    dense_results: list[RetrievalCandidate] = field(default_factory=list)
    sparse_results: list[RetrievalCandidate] = field(default_factory=list)
    fused_results: list[RetrievalCandidate] = field(default_factory=list)
    reranked_results: list[RetrievalCandidate] = field(default_factory=list)
    final_selected_chunks: list[RetrievalCandidate] = field(default_factory=list)
    latency_ms: dict[str, float] = field(default_factory=dict)
    index_version: str | None = None
    applied_filters: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    graph_enabled: bool = False
    graph_links: list[dict] = field(default_factory=list)
    route_decision: dict[str, Any] = field(default_factory=dict)
    query_variants: list[str] = field(default_factory=list)
    original_query: str | None = None
    governance_allowed_chunk_ids: frozenset[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "requested_strategy": self.requested_strategy,
            "actual_strategy": self.actual_strategy,
            "degraded": self.degraded,
            "degradation_reason": self.degradation_reason,
            "candidate_counts": self.candidate_counts,
            "dense_results": [item.to_dict() for item in self.dense_results],
            "sparse_results": [item.to_dict() for item in self.sparse_results],
            "fused_results": [item.to_dict() for item in self.fused_results],
            "reranked_results": [item.to_dict() for item in self.reranked_results],
            "final_selected_chunks": [item.to_dict() for item in self.final_selected_chunks],
            "latency_ms": self.latency_ms,
            "index_version": self.index_version,
            "applied_filters": self.applied_filters,
            "warnings": self.warnings,
            "route_decision": self.route_decision,
            "query_variants": self.query_variants,
            "original_query": self.original_query,
        }


class AccessPrefilterUnavailableError(ValueError):
    pass


class GovernancePrefilterUnavailableError(ValueError):
    pass


class EmbeddingProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def model_revision(self) -> str | None: ...

    @property
    def dimension(self) -> int: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class DenseRetriever(Protocol):
    @property
    def chunks(self) -> Sequence[Chunk] | None: ...

    def search(
        self,
        query: str,
        top_k: int,
        allowed_chunk_ids: set[str] | None = None,
    ) -> tuple[list[RetrievalCandidate], dict[str, float]]: ...


class SparseRetriever(Protocol):
    @property
    def chunks(self) -> Sequence[Chunk] | None: ...

    def search(
        self,
        query: str,
        top_k: int,
        allowed_chunk_ids: set[str] | None = None,
    ) -> tuple[list[RetrievalCandidate], dict[str, float]]: ...


class FusionStrategy(Protocol):
    def fuse(self, rankings: Sequence[Sequence[RetrievalCandidate]], top_k: int) -> list[RetrievalCandidate]: ...


class Reranker(Protocol):
    @property
    def model_name(self) -> str: ...

    def rerank(self, query: str, candidates: Sequence[RetrievalCandidate], top_k: int) -> list[RetrievalCandidate]: ...
