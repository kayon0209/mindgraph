"""MindGraph Graph RAG 检索包装（M1-D4）。

在现有 Hybrid 检索结果之上做「一跳图谱扩展」：
命中笔记 → 查 note_relations（status='confirmed'）→ 取关联笔记的 chunk 作为补充证据，
并在 trace 中记录 graph_links 供可信问答引用与前端关系可视化。

设计要点：
- 仅当 graph_enabled 且 strategy 为 hybrid/hybrid_rerank 时扩展（保证可消融对比）；
- 图谱补充证据不抢排名（original_score=0），仅作为引用来源扩展；
- note_relations 当前为空时自然「无关联则跳过」，关系数据后续填充后自动生效；
- 复用现有 RetrievalPipeline 与 Chunk 结构，不改动检索内核。
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
import inspect
from typing import Any

from .pipeline import RetrievalPipeline
from .types import RetrievalCandidate, RetrievalTrace


DEFAULT_GRAPH_HOPS = 1
MAX_GRAPH_HOPS = 2


class MindGraphRetrievalPipeline:
    """包装 RetrievalPipeline，附加图谱一跳扩展。"""

    def __init__(
        self,
        base: RetrievalPipeline,
        graph_store: Any,
        graph_enabled: bool = False,
        max_graph_chunks: int = 4,
        max_graph_hops: int = DEFAULT_GRAPH_HOPS,
        max_graph_edges_per_hop: int = 50,
        max_graph_nodes_per_hop: int = 20,
    ) -> None:
        self.base = base
        self.graph_store = graph_store
        self.graph_enabled = graph_enabled
        self.max_graph_chunks = max_graph_chunks
        self.max_graph_hops = max_graph_hops
        self.max_graph_edges_per_hop = max_graph_edges_per_hop
        self.max_graph_nodes_per_hop = max_graph_nodes_per_hop

    @property
    def dense(self):
        return self.base.dense

    def retrieve(self, query, strategy, query_date=None, categories=None,
                 include_historical=False, graph_enabled=None, access_scope=None, graph_hops=None,
                 query_variants=None):
        ge = self.graph_enabled if graph_enabled is None else graph_enabled
        hops = self.max_graph_hops if graph_hops is None else graph_hops
        variants = [query]
        for v in query_variants or []:
            if v and v.strip() and v != query:
                variants.append(v.strip())
        if len(variants) == 1:
            trace = self.base.retrieve(
                query, strategy, query_date, categories, include_historical, access_scope=access_scope,
            )
            trace.query_variants = [query]
        else:
            # 跨语言/多语言查询变体：各变体独立混合检索，RRF 按排名融合
            # （score 跨语言不可比，rank 可比——与 chat 层同语言 max-score 合并互补）
            traces = [self.base.retrieve(
                v, strategy, query_date, categories, include_historical, access_scope=access_scope,
            ) for v in variants]
            trace = traces[0]
            self._rrf_merge_variants(traces, strategy)
            trace.query_variants = list(variants)
            trace.original_query = query
            trace.warnings.append(f"query_variants_applied:{len(variants)}")
        trace.graph_enabled = ge
        trace.graph_hops = hops
        if ge and strategy in {"hybrid", "hybrid_rerank"}:
            try:
                self._expand_graph(trace, access_scope=access_scope, hops=hops)
            except Exception as exc:
                trace.warnings.append(f"graph_expansion_failed:{type(exc).__name__}")
                trace.graph_links = []
        trace.candidate_counts = {
            **trace.candidate_counts,
            "final": len(trace.final_selected_chunks),
            "graph_expanded": trace.candidate_counts.get("graph_expanded", 0),
        }
        return trace

    @staticmethod
    def _rrf_merge_variants(traces: list, strategy: str, k: int = 60) -> None:
        """按 chunk_id 做 RRF 排名融合（1/(k+rank) 累加），写入主 trace。"""
        def merge(attr: str, limit: int) -> list:
            scores: dict[str, float] = {}
            best: dict[str, Any] = {}
            for t in traces:
                for rank, cand in enumerate(getattr(t, attr), 1):
                    cid = cand.chunk.chunk_id
                    scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
                    best.setdefault(cid, cand)
            ordered = sorted(best.values(), key=lambda c: scores[c.chunk.chunk_id], reverse=True)
            return ordered[:limit]

        main = traces[0]
        main.dense_results = merge("dense_results", len(main.dense_results))
        main.sparse_results = merge("sparse_results", len(main.sparse_results))
        if strategy in {"hybrid", "hybrid_rerank"}:
            main.fused_results = merge("fused_results", len(main.fused_results))
        top_k = len(main.final_selected_chunks)
        if strategy == "dense":
            main.final_selected_chunks = merge("dense_results", top_k)
        elif strategy == "bm25":
            main.final_selected_chunks = merge("sparse_results", top_k)
        else:
            main.final_selected_chunks = merge("fused_results", top_k)
        for rank, cand in enumerate(main.final_selected_chunks, 1):
            cand.final_rank = rank

    def _expand_graph(self, trace: RetrievalTrace, *, access_scope=None, hops: int = DEFAULT_GRAPH_HOPS) -> None:
        hit_notes = {
            c.chunk.metadata.get("mindgraph_id")
            for c in trace.final_selected_chunks
            if c.chunk.metadata.get("mindgraph_id")
        }
        if not hit_notes:
            trace.candidate_counts = {
                **trace.candidate_counts,
                "final": len(trace.final_selected_chunks),
                "graph_expanded": 0,
            }
            return
        relation_method = self.graph_store.related_note_ids
        parameters = inspect.signature(relation_method).parameters
        relation_kwargs = {}
        if "hops" in parameters:
            relation_kwargs["hops"] = hops
        if "access_scope" in parameters:
            relation_kwargs["access_scope"] = access_scope
        if "as_of" in parameters:
            relation_kwargs["as_of"] = trace.applied_filters.get("query_date") if trace.applied_filters else None
        if "max_edges_per_hop" in parameters:
            relation_kwargs["max_edges_per_hop"] = self.max_graph_edges_per_hop
        if "max_nodes_per_hop" in parameters:
            relation_kwargs["max_nodes_per_hop"] = self.max_graph_nodes_per_hop
        relations = relation_method(hit_notes, **relation_kwargs)
        if not relations:
            trace.candidate_counts = {
                **trace.candidate_counts,
                "final": len(trace.final_selected_chunks),
                "graph_expanded": 0,
            }
            return

        # 证据可追溯校验：关系引用的 evidence_chunk_id 必须存在于激活索引，
        # 否则无法回原文；无 span 兜底的关系不进入检索扩展。
        known_chunk_ids = {c.chunk_id for c in self.dense.chunks}
        resolvable: list[dict] = []
        for rel in relations:
            evidence_chunk_id = rel.get("evidence_chunk_id")
            if evidence_chunk_id and evidence_chunk_id not in known_chunk_ids:
                trace.warnings.append(
                    f"graph_evidence_chunk_unresolved:{evidence_chunk_id}:{rel.get('relation_id')}"
                )
                if not rel.get("evidence_span"):
                    continue
            resolvable.append(rel)
        relations = resolvable
        if not relations:
            trace.candidate_counts = {
                **trace.candidate_counts,
                "final": len(trace.final_selected_chunks),
                "graph_expanded": 0,
            }
            return

        # 按 target 聚合，保留最高 confidence 的关系
        rel_by_target: dict[str, dict] = {}
        for rel in relations:
            target_id = rel["target_note_id"]
            if target_id not in rel_by_target or (rel.get("confidence") or 0) > (rel_by_target[target_id].get("confidence") or 0):
                rel_by_target[target_id] = rel

        titles = self.graph_store.note_titles(set(rel_by_target) | hit_notes)
        existing_ids = {c.chunk.chunk_id for c in trace.final_selected_chunks}
        added = 0
        added_targets: set[str] = set()
        for target_id, rel in rel_by_target.items():
            if target_id in hit_notes:
                continue  # 已是命中笔记，跳过
            for chunk in self.dense.chunks:
                if chunk.metadata.get("mindgraph_id") != target_id:
                    continue
                if chunk.chunk_id in existing_ids:
                    continue
                if not self._visible_for_trace(chunk, trace):
                    continue
                enriched_meta = dict(chunk.metadata)
                enriched_meta.update({
                    "graph_evidence": True,
                    "via_relation": rel.get("relation_type"),
                    "via_source_note": rel.get("source_note_id"),
                    "graph_confidence": rel.get("confidence", 0.0),
                    "graph_relation_id": rel.get("relation_id"),
                    "graph_relation_direction": rel.get("direction"),
                    "graph_traversed_direction": rel.get("traversed_direction"),
                    "graph_relation_status": rel.get("status"),
                    "graph_model_version": rel.get("model_version"),
                    "graph_prompt_version": rel.get("prompt_version"),
                    "graph_evidence_span": rel.get("evidence_span"),
                    "graph_evidence_section": rel.get("evidence_section"),
                    "graph_source_document_version": rel.get("source_document_version"),
                    "graph_extraction_method": rel.get("extraction_method"),
                    "graph_proposed_at": rel.get("proposed_at"),
                    "graph_resolved_at": rel.get("resolved_at"),
                })
                candidate = RetrievalCandidate(
                    chunk=replace(chunk, metadata=enriched_meta),
                    dense_score=None,
                    sparse_score=None,
                    rrf_score=None,
                    reranker_score=None,
                    original_score=0.0,
                    authority_adjustment=0.0,
                    adjusted_score=0.0,
                )
                trace.final_selected_chunks.append(candidate)
                existing_ids.add(chunk.chunk_id)
                added_targets.add(target_id)
                added += 1
                if added >= self.max_graph_chunks:
                    break
            if added >= self.max_graph_chunks:
                break

        # 构建 graph_links（供前端关系可视化与引用溯源）
        graph_links = []
        for target_id, rel in rel_by_target.items():
            if target_id in hit_notes or target_id not in added_targets:
                continue
            graph_links.append({
                "relation_id": rel.get("relation_id"),
                "source_note_id": rel.get("source_note_id"),
                "source_title": titles.get(rel.get("source_note_id"), rel.get("source_note_id")),
                "relation_type": rel.get("relation_type"),
                "target_note_id": target_id,
                "target_title": titles.get(target_id, target_id),
                "evidence_chunk_id": rel.get("evidence_chunk_id"),
                "confidence": rel.get("confidence", 0.0),
                "status": rel.get("status"),
                "direction": rel.get("direction"),
                "traversed_direction": rel.get("traversed_direction"),
                "model_version": rel.get("model_version"),
                "prompt_version": rel.get("prompt_version"),
                "proposed_at": rel.get("proposed_at"),
                "resolved_at": rel.get("resolved_at"),
                "evidence_span": rel.get("evidence_span"),
                "evidence_section": rel.get("evidence_section"),
                "source_document_version": rel.get("source_document_version"),
                "effective_from": rel.get("effective_from"),
                "effective_to": rel.get("effective_to"),
                "extraction_method": rel.get("extraction_method"),
                "hop": rel.get("hop", 1),
            })

        for rank, candidate in enumerate(trace.final_selected_chunks, 1):
            candidate.final_rank = rank
        trace.graph_links = graph_links
        trace.candidate_counts = {
            **trace.candidate_counts,
            "final": len(trace.final_selected_chunks),
            "graph_expanded": added,
        }

    def _visible_for_trace(self, chunk, trace: RetrievalTrace) -> bool:
        filters = trace.applied_filters
        metadata = chunk.metadata
        status = metadata.get("document_status") or metadata.get("policy_status")
        if not filters.get("include_historical", False) and status and status != "active":
            return False
        categories = filters.get("knowledge_categories") or []
        if categories and metadata.get("knowledge_category") not in categories:
            return False
        target_date = date.fromisoformat(filters["query_date"]) if filters.get("query_date") else date.today()
        effective = metadata.get("effective_date") or metadata.get("effective_from")
        expiration = metadata.get("expiration_date") or metadata.get("effective_to")
        if effective and date.fromisoformat(effective) > target_date:
            return False
        if expiration and date.fromisoformat(expiration) < target_date and not filters.get("include_historical", False):
            return False
        access_scope = filters.get("access_scope")
        if access_scope is not None:
            from application.access_control import chunk_acl_matches
            if not chunk_acl_matches(metadata, access_scope):
                return False
        return True
