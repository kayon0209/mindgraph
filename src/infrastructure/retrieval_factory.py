from __future__ import annotations

from pathlib import Path
from typing import Any

from config import ROOT
from infrastructure.settings import get_settings
from retrieval.embeddings import BGEEmbeddingProvider
from retrieval.fusion import ReciprocalRankFusion
from retrieval.indexing import load_current_index
from retrieval.mindgraph_pipeline import MindGraphRetrievalPipeline
from retrieval.pipeline import RetrievalPipeline
from retrieval.reranker import CrossEncoderReranker
from retrieval.sparse import BM25Retriever

INDEX_ROOT = ROOT / "data" / "retrieval_indexes"
# MindGraph 索引根与通用检索索引根是**两个命名空间**（见 application/index_snapshot.py
# 的"不跨根比较"约定）：`retrieval_indexes` 索引 knowledge/ 语料（4 篇制度），
# `mindgraph_indexes` 索引 vault 笔记（25 篇，与 notes 表同源）。
# 启动自检 `_warn_if_index_diverges` 的对照基准是 notes 表，必须用这一个——
# 用错根会把"两套语料本就不同"误报成 `index_corpus_divergence` ERROR。
MINDGRAPH_INDEX_ROOT = ROOT / "data" / "mindgraph_indexes"


def _rerank_top_n() -> int:
    # 统一从 settings 读取（进程环境变量 > .env > 默认），避免 os.getenv
    # 双配置源导致的"裸机启动不生效"问题。
    value = int(get_settings().RERANK_TOP_N)
    if value < 1:
        raise ValueError("RERANK_TOP_N must be a positive integer")
    return value


def _build_reranker(settings) -> CrossEncoderReranker | None:
    """按 settings 构造精排器（关闭时返回 None → 管线标记 reranker_disabled 降级）。

    配置必须显式传进去：pydantic-settings 读 ``.env`` 但**不写回**
    ``os.environ``，所以精排器内部用 ``os.getenv`` 读 RERANKER_LOCAL_PATH
    在裸机启动时会拿不到值，表现为"权重明明放好了却仍整批降级"。
    """
    if not settings.RERANKER_ENABLED:
        return None
    return CrossEncoderReranker(
        model_name=settings.RERANKER_MODEL_NAME or None,
        local_files_only=bool(settings.RERANKER_LOCAL_FILES_ONLY),
        local_path=settings.RERANKER_LOCAL_PATH or None,
    )


def _context_expansion_kwargs() -> dict[str, Any]:
    """PR-09：parent 上下文扩展开关（默认关）。m4 上传文档索引的 chunk 带
    parent lineage 时才有可观察效果；mg-/m3- 扁平块自动走 no_parent_lineage。"""
    settings = get_settings()
    return {
        "context_expansion": bool(settings.CONTEXT_EXPANSION_ENABLED),
        "context_expansion_max_chars": int(settings.CONTEXT_EXPANSION_MAX_CHARS),
        "conditional_rerank": bool(settings.CONDITIONAL_RERANK_ENABLED),
    }


def create_retrieval_pipeline(final_top_k: int = 5) -> RetrievalPipeline:
    settings = get_settings()
    provider = BGEEmbeddingProvider()
    dense = load_current_index(provider, INDEX_ROOT)
    chunks = dense.chunks
    sparse = BM25Retriever(chunks, float(settings.BM25_K1), float(settings.BM25_B))
    reranker = _build_reranker(settings)
    return RetrievalPipeline(
        dense, sparse, ReciprocalRankFusion(int(settings.RRF_CONSTANT)), reranker,
        candidate_count=int(settings.RETRIEVAL_CANDIDATE_COUNT),
        rerank_top_n=_rerank_top_n(), final_top_k=final_top_k,
        **_context_expansion_kwargs(),
    )


class _EmptyDenseRetriever:
    """索引尚未构建时的占位 dense 检索器（search 返回空）。"""

    chunks: list = []
    metadata: dict = {}

    def search(self, query, top_k):
        return [], {}


def create_mindgraph_retrieval_pipeline(
    index_root: Path,
    graph_store: Any,
    final_top_k: int = 5,
    graph_enabled: bool = False,
) -> MindGraphRetrievalPipeline:
    """构建 MindGraph 检索管线（Hybrid + 图谱一跳扩展）。

    - 索引已构建：加载当前版本（load_current_index 兼容 MindGraph 索引结构）；
    - 索引尚未构建：返回空管线，问答将自然返回 insufficient_evidence，不崩溃。
    """
    candidate_count = int(get_settings().RETRIEVAL_CANDIDATE_COUNT)
    rerank_top_n = _rerank_top_n()
    settings = get_settings()
    reranker = _build_reranker(settings)

    current = index_root / "CURRENT"
    if not current.exists():
        base = RetrievalPipeline(
            _EmptyDenseRetriever(),
            BM25Retriever([], float(settings.BM25_K1), float(settings.BM25_B)),
            ReciprocalRankFusion(int(settings.RRF_CONSTANT)),
            None,
            candidate_count=candidate_count,
            rerank_top_n=rerank_top_n,
            final_top_k=final_top_k,
            **_context_expansion_kwargs(),
        )
        return MindGraphRetrievalPipeline(base, graph_store, graph_enabled=graph_enabled)

    dense = load_current_index(BGEEmbeddingProvider(), index_root)
    chunks = dense.chunks
    sparse = BM25Retriever(chunks, float(settings.BM25_K1), float(settings.BM25_B))
    base = RetrievalPipeline(
        dense, sparse, ReciprocalRankFusion(int(settings.RRF_CONSTANT)), reranker,
        candidate_count=candidate_count,
        rerank_top_n=rerank_top_n, final_top_k=final_top_k,
        **_context_expansion_kwargs(),
    )
    return MindGraphRetrievalPipeline(base, graph_store, graph_enabled=graph_enabled)
