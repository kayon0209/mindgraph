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
from typing import Any

from .pipeline import RetrievalPipeline
from .types import RetrievalCandidate, RetrievalTrace


class MindGraphRetrievalPipeline:
    """包装 RetrievalPipeline，附加图谱一跳扩展。"""

    def __init__(
        self,
        base: RetrievalPipeline,
        graph_store: Any,
        graph_enabled: bool = True,
        max_graph_chunks: int = 4,
    ) -> None:
        self.base = base
        self.graph_store = graph_store
        self.graph_enabled = graph_enabled
        self.max_graph_chunks = max_graph_chunks

    @property
    def dense(self):
        return self.base.dense

    def retrieve(self, query, strategy, query_date=None, categories=None,
                 include_historical=False, graph_enabled=None, access_scope=None):
        ge = self.graph_enabled if graph_enabled is None else graph_enabled
        trace = self.base.retrieve(
            query, strategy, query_date, categories, include_historical, access_scope=access_scope,
        )
        trace.graph_enabled = ge
        if ge and strategy in {"hybrid", "hybrid_rerank"}:
            self._expand_graph(trace)
        return trace

    def _expand_graph(self, trace: RetrievalTrace) -> None:
        hit_notes = {
            c.chunk.metadata.get("mindgraph_id")
            for c in trace.final_selected_chunks
            if c.chunk.metadata.get("mindgraph_id")
        }
        if not hit_notes:
            return
        relations = self.graph_store.related_note_ids(hit_notes)
        if not relations:
            return

        # 按 target 聚合，保留最高 confidence 的关系
        rel_by_target: dict[str, dict] = {}
        for r in relations:
            t = r["target_note_id"]
            if t not in rel_by_target or (r.get("confidence") or 0) > (rel_by_target[t].get("confidence") or 0):
                rel_by_target[t] = r

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
                "source_note_id": rel.get("source_note_id"),
                "source_title": titles.get(rel.get("source_note_id"), rel.get("source_note_id")),
                "relation_type": rel.get("relation_type"),
                "target_note_id": target_id,
                "target_title": titles.get(target_id, target_id),
                "confidence": rel.get("confidence", 0.0),
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
        governed = trace.governance_allowed_chunk_ids
        if governed is None or chunk.chunk_id not in governed:
            return False
        categories = filters.get("knowledge_categories") or []
        if categories and metadata.get("knowledge_category") not in categories:
            return False
        access_scope = filters.get("access_scope")
        if access_scope is not None:
            from application.access_control import chunk_acl_matches
            scope = self.base._normalized_access_scope(access_scope)
            if not chunk_acl_matches(metadata, scope):
                return False
        return True
