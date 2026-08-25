from __future__ import annotations

from datetime import date
import time

from .types import (
    AccessPrefilterUnavailableError,
    DenseRetriever,
    FusionStrategy,
    Reranker,
    RetrievalTrace,
    SparseRetriever,
)

VALID_STRATEGIES = {"dense", "bm25", "hybrid", "hybrid_rerank"}
DEFAULT_AUTHORITY_WEIGHTS = {
    "official_policy": 0.020,
    "official_guideline": 0.015,
    "approved_faq": 0.010,
    "user_uploaded_reference": 0.005,
    "external_reference": 0.0,
}


class PermissionDeniedError(ValueError):
    pass


class DuplicateChunkIdError(ValueError):
    pass


class RetrievalPipeline:
    def __init__(
        self,
        dense: DenseRetriever,
        sparse: SparseRetriever,
        fusion: FusionStrategy,
        reranker: Reranker | None = None,
        candidate_count: int = 20,
        rerank_top_n: int = 10,
        final_top_k: int = 5,
    ) -> None:
        self.dense, self.sparse, self.fusion, self.reranker = dense, sparse, fusion, reranker
        self.candidate_count = candidate_count
        self.rerank_top_n = rerank_top_n
        self.final_top_k = final_top_k

    @staticmethod
    def _base_score(candidate) -> float:
        for value in (candidate.reranker_score, candidate.rrf_score, candidate.dense_score, candidate.sparse_score):
            if value is not None:
                return float(value)
        return 0.0

    def _filter_and_adjust(self, candidates, query_date, categories, include_historical, trace):
        selected = []
        target_date = date.fromisoformat(query_date) if query_date else date.today()
        missing_date_metadata = False
        missing_status_metadata = False
        for candidate in candidates:
            metadata = candidate.chunk.metadata
            status = metadata.get("document_status")
            if status is None:
                missing_status_metadata = True
            elif not include_historical and status != "active":
                continue
            if categories and metadata.get("knowledge_category") not in categories:
                continue
            effective = metadata.get("effective_date")
            expiration = metadata.get("expiration_date")
            if effective and date.fromisoformat(effective) > target_date:
                continue
            if expiration and date.fromisoformat(expiration) < target_date:
                continue
            if query_date and not effective and not expiration:
                missing_date_metadata = True
            candidate.original_score = self._base_score(candidate)
            candidate.authority_adjustment = DEFAULT_AUTHORITY_WEIGHTS.get(metadata.get("authority_level", ""), 0.0)
            candidate.adjusted_score = candidate.original_score + candidate.authority_adjustment
            selected.append(candidate)
        if missing_status_metadata:
            trace.warnings.append("index_chunks_missing_document_status")
        if missing_date_metadata:
            trace.warnings.append("explicit_date_filter_has_incomplete_metadata")
        return sorted(selected, key=lambda item: (item.adjusted_score or 0.0, item.chunk.chunk_id), reverse=True)

    def _filter_by_access(self, candidates, access_scope: dict | None, trace: RetrievalTrace) -> list:
        """按当前主体的 ACL 范围裁剪候选。

        access_scope 形如：{"allow": [...], "deny": [...], "user": "...", "roles": [...]}
        - 无 access_scope（单用户 / demo / 旧版调用）→ 不裁剪；
        - 有 access_scope → 拒绝无 ACL 元数据的 chunk，仅保留显式命中的。
        """
        if access_scope is None:
            return candidates
        from application.access_control import chunk_acl_matches

        scope = self._normalized_access_scope(access_scope)
        allowed = set(scope["allow"])
        if "*" in allowed:
            return candidates
        visible = []
        for candidate in candidates:
            metadata = candidate.chunk.metadata
            if not chunk_acl_matches(metadata, scope):
                continue
            visible.append(candidate)
        if len(visible) < len(candidates):
            trace.warnings.append("access_denied_chunks_filtered")
            trace.warnings = list(dict.fromkeys(trace.warnings))
        return visible

    @staticmethod
    def _normalized_access_scope(access_scope: dict) -> dict:
        return {
            **access_scope,
            "allow": list(access_scope.get("allow") or []),
            "deny": list(access_scope.get("deny") or []),
            "roles": list(access_scope.get("roles") or []),
            "user": access_scope.get("user"),
        }

    @staticmethod
    def _chunk_map(chunks, retriever_name: str) -> dict:
        chunks_by_id = {}
        for chunk in chunks:
            if chunk.chunk_id in chunks_by_id:
                raise DuplicateChunkIdError(
                    f"{retriever_name} retrieval corpus contains duplicate chunk IDs"
                )
            chunks_by_id[chunk.chunk_id] = chunk
        return chunks_by_id

    def _loaded_chunks(self, require_complete_metadata: bool) -> list:
        dense_source = getattr(self.dense, "chunks", None)
        sparse_source = getattr(self.sparse, "chunks", None)
        if require_complete_metadata and (dense_source is None or sparse_source is None):
            raise AccessPrefilterUnavailableError(
                "Access prefilter requires complete corpus metadata before retrieval"
            )

        dense_by_id = self._chunk_map(list(dense_source or []), "Dense")
        sparse_by_id = self._chunk_map(list(sparse_source or []), "Sparse")
        for chunk_id in dense_by_id.keys() & sparse_by_id.keys():
            if dense_by_id[chunk_id] != sparse_by_id[chunk_id]:
                raise DuplicateChunkIdError(
                    "Cross-retriever chunk identity conflict prevents access prefilter"
                )

        chunks_by_id = dict(dense_by_id)
        for chunk_id, chunk in sparse_by_id.items():
            chunks_by_id.setdefault(chunk_id, chunk)
        return list(chunks_by_id.values())

    def _access_prefilter(self, access_scope: dict | None) -> tuple[set[str] | None, dict, dict | None]:
        chunks = self._loaded_chunks(require_complete_metadata=access_scope is not None)
        corpus_count = len(chunks)
        if access_scope is None:
            return None, {
                "reason": "explicit_bypass",
                "corpus_count": corpus_count,
                "allowed_count": corpus_count,
                "rejected_count": 0,
            }, None

        from application.access_control import chunk_acl_matches

        scope = self._normalized_access_scope(access_scope)
        if "*" in scope["allow"]:
            allowed_chunk_ids = {chunk.chunk_id for chunk in chunks}
            reason = "wildcard"
        else:
            allowed_chunk_ids = {
                chunk.chunk_id
                for chunk in chunks
                if chunk_acl_matches(chunk.metadata, scope)
            }
            reason = "scope_match" if scope["allow"] else "public_only"
        return allowed_chunk_ids, {
            "reason": reason,
            "corpus_count": corpus_count,
            "allowed_count": len(allowed_chunk_ids),
            "rejected_count": corpus_count - len(allowed_chunk_ids),
        }, scope

    @staticmethod
    def _prefilter_results(candidates, allowed_chunk_ids: set[str] | None):
        if allowed_chunk_ids is None:
            return candidates
        return [candidate for candidate in candidates if candidate.chunk.chunk_id in allowed_chunk_ids]

    def retrieve(self, query: str, strategy: str, query_date: str | None = None,
                 categories: list[str] | None = None, include_historical: bool = False,
                 access_scope: dict | None = None) -> RetrievalTrace:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown retrieval strategy: {strategy}")
        trace = RetrievalTrace(query=query, requested_strategy=strategy, actual_strategy=strategy)
        trace.index_version = getattr(self.dense, "metadata", {}).get("index_version")
        allowed_chunk_ids, access_prefilter, normalized_scope = self._access_prefilter(access_scope)
        trace.applied_filters = {
            "query_date": query_date,
            "knowledge_categories": categories or [],
            "include_historical": include_historical,
            "access_scope": normalized_scope,
            "access_prefilter": access_prefilter,
        }
        dense_results, sparse_results = [], []
        empty_corpus = not access_prefilter["corpus_count"]
        if strategy in {"dense", "hybrid", "hybrid_rerank"}:
            if allowed_chunk_ids == set() and empty_corpus:
                timings = {}
            elif allowed_chunk_ids is None:
                dense_results, timings = self.dense.search(query, self.candidate_count)
            else:
                dense_results, timings = self.dense.search(
                    query,
                    self.candidate_count,
                    allowed_chunk_ids=allowed_chunk_ids,
                )
            dense_results = self._prefilter_results(dense_results, allowed_chunk_ids)
            trace.latency_ms.update(timings)
            trace.dense_results = dense_results
        if strategy in {"bm25", "hybrid", "hybrid_rerank"}:
            if allowed_chunk_ids == set() and empty_corpus:
                timings = {}
            elif allowed_chunk_ids is None:
                sparse_results, timings = self.sparse.search(query, self.candidate_count)
            else:
                sparse_results, timings = self.sparse.search(
                    query,
                    self.candidate_count,
                    allowed_chunk_ids=allowed_chunk_ids,
                )
            sparse_results = self._prefilter_results(sparse_results, allowed_chunk_ids)
            trace.latency_ms.update(timings)
            trace.sparse_results = sparse_results
        if strategy == "dense":
            final = self._filter_by_access(
                self._filter_and_adjust(dense_results, query_date, categories or [], include_historical, trace),
                access_scope, trace,
            )[:self.final_top_k]
        elif strategy == "bm25":
            final = self._filter_by_access(
                self._filter_and_adjust(sparse_results, query_date, categories or [], include_historical, trace),
                access_scope, trace,
            )[:self.final_top_k]
        else:
            start = time.perf_counter()
            fused = self.fusion.fuse([dense_results, sparse_results], self.candidate_count)
            trace.latency_ms["fusion_ms"] = round((time.perf_counter() - start) * 1000, 3)
            trace.fused_results = fused
            filtered = self._filter_by_access(
                self._filter_and_adjust(fused, query_date, categories or [], include_historical, trace),
                access_scope, trace,
            )
            final = filtered[:self.final_top_k]
            if strategy == "hybrid_rerank":
                if self.reranker is None:
                    trace.degraded = True
                    trace.actual_strategy = "hybrid"
                    trace.degradation_reason = "reranker_disabled"
                else:
                    start = time.perf_counter()
                    try:
                        reranked = self.reranker.rerank(query, filtered[:self.rerank_top_n], self.final_top_k)
                        trace.reranked_results = reranked
                        final = self._filter_by_access(
                            self._filter_and_adjust(reranked, query_date, categories or [], include_historical, trace),
                            access_scope, trace,
                        )[:self.final_top_k]
                    except Exception as exc:
                        trace.degraded = True
                        trace.actual_strategy = "hybrid"
                        trace.degradation_reason = f"reranker_error: {type(exc).__name__}: {exc}"
                    trace.latency_ms["reranker_ms"] = round((time.perf_counter() - start) * 1000, 3)
        for rank, candidate in enumerate(final, 1):
            candidate.final_rank = rank
        trace.final_selected_chunks = final
        trace.candidate_counts = {
            "prefilter_corpus": access_prefilter["corpus_count"],
            "prefilter_allowed": access_prefilter["allowed_count"],
            "prefilter_rejected": access_prefilter["rejected_count"],
            "dense": len(trace.dense_results),
            "sparse": len(trace.sparse_results),
            "fused": len(trace.fused_results),
            "reranked": len(trace.reranked_results),
            "final": len(trace.final_selected_chunks),
        }
        trace.latency_ms["total_retrieval_ms"] = round(sum(value for key, value in trace.latency_ms.items() if key != "total_retrieval_ms"), 3)
        return trace
