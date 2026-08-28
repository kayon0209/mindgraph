from __future__ import annotations

from pathlib import Path
from typing import Any

from config import DOCS_DIR, ROOT, UPLOAD_DIR
from infrastructure.settings import get_settings
from retrieval.embeddings import BGEEmbeddingProvider
from retrieval.fusion import ReciprocalRankFusion
from retrieval.indexing import load_corpus, load_current_index
from retrieval.mindgraph_pipeline import MindGraphRetrievalPipeline
from retrieval.pipeline import RetrievalPipeline
from retrieval.reranker import CrossEncoderReranker
from retrieval.sparse import BM25Retriever


INDEX_ROOT = ROOT / "data" / "retrieval_indexes"


def _rerank_top_n() -> int:
    # 统一从 settings 读取（进程环境变量 > .env > 默认），避免 os.getenv
    # 双配置源导致的"裸机启动不生效"问题。
    value = int(get_settings().RERANK_TOP_N)
    if value < 1:
        raise ValueError("RERANK_TOP_N must be a positive integer")
    return value


def create_retrieval_pipeline(final_top_k: int = 5) -> RetrievalPipeline:
    settings = get_settings()
    provider = BGEEmbeddingProvider()
    dense = load_current_index(provider, INDEX_ROOT)
    chunks = dense.chunks
    sparse = BM25Retriever(chunks, float(settings.BM25_K1), float(settings.BM25_B))
    reranker = CrossEncoderReranker() if settings.RERANKER_ENABLED else None
    return RetrievalPipeline(
        dense, sparse, ReciprocalRankFusion(int(settings.RRF_CONSTANT)), reranker,
        candidate_count=int(settings.RETRIEVAL_CANDIDATE_COUNT),
        rerank_top_n=_rerank_top_n(), final_top_k=final_top_k,
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
) -> "MindGraphRetrievalPipeline":
    """构建 MindGraph 检索管线（Hybrid + 图谱一跳扩展）。

    - 索引已构建：加载当前版本（load_current_index 兼容 MindGraph 索引结构）；
    - 索引尚未构建：返回空管线，问答将自然返回 insufficient_evidence，不崩溃。
    """
    candidate_count = int(get_settings().RETRIEVAL_CANDIDATE_COUNT)
    rerank_top_n = _rerank_top_n()
    settings = get_settings()
    reranker = CrossEncoderReranker() if settings.RERANKER_ENABLED else None

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
        )
        return MindGraphRetrievalPipeline(base, graph_store, graph_enabled=graph_enabled)

    dense = load_current_index(BGEEmbeddingProvider(), index_root)
    chunks = dense.chunks
    sparse = BM25Retriever(chunks, float(settings.BM25_K1), float(settings.BM25_B))
    base = RetrievalPipeline(
        dense, sparse, ReciprocalRankFusion(int(settings.RRF_CONSTANT)), reranker,
        candidate_count=candidate_count,
        rerank_top_n=rerank_top_n, final_top_k=final_top_k,
    )
    return MindGraphRetrievalPipeline(base, graph_store, graph_enabled=graph_enabled)
