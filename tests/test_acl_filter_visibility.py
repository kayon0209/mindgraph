"""ACL 过滤可观测性：access_scope 把候选全裁掉时必须留下线索。

真实成因（本机活索引实测）：检索索引由文件构建（`load_corpus` →
`document_loader.load_markdown_chunks`），chunk metadata 只有
`doc_name / section_path / chunk_index / source / origin`，**没有**
`acl_json` / `workspace` / `department`。于是 `chunk_acl_matches` 对每个
chunk 都判 False，任何非通配的 access_scope 都会让问答返回「无证据」——
而修复前这条路径完全静默（trace.warnings 为空），无从归因。
"""
from __future__ import annotations

from types import SimpleNamespace

from retrieval.pipeline import RetrievalPipeline
from retrieval.sparse import BM25Retriever
from retrieval.types import Chunk

NO_ACL_METADATA = Chunk(
    "policy.md::0", "差旅费报销时限为十个工作日", "policy.md", 0, None,
    {"document_status": "active"},
)


def _pipeline(chunks: list[Chunk]) -> RetrievalPipeline:
    return RetrievalPipeline(
        dense=SimpleNamespace(metadata={}),
        sparse=BM25Retriever(chunks),
        fusion=SimpleNamespace(),
        reranker=None,
        candidate_count=10,
        final_top_k=5,
    )


def test_non_wildcard_scope_without_acl_metadata_is_reported():
    trace = _pipeline([NO_ACL_METADATA]).retrieve(
        "差旅费报销", "bm25",
        access_scope={"allow": ["user:alice"], "deny": [], "user": "alice", "roles": ["read"]},
    )

    assert trace.final_selected_chunks == []
    assert "acl_dropped_chunks:1" in trace.warnings
    assert "acl_filtered_all_candidates" in trace.warnings


def test_wildcard_scope_is_not_reported_as_acl_filtered():
    """allow=["*"] 是显式放行，不该被误报成 ACL 裁剪。"""
    trace = _pipeline([NO_ACL_METADATA]).retrieve(
        "差旅费报销", "bm25", access_scope={"allow": ["*"], "deny": []},
    )

    assert [c.chunk.chunk_id for c in trace.final_selected_chunks] == ["policy.md::0"]
    assert not [w for w in trace.warnings if w.startswith("acl_dropped_chunks")]


def test_no_scope_means_no_acl_signal():
    trace = _pipeline([NO_ACL_METADATA]).retrieve("差旅费报销", "bm25")

    assert [c.chunk.chunk_id for c in trace.final_selected_chunks] == ["policy.md::0"]
    assert not [w for w in trace.warnings if w.startswith("acl_")]


def test_partial_acl_drop_reports_count_without_all_candidates_warning():
    """只裁掉一部分时不应报「全被裁掉」。"""
    public_chunk = Chunk(
        "public.md::0", "差旅费报销时限为十个工作日", "public.md", 0, None,
        {"document_status": "active", "acl_public": True},
    )
    trace = _pipeline([public_chunk, NO_ACL_METADATA]).retrieve(
        "差旅费报销", "bm25",
        access_scope={"allow": ["user:alice"], "deny": [], "user": "alice", "roles": ["read"]},
    )

    assert [c.chunk.chunk_id for c in trace.final_selected_chunks] == ["public.md::0"]
    assert "acl_dropped_chunks:1" in trace.warnings
    assert "acl_filtered_all_candidates" not in trace.warnings
