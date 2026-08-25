from __future__ import annotations

import json
from pathlib import Path

import pytest

from retrieval.dense import FAISSDenseRetriever
from retrieval.fusion import ReciprocalRankFusion
from retrieval.mindgraph_pipeline import MindGraphRetrievalPipeline
from retrieval.pipeline import RetrievalPipeline
from retrieval.sparse import BM25Retriever
from retrieval.types import Chunk, RetrievalCandidate


def _chunk(
    chunk_id: str,
    text: str,
    *,
    public: bool = False,
    department: str = "hr",
    mindgraph_id: str | None = None,
) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        text=text,
        document_id=chunk_id.split("::", 1)[0],
        chunk_index=0,
        section_path="private-section" if not public else "public-section",
        metadata={
            "acl_public": public,
            "department": department,
            "document_status": "active",
            "mindgraph_id": mindgraph_id,
            "vault_path": f"secret/{chunk_id}.md" if not public else f"public/{chunk_id}.md",
        },
    )


class CrowdingEmbeddingProvider:
    model_name = "crowding-test"
    model_revision = "1"
    dimension = 2

    def embed_documents(self, texts):
        return [[0.8, 0.6] if "allowed-low-score" in text else [1.0, 0.0] for text in texts]

    def embed_query(self, _text):
        return [1.0, 0.0]


class RecordingReranker:
    model_name = "recording"

    def __init__(self) -> None:
        self.seen_ids: list[str] = []

    def rerank(self, _query, candidates, top_k):
        self.seen_ids = [candidate.chunk.chunk_id for candidate in candidates]
        reranked = list(candidates)[:top_k]
        for rank, candidate in enumerate(reranked, 1):
            candidate.reranker_score = 1.0 / rank
        return reranked


class RecordingFusion:
    def __init__(self) -> None:
        self.calls = 0

    def fuse(self, _rankings, _top_k):
        self.calls += 1
        return []


class RecordingRetriever:
    def __init__(self, chunks: list[Chunk], score_field: str) -> None:
        self.chunks = chunks
        self.metadata = {}
        self.score_field = score_field
        self.allowed_calls: list[set[str] | None] = []

    def search(self, _query, top_k, allowed_chunk_ids=None):
        self.allowed_calls.append(allowed_chunk_ids)
        selected = self.chunks if allowed_chunk_ids is None else [
            chunk for chunk in self.chunks if chunk.chunk_id in allowed_chunk_ids
        ]
        results = []
        for rank, chunk in enumerate(selected[:top_k], 1):
            kwargs = {self.score_field: 1.0 / rank, self.score_field.replace("score", "rank"): rank}
            results.append(RetrievalCandidate(chunk=chunk, **kwargs))
        return results, {}


def _ids(candidates) -> list[str]:
    return [candidate.chunk.chunk_id for candidate in candidates]


def test_hybrid_prefilter_prevents_private_top_n_crowding_and_trace_leakage(tmp_path: Path):
    private_chunks = [
        _chunk(f"private-{index}::0", "针 private-secret-body " * 10)
        for index in range(20)
    ]
    allowed = _chunk(
        "allowed::0",
        "针 allowed-low-score",
        department="finance",
    )
    chunks = [*private_chunks, allowed]
    dense = FAISSDenseRetriever(CrowdingEmbeddingProvider(), tmp_path / "index")
    dense.build(chunks, {"index_version": "acl-prefilter-test"})
    sparse = BM25Retriever(chunks)
    reranker = RecordingReranker()
    pipeline = RetrievalPipeline(
        dense,
        sparse,
        ReciprocalRankFusion(),
        reranker,
        candidate_count=20,
        rerank_top_n=10,
        final_top_k=5,
    )

    trace = pipeline.retrieve(
        "针",
        "hybrid_rerank",
        access_scope={"allow": ["department:finance"], "deny": []},
    )

    assert _ids(trace.dense_results) == ["allowed::0"]
    assert _ids(trace.sparse_results) == ["allowed::0"]
    assert _ids(trace.fused_results) == ["allowed::0"]
    assert reranker.seen_ids == ["allowed::0"]
    assert _ids(trace.reranked_results) == ["allowed::0"]
    assert _ids(trace.final_selected_chunks) == ["allowed::0"]
    assert trace.candidate_counts["prefilter_corpus"] == 21
    assert trace.candidate_counts["prefilter_allowed"] == 1
    assert trace.candidate_counts["prefilter_rejected"] == 20
    assert trace.applied_filters["access_prefilter"]["reason"] == "scope_match"
    serialized = json.dumps(trace.to_dict(), ensure_ascii=False)
    assert "private-secret-body" not in serialized
    assert "secret/private-" not in serialized


def test_scope_without_allow_tags_is_public_only_and_none_is_explicit_bypass():
    public = _chunk("public::0", "public needle", public=True)
    private = _chunk("private::0", "private needle")
    chunks = [private, public]
    dense = RecordingRetriever(chunks, "dense_score")
    sparse = RecordingRetriever(chunks, "sparse_score")
    pipeline = RetrievalPipeline(dense, sparse, ReciprocalRankFusion(), final_top_k=5)

    public_trace = pipeline.retrieve("needle", "hybrid", access_scope={"allow": [], "deny": []})
    assert dense.allowed_calls[-1] == {"public::0"}
    assert sparse.allowed_calls[-1] == {"public::0"}
    assert _ids(public_trace.dense_results) == ["public::0"]
    assert _ids(public_trace.sparse_results) == ["public::0"]
    assert _ids(public_trace.final_selected_chunks) == ["public::0"]
    assert public_trace.applied_filters["access_prefilter"]["reason"] == "public_only"

    bypass_trace = pipeline.retrieve("needle", "hybrid", access_scope=None)
    assert dense.allowed_calls[-1] is None
    assert sparse.allowed_calls[-1] is None
    assert set(_ids(bypass_trace.final_selected_chunks)) == {"private::0", "public::0"}
    assert bypass_trace.applied_filters["access_prefilter"]["reason"] == "explicit_bypass"

    private_dense = RecordingRetriever([private], "dense_score")
    private_sparse = RecordingRetriever([private], "sparse_score")
    private_pipeline = RetrievalPipeline(private_dense, private_sparse, ReciprocalRankFusion())
    denied_trace = private_pipeline.retrieve("needle", "hybrid", access_scope={})
    assert private_dense.allowed_calls == [set()]
    assert private_sparse.allowed_calls == [set()]
    assert denied_trace.final_selected_chunks == []


def test_graph_expansion_excludes_private_related_chunk_for_public_scope():
    public = _chunk("public::0", "policy", public=True, mindgraph_id="public-note")
    private = _chunk("private::0", "secret related policy", mindgraph_id="private-note")
    chunks = [public, private]
    base = RetrievalPipeline(
        RecordingRetriever(chunks, "dense_score"),
        RecordingRetriever(chunks, "sparse_score"),
        ReciprocalRankFusion(),
        final_top_k=5,
    )
    graph_store = type(
        "GraphStore",
        (),
        {
            "related_note_ids": staticmethod(lambda _ids: [{
                "source_note_id": "public-note",
                "target_note_id": "private-note",
                "relation_type": "references",
                "confidence": 0.9,
            }]),
            "note_titles": staticmethod(lambda _ids: {
                "public-note": "Public",
                "private-note": "Private",
            }),
        },
    )()
    pipeline = MindGraphRetrievalPipeline(base, graph_store)

    trace = pipeline.retrieve("policy", "hybrid", access_scope={})

    assert _ids(trace.final_selected_chunks) == ["public::0"]
    assert trace.candidate_counts["graph_expanded"] == 0
    assert trace.graph_links == []


def test_duplicate_dense_chunk_ids_fail_before_any_retrieval_stage():
    private = _chunk("collision::0", "private-secret-body")
    public = _chunk("collision::0", "public body", public=True)
    dense = RecordingRetriever([private, public], "dense_score")
    sparse = RecordingRetriever([private, public], "sparse_score")
    reranker = RecordingReranker()

    fusion = RecordingFusion()
    pipeline = RetrievalPipeline(dense, sparse, fusion, reranker)

    with pytest.raises(ValueError, match="duplicate") as raised:
        pipeline.retrieve("body", "hybrid_rerank", access_scope={})

    assert dense.allowed_calls == []
    assert sparse.allowed_calls == []
    assert fusion.calls == 0
    assert reranker.seen_ids == []
    assert "collision::0" not in str(raised.value)
    assert "private-secret-body" not in str(raised.value)


def test_duplicate_sparse_chunk_ids_fail_before_any_retrieval_stage():
    dense_chunk = _chunk("dense::0", "public body", public=True)
    sparse_private = _chunk("sparse-collision::0", "private-secret-body")
    sparse_public = _chunk("sparse-collision::0", "public body", public=True)
    dense = RecordingRetriever([dense_chunk], "dense_score")
    sparse = RecordingRetriever([sparse_private, sparse_public], "sparse_score")
    fusion = RecordingFusion()
    reranker = RecordingReranker()
    pipeline = RetrievalPipeline(dense, sparse, fusion, reranker)

    with pytest.raises(ValueError, match="duplicate") as raised:
        pipeline.retrieve("body", "hybrid_rerank", access_scope={})

    assert dense.allowed_calls == []
    assert sparse.allowed_calls == []
    assert fusion.calls == 0
    assert reranker.seen_ids == []
    assert "sparse-collision::0" not in str(raised.value)
    assert "private-secret-body" not in str(raised.value)


def test_cross_retriever_chunk_identity_conflict_fails_before_trace_stages():
    dense_public = _chunk("shared::0", "public body", public=True)
    sparse_private = _chunk("shared::0", "private-secret-body")
    dense = RecordingRetriever([dense_public], "dense_score")
    sparse = RecordingRetriever([sparse_private], "sparse_score")
    fusion = RecordingFusion()
    reranker = RecordingReranker()
    pipeline = RetrievalPipeline(dense, sparse, fusion, reranker)

    with pytest.raises(ValueError, match="conflict") as raised:
        pipeline.retrieve("body", "hybrid_rerank", access_scope={})

    assert dense.allowed_calls == []
    assert sparse.allowed_calls == []
    assert fusion.calls == 0
    assert reranker.seen_ids == []
    assert "shared::0" not in str(raised.value)
    assert "private-secret-body" not in str(raised.value)
