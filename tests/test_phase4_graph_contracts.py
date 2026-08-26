from __future__ import annotations

import pytest

from application.mindgraph_graph_store import MindGraphGraphStore
from retrieval.mindgraph_pipeline import MindGraphRetrievalPipeline
from retrieval.types import Chunk, RetrievalCandidate, RetrievalTrace


class _FakeDb:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def fetch_all(self, sql: str, params: tuple[object, ...]) -> list[dict[str, object]]:
        if "FROM note_relations" in sql:
            return self.rows
        if "FROM notes" in sql:
            return [
                {"note_id": row["source_note_id"], "title": f"title-{row['source_note_id']}"}
                for row in self.rows
            ]
        return []


class _BasePipeline:
    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        self.calls: list[dict[str, object]] = []

    @property
    def dense(self):
        return self

    def retrieve(self, query, strategy, query_date=None, categories=None, include_historical=False, access_scope=None):
        self.calls.append({"strategy": strategy, "access_scope": access_scope})
        return RetrievalTrace(
            query=query,
            requested_strategy=strategy,
            actual_strategy=strategy,
            final_selected_chunks=[RetrievalCandidate(chunk=self.chunks[0], final_rank=1)],
            applied_filters={"query_date": query_date, "knowledge_categories": categories or [], "include_historical": include_historical, "access_scope": access_scope},
        )


@pytest.fixture()
def relation_rows() -> list[dict[str, object]]:
    return [
        {
            "relation_id": "rel-1",
            "source_note_id": "n1",
            "target_note_id": "n2",
            "relation_type": "related_to",
            "direction": "outgoing",
            "status": "confirmed",
            "evidence_chunk_id": "chunk-1",
            "confidence": 0.9,
            "model_version": "m1",
            "prompt_version": "p1",
            "proposed_at": "2026-01-01T00:00:00Z",
            "resolved_at": "2026-01-02T00:00:00Z",
            "resolved_by": "u1",
        }
    ]


def test_graph_store_returns_typed_relations_and_rejects_invalid_hops(relation_rows):
    store = MindGraphGraphStore(_FakeDb(relation_rows))

    relations = store.related_note_ids(["n1"], hops=1)

    assert relations[0]["relation_id"] == "rel-1"
    assert relations[0]["evidence"]["chunk_id"] == "chunk-1"
    assert relations[0]["hop"] == 1
    with pytest.raises(ValueError, match="hops must be between 1 and 2"):
        store.related_note_ids(["n1"], hops=3)
    with pytest.raises(ValueError, match="unsupported relation status"):
        store.related_note_ids(["n1"], status="draft")


def test_graph_store_traverses_incoming_relations_and_normalizes_direction(relation_rows):
    """命中笔记是 target 时，关系通过 incoming 方向反向发现，并归一化为「命中→目标」。"""
    store = MindGraphGraphStore(_FakeDb(relation_rows))

    relations = store.related_note_ids(["n2"], hops=1)

    assert len(relations) == 1
    assert relations[0]["source_note_id"] == "n2"
    assert relations[0]["target_note_id"] == "n1"
    assert relations[0]["direction"] == "outgoing"  # 原始存储方向
    assert relations[0]["traversed_direction"] == "incoming"  # 本次遍历方向


def test_graph_store_requires_chunk_evidence_for_governance_relations(relation_rows):
    """SUPERSEDES / CONTRADICTS 等治理型关系必须引用具体 chunk，不能只靠 span 摘要。"""
    superseded_row = dict(
        relation_rows[0],
        relation_type="SUPERSEDES",
        evidence_chunk_id=None,
        evidence_span="V2 替代 V1 全文",
    )
    store = MindGraphGraphStore(_FakeDb([superseded_row]))

    assert store.related_note_ids(["n1"], hops=1) == []

    superseded_row["evidence_chunk_id"] = "chunk-1"
    relations = store.related_note_ids(["n1"], hops=1)
    assert len(relations) == 1
    assert relations[0]["relation_type"] == "SUPERSEDES"


def test_pipeline_exposes_graph_links_and_metadata(relation_rows):
    base_chunk = Chunk("chunk-0", "root", "doc-1", 0, None, {"mindgraph_id": "n1", "document_status": "active", "knowledge_category": "policy"})
    target_chunk = Chunk("chunk-1", "linked", "doc-2", 0, None, {"mindgraph_id": "n2", "document_status": "active", "knowledge_category": "policy"})
    base = _BasePipeline([base_chunk, target_chunk])
    pipeline = MindGraphRetrievalPipeline(base, MindGraphGraphStore(_FakeDb(relation_rows)), graph_enabled=True, max_graph_chunks=2, max_graph_hops=2)

    trace = pipeline.retrieve("q", "hybrid", graph_enabled=True, access_scope={"allow": ["*"], "deny": []})

    assert trace.graph_enabled is True
    assert trace.graph_hops == 2
    assert trace.candidate_counts["graph_expanded"] == 1
    assert trace.final_selected_chunks[-1].chunk.metadata["graph_evidence"] is True
    assert trace.final_selected_chunks[-1].chunk.metadata["graph_relation_id"] == "rel-1"
    assert trace.graph_links[0]["relation_id"] == "rel-1"
    assert trace.graph_links[0]["hop"] == 1


def test_pipeline_honors_acl_and_graph_can_be_disabled(relation_rows):
    blocked_rows = [dict(relation_rows[0], workspace="restricted")]
    base_chunk = Chunk("chunk-0", "root", "doc-1", 0, None, {"mindgraph_id": "n1", "document_status": "active", "knowledge_category": "policy"})
    target_chunk = Chunk("chunk-1", "linked", "doc-2", 0, None, {"mindgraph_id": "n2", "document_status": "active", "knowledge_category": "policy", "workspace": "restricted"})
    base = _BasePipeline([base_chunk, target_chunk])
    pipeline = MindGraphRetrievalPipeline(base, MindGraphGraphStore(_FakeDb(blocked_rows)), graph_enabled=True, max_graph_chunks=2)

    trace = pipeline.retrieve("q", "hybrid", graph_enabled=False, access_scope={"allow": ["workspace:public"], "deny": ["workspace:restricted"]})

    assert trace.graph_enabled is False
    assert trace.graph_links == []
    assert trace.candidate_counts.get("graph_expanded", 0) == 0


def test_pipeline_excludes_relations_with_unresolvable_evidence_chunk(relation_rows):
    """evidence_chunk_id 必须能回原文；索引里不存在且无 span 兜底时，该关系不得扩展。"""
    ghost_row = dict(relation_rows[0], evidence_chunk_id="ghost-chunk", evidence_span=None)
    base_chunk = Chunk("chunk-0", "root", "doc-1", 0, None, {"mindgraph_id": "n1", "document_status": "active", "knowledge_category": "policy"})
    target_chunk = Chunk("chunk-1", "linked", "doc-2", 0, None, {"mindgraph_id": "n2", "document_status": "active", "knowledge_category": "policy"})
    base = _BasePipeline([base_chunk, target_chunk])
    pipeline = MindGraphRetrievalPipeline(base, MindGraphGraphStore(_FakeDb([ghost_row])), graph_enabled=True, max_graph_chunks=2)

    trace = pipeline.retrieve("q", "hybrid", graph_enabled=True, access_scope={"allow": ["*"], "deny": []})

    assert trace.candidate_counts.get("graph_expanded", 0) == 0
    assert trace.graph_links == []
    assert any("graph_evidence_chunk_unresolved:ghost-chunk" in warning for warning in trace.warnings)


def test_pipeline_keeps_relation_when_span_falls_back_for_unresolved_chunk(relation_rows):
    """evidence_chunk_id 无法解析但有 span 时，关系保留并记录警告，不静默丢弃。"""
    span_row = dict(relation_rows[0], evidence_chunk_id="ghost-chunk", evidence_span="V2 明确替代 V1")
    base_chunk = Chunk("chunk-0", "root", "doc-1", 0, None, {"mindgraph_id": "n1", "document_status": "active", "knowledge_category": "policy"})
    target_chunk = Chunk("chunk-1", "linked", "doc-2", 0, None, {"mindgraph_id": "n2", "document_status": "active", "knowledge_category": "policy"})
    base = _BasePipeline([base_chunk, target_chunk])
    pipeline = MindGraphRetrievalPipeline(base, MindGraphGraphStore(_FakeDb([span_row])), graph_enabled=True, max_graph_chunks=2)

    trace = pipeline.retrieve("q", "hybrid", graph_enabled=True, access_scope={"allow": ["*"], "deny": []})

    assert trace.candidate_counts["graph_expanded"] == 1
    assert any("graph_evidence_chunk_unresolved:ghost-chunk" in warning for warning in trace.warnings)
