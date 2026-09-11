"""数据源命名空间隔离（source_ids）回归测试。

背景：`knowledge/` 这一个 vault 根下同时存着自建中文制度与
`external/public/` 的英文公开手册，同步时共用一个 `source_id`
（`vault_sync_service.py:202` 用 `path_prefix or vault 根`），检索侧此前只能
按 workspace 级 access_scope 过滤 —— 两套内容混在一个池子里且无法区分。
本文件锁住新加的这一层过滤：默认不生效（旧行为），显式指定才裁剪，
且图扩展补料不能绕过它。
"""
from __future__ import annotations

from types import SimpleNamespace

from retrieval.fusion import ReciprocalRankFusion
from retrieval.mindgraph_pipeline import MindGraphRetrievalPipeline
from retrieval.pipeline import RetrievalPipeline, filter_candidates_by_source
from retrieval.types import Chunk, RetrievalCandidate, RetrievalTrace

# 命名空间占位符：不写本机绝对路径，clone 到任何机器上结果一致。
# 真实环境下 `source_id` 形如 `<vault_root>/knowledge`；这里只需要保住
# 「两套语料共享同一个 vault 根、子目录另属一套」这一结构。
ZH_SOURCE = "knowledge"
EXT_SOURCE = "knowledge/external/public"

# 中文自建制度与英文公开手册：同一个 vault 根下的两套内容
ZH_CHUNK = Chunk(
    "zh::0", "差旅费报销时限为十个工作日", "费用报销管理制度.md", 0, None,
    {
        "document_status": "active", "effective_date": "2026-01-01",
        "knowledge_category": "policy", "source_id": ZH_SOURCE,
    },
)
EXT_CHUNK = Chunk(
    "ext::0", "GitLab travel expense policy: submit within 30 days", "gitlab-travel-expense.md", 0, None,
    {
        "document_status": "active", "effective_date": "2026-01-01",
        "knowledge_category": "policy", "source_id": EXT_SOURCE,
    },
)


class _FakeRetriever:
    """只声明真实管线下传的形参，行为可预期且与 embedding 无关。"""

    def __init__(self, candidates: list[RetrievalCandidate], metadata: dict | None = None) -> None:
        self._candidates = candidates
        self.metadata = metadata or {}
        self.chunks = [item.chunk for item in candidates]

    def search(self, query, top_k, access_scope=None, query_date=None, categories=None, include_historical=False):
        # 真实检索器返回 (候选列表, 分阶段耗时)，管线按元组解包
        return self._candidates, {"fake_ms": 0.1}


def _candidates(*chunks: Chunk) -> list[RetrievalCandidate]:
    return [RetrievalCandidate(chunk=chunk, dense_score=1.0, sparse_score=1.0) for chunk in chunks]


def _hybrid_pipeline(*chunks: Chunk) -> RetrievalPipeline:
    candidates = _candidates(*chunks)
    return RetrievalPipeline(
        dense=_FakeRetriever(candidates, {"index_version": "test"}),
        sparse=_FakeRetriever(candidates),
        fusion=ReciprocalRankFusion(60),
        reranker=None,
        candidate_count=10,
        final_top_k=5,
    )


# ── 纯函数层 ──────────────────────────────────────────────────────────────

def test_no_source_ids_keeps_legacy_behaviour():
    trace = RetrievalTrace(query="q", requested_strategy="hybrid", actual_strategy="hybrid")
    candidates = _candidates(ZH_CHUNK, EXT_CHUNK)

    assert filter_candidates_by_source(candidates, None, trace) == candidates
    assert filter_candidates_by_source(candidates, [], trace) == candidates
    assert trace.warnings == []


def test_exact_source_id_selects_only_that_source():
    trace = RetrievalTrace(query="q", requested_strategy="hybrid", actual_strategy="hybrid")
    kept = filter_candidates_by_source(_candidates(ZH_CHUNK, EXT_CHUNK), [ZH_SOURCE], trace)

    assert [item.chunk.chunk_id for item in kept] == ["zh::0"]
    assert "source_filtered_chunks" in trace.warnings


def test_subpath_token_matches_without_absolute_path():
    """允许用 `external/public` 这样的子路径定位，不必写绝对路径。"""
    trace = RetrievalTrace(query="q", requested_strategy="hybrid", actual_strategy="hybrid")
    kept = filter_candidates_by_source(_candidates(ZH_CHUNK, EXT_CHUNK), ["external/public"], trace)

    assert [item.chunk.chunk_id for item in kept] == ["ext::0"]


def test_forward_slash_and_token_normalisation_are_equivalent():
    trace = RetrievalTrace(query="q", requested_strategy="hybrid", actual_strategy="hybrid")
    kept = filter_candidates_by_source(_candidates(ZH_CHUNK, EXT_CHUNK), ["knowledge/external/public/"], trace)

    assert [item.chunk.chunk_id for item in kept] == ["ext::0"]


def test_match_by_source_path_when_source_id_is_a_shared_vault_root():
    """本机真实情形：25 篇笔记的 `source_id` 全是同一个 vault 根。

    只认 `source_id` 时，"按源隔离"实际只能整体放行或整体拒绝；真正能区分
    「自建中文制度」与「external/public 英文手册」的是 `source_path` 与 `workspace`。
    """
    shared_root = ZH_SOURCE
    zh = Chunk("zh::0", "差旅费报销时限为十个工作日", "差旅费报销管理办法.md", 0, None,
               {"source_id": shared_root, "source_path": "差旅费报销管理办法.md", "workspace": "knowledge"})
    ext = Chunk("ext::0", "GitLab travel expense policy", "gitlab.md", 0, None,
                {"source_id": shared_root, "source_path": "external/public/gitlab.md", "workspace": "public"})

    def _kept(token: str) -> list[str]:
        trace = RetrievalTrace(query="q", requested_strategy="hybrid", actual_strategy="hybrid")
        return [item.chunk.chunk_id for item in filter_candidates_by_source(_candidates(zh, ext), [token], trace)]

    assert _kept("差旅费报销管理办法.md") == ["zh::0"]  # 文档相对路径
    assert _kept("external/public") == ["ext::0"]  # 子目录路径（目录前缀匹配）
    assert _kept("public") == ["ext::0"]  # workspace 维度
    # "knowledge" 既是 vault 根标签本身，也是 zh 的 workspace —— 两篇同源，
    # 因此两篇都保留：这是「按 vault 根过滤」的预期粗粒度语义，不是泄漏。
    assert sorted(_kept("knowledge")) == ["ext::0", "zh::0"]

    # vault 根作为 token：两篇同源，都保留（粗粒度但语义正确）
    trace = RetrievalTrace(query="q", requested_strategy="hybrid", actual_strategy="hybrid")
    both = filter_candidates_by_source(_candidates(zh, ext), [shared_root], trace)
    assert {item.chunk.chunk_id for item in both} == {"zh::0", "ext::0"}


def test_unattributed_chunks_are_dropped_and_warned():
    """无 source_id 的历史数据不能被「隔离」静默穿透。"""
    legacy = Chunk(
        "legacy::0", "旧制度正文", "legacy.md", 0, None,
        {"document_status": "active", "effective_date": "2026-01-01"},
    )
    trace = RetrievalTrace(query="q", requested_strategy="hybrid", actual_strategy="hybrid")
    kept = filter_candidates_by_source(_candidates(ZH_CHUNK, legacy), [ZH_SOURCE], trace)

    assert [item.chunk.chunk_id for item in kept] == ["zh::0"]
    assert "source_unattributed_dropped:1" in trace.warnings


# ── 管线层 ────────────────────────────────────────────────────────────────

def test_hybrid_retrieve_without_source_ids_returns_both_sources():
    """默认路径必须与加过滤前完全一致（不裁剪任何来源）。"""
    pipeline = _hybrid_pipeline(ZH_CHUNK, EXT_CHUNK)
    trace = pipeline.retrieve("差旅费报销", "hybrid")

    assert {item.chunk.chunk_id for item in trace.final_selected_chunks} == {"zh::0", "ext::0"}
    assert trace.applied_filters["source_ids"] == []


def test_hybrid_retrieve_honours_source_ids():
    pipeline = _hybrid_pipeline(ZH_CHUNK, EXT_CHUNK)
    trace = pipeline.retrieve("差旅费报销", "hybrid", source_ids=["external/public"])

    assert [item.chunk.chunk_id for item in trace.final_selected_chunks] == ["ext::0"]
    assert trace.applied_filters["source_ids"] == ["external/public"]
    # 候选阶段就裁掉，避免融合池里混入另一套内容
    assert [item.chunk.chunk_id for item in trace.dense_results] == ["ext::0"]
    assert [item.chunk.chunk_id for item in trace.sparse_results] == ["ext::0"]


def test_source_ids_survives_bm25_and_dense_strategies():
    pipeline = _hybrid_pipeline(ZH_CHUNK, EXT_CHUNK)

    dense_trace = pipeline.retrieve("q", "dense", source_ids=[ZH_SOURCE])
    bm25_trace = pipeline.retrieve("q", "bm25", source_ids=[ZH_SOURCE])

    assert [item.chunk.chunk_id for item in dense_trace.final_selected_chunks] == ["zh::0"]
    assert [item.chunk.chunk_id for item in bm25_trace.final_selected_chunks] == ["zh::0"]


# ── 图扩展不得绕过隔离 ────────────────────────────────────────────────────

class _StubBasePipeline:
    """不带 source_ids 形参的替身：验证兜底过滤路径。"""

    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks

    @property
    def dense(self):
        return self

    def retrieve(self, query, strategy, query_date=None, categories=None, include_historical=False, access_scope=None):
        return RetrievalTrace(
            query=query,
            requested_strategy=strategy,
            actual_strategy=strategy,
            final_selected_chunks=[RetrievalCandidate(chunk=self.chunks[0], final_rank=1)],
            applied_filters={"access_scope": access_scope},
        )


class _FakeGraphStore:
    def related_note_ids(self, note_ids, hops=1, access_scope=None, **kwargs):
        return [
            {
                "relation_id": "rel-1",
                "source_note_id": "n-zh",
                "target_note_id": "n-ext",
                "relation_type": "related_to",
                "direction": "outgoing",
                "status": "confirmed",
                "evidence_chunk_id": "ext::0",
                "confidence": 0.9,
            }
        ]

    def note_titles(self, note_ids):
        return {note_id: f"title-{note_id}" for note_id in note_ids}


def test_graph_expansion_cannot_leak_across_sources():
    """命中中文制度后图扩展拉到英文手册邻居 —— 指定 source_ids 时必须收口。"""
    zh_root = Chunk(
        "zh::0", "差旅费报销时限为十个工作日", "费用报销管理制度.md", 0, None,
        {"mindgraph_id": "n-zh", "source_id": ZH_SOURCE, "document_status": "active", "knowledge_category": "policy"},
    )
    ext_neighbour = Chunk(
        "ext::0", "GitLab travel expense policy", "gitlab-travel-expense.md", 0, None,
        {"mindgraph_id": "n-ext", "source_id": EXT_SOURCE, "document_status": "active", "knowledge_category": "policy"},
    )
    base = _StubBasePipeline([zh_root, ext_neighbour])
    pipeline = MindGraphRetrievalPipeline(base, _FakeGraphStore(), graph_enabled=True, max_graph_chunks=2)

    unfiltered = pipeline.retrieve("差旅费报销", "hybrid", graph_enabled=True)
    filtered = pipeline.retrieve("差旅费报销", "hybrid", graph_enabled=True, source_ids=[ZH_SOURCE])

    # 不指定：图扩展照旧把英文邻居补进来（旧行为不变）
    assert {c.chunk.chunk_id for c in unfiltered.final_selected_chunks} == {"zh::0", "ext::0"}
    # 指定后：补料被裁掉，且最终排名重排为连续序号
    assert [c.chunk.chunk_id for c in filtered.final_selected_chunks] == ["zh::0"]
    assert [c.final_rank for c in filtered.final_selected_chunks] == [1]
    assert filtered.applied_filters["source_ids"] == [ZH_SOURCE]


def test_chat_style_signature_probe_still_works():
    """chat_service 用 inspect.signature 决定是否下传参数，形参名必须稳定。"""
    pipeline = _hybrid_pipeline(ZH_CHUNK)
    assert "source_ids" in SimpleNamespace(f=pipeline.retrieve).f.__func__.__code__.co_varnames
    assert "source_ids" in MindGraphRetrievalPipeline.retrieve.__code__.co_varnames
