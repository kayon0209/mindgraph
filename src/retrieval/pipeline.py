from __future__ import annotations

import inspect
import logging
import time
from datetime import date

from infrastructure.date_utils import parse_date_safe

from .types import DenseRetriever, FusionStrategy, Reranker, RetrievalTrace, SparseRetriever

logger = logging.getLogger("mindgraph.retrieval.pipeline")


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


def filter_candidates_by_source(candidates: list, source_ids: list[str] | None, trace: RetrievalTrace) -> list:
    """按数据源命名空间裁剪候选；`source_ids` 为空 → 原样返回（旧行为）。

    匹配规则（显式、可预期）：候选 metadata 里**任一**命名空间字段与某个 token
    满足以下之一即保留（token 与值都按 POSIX 分隔符归一、去首尾斜杠）：

    - **身份匹配**（所有字段）：完全相等；
    - **父级后缀**（所有字段）：值以 `/<token>` 结尾 —— 便于用 `"external/public"`
      定位而不必写绝对路径（真实 `source_id` 形如
      `<vault_root>/knowledge/external/public`）；
    - **目录前缀**（**仅** `source_path` / `vault_path`）：值以 `<token>/` 开头 ——
      文档相对路径下 token 表示一棵子树（token `"external/public"` 命中
      `source_path = "external/public/gitlab.md"`）。

    为什么前缀匹配只给路径字段：`source_id` / `workspace` 是**命名空间标签**
    （vault 根或 path_prefix）。若对它们启用前缀匹配，用 vault 根过滤会把嵌套在
    其下的另一套语料一并放进来，"按源隔离"就失效了 —— 已有测试
    `test_exact_source_id_selects_only_that_source` 锁的就是这条语义。

    参与匹配的字段：`source_id`（vault 根，粒度最粗）、`source_path` /
    `vault_path`（文档相对路径）、`workspace`（知识空间，如 `knowledge` / `public`）。
    为什么不止 `source_id`：本机 25 篇笔记的 `source_id` **全都是同一个 vault 根**，
    只认它的话"按源隔离"实际上只能整体放行或整体拒绝——真正能区分
    「自建中文制度」与「`external/public` 英文公开手册」的是 `source_path` 与 `workspace`。

    无任何命名空间元数据的历史数据在显式指定 source_ids 时会被剔除并计入
    warnings —— 否则「按源隔离」会被未归属数据静默穿透，等于没做。
    提成模块级函数是因为图扩展路径（mindgraph_pipeline）也要复用，
    且它不依赖 RetrievalPipeline 实例，测试替身也能直接用。
    """
    if not source_ids:
        return candidates
    wanted = {str(item).replace("\\", "/").rstrip("/") for item in source_ids}
    # 标签字段：只做身份 / 父级后缀匹配（前缀匹配会让 vault 根把嵌套语料一起放进来）。
    label_fields = ("source_id", "workspace")
    # 路径字段：额外允许目录前缀匹配，用于按子树隔离。
    path_fields = ("source_path", "vault_path")

    def _normalized(metadata: dict, field: str) -> str | None:
        value = metadata.get(field)
        if value in (None, ""):
            return None
        return str(value).replace("\\", "/").rstrip("/")

    def _matches(value: str, allow_prefix: bool) -> bool:
        for token in wanted:
            if value == token or value.endswith(f"/{token}"):
                return True
            if allow_prefix and value.startswith(f"{token}/"):
                return True
        return False

    kept: list = []
    unattributed = 0
    for candidate in candidates:
        metadata = candidate.chunk.metadata or {}
        labels = [value for field in label_fields if (value := _normalized(metadata, field))]
        paths = [value for field in path_fields if (value := _normalized(metadata, field))]
        if not labels and not paths:
            unattributed += 1
            continue
        if any(_matches(value, False) for value in labels) or any(_matches(value, True) for value in paths):
            kept.append(candidate)
    if unattributed:
        trace.warnings.append(f"source_unattributed_dropped:{unattributed}")
    if len(kept) != len(candidates):
        trace.warnings.append("source_filtered_chunks")
    trace.warnings = list(dict.fromkeys(trace.warnings))
    return kept


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
    def _search(
        retriever,
        query: str,
        top_k: int,
        access_scope: dict | None,
        query_date: str | None = None,
        categories: list[str] | None = None,
        include_historical: bool = False,
    ):
        parameters = inspect.signature(retriever.search).parameters
        kwargs = {}
        for name, value in (
            ("access_scope", access_scope),
            ("query_date", query_date),
            ("categories", categories),
            ("include_historical", include_historical),
        ):
            if name in parameters:
                kwargs[name] = value
        return retriever.search(query, top_k, **kwargs)

    @staticmethod
    def _base_score(candidate) -> float:
        for value in (candidate.reranker_score, candidate.rrf_score, candidate.dense_score, candidate.sparse_score):
            if value is not None:
                return float(value)
        return 0.0

    def _filter_and_adjust(self, candidates, query_date, categories, include_historical, trace):
        selected = []
        target_date = parse_date_safe(query_date) or date.today()
        missing_date_metadata = False
        missing_status_metadata = False
        invalid_date_metadata = False
        for candidate in candidates:
            metadata = candidate.chunk.metadata
            status = metadata.get("document_status")
            if status is None:
                missing_status_metadata = True
            elif not include_historical and status != "active":
                continue
            if categories and metadata.get("knowledge_category") not in categories:
                continue
            effective = parse_date_safe(metadata.get("effective_date"))
            expiration = parse_date_safe(metadata.get("expiration_date"))
            # 非法日期元数据不再让整条检索 503：按"缺省日期"处理并告警（读侧容错，
            # 写侧由 vault_sync_service 标记 invalid_effective_date_format）。
            if (effective is None) != (metadata.get("effective_date") in (None, "")) or (
                expiration is None
            ) != (metadata.get("expiration_date") in (None, "")):
                invalid_date_metadata = True
            if effective and effective > target_date:
                continue
            # 与 dense/sparse/图扩展路径保持一致：include_historical=True 时
            # 允许查看已过期文档（未生效的仍无条件排除）。
            if expiration and expiration < target_date and not include_historical:
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
        if invalid_date_metadata:
            trace.warnings.append("invalid_date_metadata_treated_as_missing")
        return sorted(selected, key=lambda item: (item.adjusted_score or 0.0, item.chunk.chunk_id), reverse=True)

    def _filter_by_access(self, candidates: list, access_scope: dict | None, trace: RetrievalTrace) -> list:
        """按当前主体的 ACL 范围裁剪候选。

        access_scope 形如：{"allow": [...], "deny": [...], "user": "...", "roles": [...]}
        - 无 access_scope（单用户 / demo / 旧版调用）→ 不裁剪；
        - 有 access_scope → 拒绝无 ACL 元数据的 chunk，仅保留显式命中的。
        """
        if access_scope is None:
            return candidates
        from application.access_control import chunk_acl_matches

        allowed = set(access_scope.get("allow") or [])
        denied = set(access_scope.get("deny") or [])
        scope = {
            "allow": allowed,
            "deny": denied,
            "roles": access_scope.get("roles", []),
            "user": access_scope.get("user"),
        }
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

    def _filter_by_source(self, candidates: list, source_ids: list[str] | None, trace: RetrievalTrace) -> list:
        """按**数据源命名空间**裁剪候选（source_ids=None → 不裁剪，行为与旧版一致）。

        背景：chunk metadata 里已带 `source_id`（由 `mindgraph_index_service` 从
        notes.source_id 写入，值在同步时由 vault 根/path_prefix 决定，
        `vault_sync_service.py:202`），但检索侧此前只能按 workspace 级
        access_scope 过滤——同一个 vault 根下的多套内容（例如自建中文制度与
        `external/public` 的英文公开手册）会共用一个 source_id，落进同一个
        检索池且无法区分。这个参数补上的就是那一层。

        实现见模块级 `filter_candidates_by_source`（图扩展路径也要复用它）。
        """
        return filter_candidates_by_source(candidates, source_ids, trace)

    def retrieve(self, query: str, strategy: str, query_date: str | None = None,
                 categories: list[str] | None = None, include_historical: bool = False,
                 access_scope: dict | None = None,
                 source_ids: list[str] | None = None) -> RetrievalTrace:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown retrieval strategy: {strategy}")
        trace = RetrievalTrace(query=query, requested_strategy=strategy, actual_strategy=strategy)
        trace.index_version = getattr(self.dense, "metadata", {}).get("index_version")
        trace.applied_filters = {"query_date": query_date, "knowledge_categories": categories or [], "include_historical": include_historical, "access_scope": access_scope, "source_ids": source_ids or []}
        dense_results, sparse_results = [], []
        if strategy in {"dense", "hybrid", "hybrid_rerank"}:
            dense_results, timings = self._search(
                self.dense, query, self.candidate_count, access_scope,
                query_date, categories, include_historical,
            )
            dense_results = self._filter_by_access(dense_results, access_scope, trace)
            dense_results = self._filter_by_source(dense_results, source_ids, trace)
            trace.latency_ms.update(timings)
            trace.dense_results = dense_results
        if strategy in {"bm25", "hybrid", "hybrid_rerank"}:
            sparse_results, timings = self._search(
                self.sparse, query, self.candidate_count, access_scope,
                query_date, categories, include_historical,
            )
            sparse_results = self._filter_by_access(sparse_results, access_scope, trace)
            sparse_results = self._filter_by_source(sparse_results, source_ids, trace)
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
            fused = self._filter_by_access(fused, access_scope, trace)
            trace.fused_results = fused
            filtered = self._filter_and_adjust(fused, query_date, categories or [], include_historical, trace)
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
                        reranked = self._filter_by_access(reranked, access_scope, trace)
                        trace.reranked_results = reranked
                        final = self._filter_and_adjust(reranked, query_date, categories or [], include_historical, trace)[:self.final_top_k]
                    except Exception as exc:
                        trace.degraded = True
                        trace.actual_strategy = "hybrid"
                        trace.degradation_reason = f"reranker_error: {type(exc).__name__}: {exc}"
                    trace.latency_ms["reranker_ms"] = round((time.perf_counter() - start) * 1000, 3)
        # access_scope 生效时把候选全部裁掉，此前是**完全静默**的：检索器内部
        # （dense.py / sparse.py 的 chunk_acl_matches）逐条丢弃，管线只看到空列表，
        # 于是问答表现为「无证据」而没有任何线索。最常见的成因不是权限收紧，
        # 而是索引里压根没有 ACL 元数据 —— 文件式索引（load_corpus →
        # document_loader）只写 doc_name/section_path/chunk_index/source/origin，
        # 不含 acl_json / workspace，于是每个 chunk 都被判定不可见。
        acl_dropped_total = int(
            trace.latency_ms.get("acl_dropped_dense", 0.0) + trace.latency_ms.get("acl_dropped_sparse", 0.0)
        )
        if acl_dropped_total:
            trace.warnings.append(f"acl_dropped_chunks:{acl_dropped_total}")
            if not final:
                trace.warnings.append("acl_filtered_all_candidates")
                logger.warning(
                    "acl_filtered_all_candidates",
                    extra={
                        "query": trace.query[:80],
                        "access_scope_allow": (access_scope or {}).get("allow"),
                        "acl_dropped": acl_dropped_total,
                    },
                )
        trace.warnings = list(dict.fromkeys(trace.warnings))
        final = self._filter_by_source(final, source_ids, trace)
        for rank, candidate in enumerate(final, 1):
            candidate.final_rank = rank
        trace.final_selected_chunks = final
        trace.candidate_counts = {
            "dense": len(trace.dense_results),
            "sparse": len(trace.sparse_results),
            "fused": len(trace.fused_results),
            "reranked": len(trace.reranked_results),
            "final": len(trace.final_selected_chunks),
        }
        trace.latency_ms["total_retrieval_ms"] = round(sum(value for key, value in trace.latency_ms.items() if key != "total_retrieval_ms"), 3)
        return trace
