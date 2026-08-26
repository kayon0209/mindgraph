from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from config import DOCS_DIR, ROOT, UPLOAD_DIR
from retrieval.embeddings import BGEEmbeddingProvider
from retrieval.fusion import ReciprocalRankFusion
from retrieval.indexing import load_corpus, load_current_index
from retrieval.mindgraph_pipeline import MindGraphRetrievalPipeline
from retrieval.pipeline import RetrievalPipeline
from retrieval.reranker import CrossEncoderReranker
from retrieval.sparse import BM25Retriever


INDEX_ROOT = ROOT / "data" / "retrieval_indexes"


def create_retrieval_pipeline(final_top_k: int = 5) -> RetrievalPipeline:
    provider = BGEEmbeddingProvider()
    dense = load_current_index(provider, INDEX_ROOT)
    chunks = dense.chunks
    sparse = BM25Retriever(chunks, float(os.getenv("BM25_K1", "1.5")), float(os.getenv("BM25_B", "0.75")))
    reranker = CrossEncoderReranker() if os.getenv("RERANKER_ENABLED", "false").lower() == "true" else None
    return RetrievalPipeline(
        dense, sparse, ReciprocalRankFusion(int(os.getenv("RRF_CONSTANT", "60"))), reranker,
        candidate_count=int(os.getenv("RETRIEVAL_CANDIDATE_COUNT", "20")),
        rerank_top_n=int(os.getenv("RETRANK_TOP_N", "10")), final_top_k=final_top_k,
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
    candidate_count = int(os.getenv("RETRIEVAL_CANDIDATE_COUNT", "20"))
    rerank_top_n = int(os.getenv("RETRANK_TOP_N", "10"))
    reranker = CrossEncoderReranker() if os.getenv("RERANKER_ENABLED", "false").lower() == "true" else None

    current = index_root / "CURRENT"
    if not current.exists():
        base = RetrievalPipeline(
            _EmptyDenseRetriever(),
            BM25Retriever([], float(os.getenv("BM25_K1", "1.5")), float(os.getenv("BM25_B", "0.75"))),
            ReciprocalRankFusion(int(os.getenv("RRF_CONSTANT", "60"))),
            None,
            candidate_count=candidate_count,
            rerank_top_n=rerank_top_n,
            final_top_k=final_top_k,
        )
        return MindGraphRetrievalPipeline(base, graph_store, graph_enabled=graph_enabled)

    dense = load_current_index(BGEEmbeddingProvider(), index_root)
    chunks = dense.chunks
    sparse = BM25Retriever(chunks, float(os.getenv("BM25_K1", "1.5")), float(os.getenv("BM25_B", "0.75")))
    base = RetrievalPipeline(
        dense, sparse, ReciprocalRankFusion(int(os.getenv("RRF_CONSTANT", "60"))), reranker,
        candidate_count=candidate_count,
        rerank_top_n=rerank_top_n, final_top_k=final_top_k,
    )
    return MindGraphRetrievalPipeline(base, graph_store, graph_enabled=graph_enabled)
