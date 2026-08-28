import tempfile
import unittest
from pathlib import Path

from retrieval.dense import FAISSDenseRetriever, IncompatibleIndexError
from retrieval.fusion import ReciprocalRankFusion
from retrieval.pipeline import RetrievalPipeline
from retrieval.sparse import BM25Retriever, tokenize_zh
from retrieval.types import Chunk, RetrievalCandidate


CHUNKS = [
    Chunk("policy.md::0", "差旅费报销时限为十个工作日", "policy.md", 0, "时限"),
    Chunk("policy.md::1", "普通员工飞机标准为经济舱", "policy.md", 1, "交通"),
    Chunk("materials.md::0", "电子发票须打印后粘贴", "materials.md", 0, "发票"),
]


class FakeEmbeddingProvider:
    model_name = "fake-bge"
    model_revision = "test"

    def __init__(self, dimension=3):
        self._dimension = dimension

    @property
    def dimension(self):
        return self._dimension

    def embed_documents(self, texts):
        return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]

    def embed_query(self, text):
        return [1.0, 0.0, 0.0]


class RetrievalTests(unittest.TestCase):
    def test_chinese_tokenizer_includes_unigrams_and_bigrams(self):
        tokens = tokenize_zh("电子发票 policy-123")
        self.assertIn("发票", tokens)
        self.assertIn("policy", tokens)

    def test_bm25_exact_terms_empty_query_and_top_k(self):
        retriever = BM25Retriever(CHUNKS)
        results, _ = retriever.search("电子发票", 1)
        self.assertEqual(results[0].chunk.chunk_id, "materials.md::0")
        self.assertEqual(retriever.search("", 5)[0], [])
        self.assertLessEqual(len(retriever.search("报销", 2)[0]), 2)

    def test_bm25_policy_term_and_paraphrase_overlap(self):
        retriever = BM25Retriever(CHUNKS)
        self.assertEqual(retriever.search("经济舱标准", 1)[0][0].chunk.chunk_id, "policy.md::1")
        self.assertEqual(retriever.search("多久必须报完差旅费", 1)[0][0].chunk.chunk_id, "policy.md::0")

    def test_rrf_is_deterministic_and_preserves_stage_scores(self):
        dense = [RetrievalCandidate(CHUNKS[0], dense_score=0.9, dense_rank=1), RetrievalCandidate(CHUNKS[1], dense_score=0.8, dense_rank=2)]
        sparse = [RetrievalCandidate(CHUNKS[1], sparse_score=5.0, sparse_rank=1), RetrievalCandidate(CHUNKS[0], sparse_score=4.0, sparse_rank=2)]
        fused = ReciprocalRankFusion(60).fuse([dense, sparse], 2)
        self.assertEqual([item.chunk.chunk_id for item in fused], ["policy.md::0", "policy.md::1"])
        self.assertIsNotNone(fused[0].dense_score)
        self.assertIsNotNone(fused[0].sparse_score)

    def test_faiss_build_reload_metadata_top_k_and_empty_query(self):
        with tempfile.TemporaryDirectory() as directory:
            retriever = FAISSDenseRetriever(FakeEmbeddingProvider(), Path(directory))
            retriever.build(CHUNKS, {"chunk_size": 500, "chunk_overlap": 50})
            loaded = FAISSDenseRetriever(FakeEmbeddingProvider(), Path(directory))
            loaded.load()
            results, _ = loaded.search("时限", 2)
            self.assertEqual(results[0].chunk.chunk_id, "policy.md::0")
            self.assertEqual(len(results), 2)
            self.assertEqual(loaded.search("", 5)[0], [])
            self.assertEqual(loaded.metadata["vector_dimension"], 3)

    def test_faiss_rejects_incompatible_embedding_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            retriever = FAISSDenseRetriever(FakeEmbeddingProvider(), Path(directory))
            retriever.build(CHUNKS, {"chunk_size": 500, "chunk_overlap": 50})
            incompatible = FAISSDenseRetriever(FakeEmbeddingProvider(dimension=4), Path(directory))
            with self.assertRaises(IncompatibleIndexError):
                incompatible.load()

    def test_pipeline_records_explicit_reranker_degradation(self):
        class Dense:
            def search(self, query, top_k):
                return [RetrievalCandidate(CHUNKS[0], dense_score=1, dense_rank=1)], {"dense_retrieval_ms": 1.0}
        class Sparse:
            def search(self, query, top_k):
                return [RetrievalCandidate(CHUNKS[0], sparse_score=1, sparse_rank=1)], {"bm25_retrieval_ms": 1.0}
        class BrokenReranker:
            model_name = "broken"
            def rerank(self, query, candidates, top_k):
                raise RuntimeError("unavailable")
        pipeline = RetrievalPipeline(Dense(), Sparse(), ReciprocalRankFusion(), BrokenReranker())
        trace = pipeline.retrieve("test", "hybrid_rerank")
        self.assertTrue(trace.degraded)
        self.assertEqual(trace.actual_strategy, "hybrid")
        self.assertIn("unavailable", trace.degradation_reason)


if __name__ == "__main__":
    unittest.main()


def test_invalid_date_metadata_is_tolerated_instead_of_raising():
    """P2-5 回归：非 ISO 日期元数据不得让检索 503，按缺省日期处理并告警。"""
    from datetime import date
    from types import SimpleNamespace

    from retrieval.types import RetrievalTrace

    bad_chunk = Chunk(
        "legacy.md::0", "老制度报销时限", "legacy.md", 0, None,
        {"document_status": "active", "effective_date": "2026/07/01"},
    )
    good_chunk = Chunk(
        "policy.md::0", "现行制度报销时限", "policy.md", 0, None,
        {"document_status": "active", "effective_date": "2026-01-01"},
    )
    pipeline = RetrievalPipeline(
        dense=SimpleNamespace(metadata={}), sparse=SimpleNamespace(),
        fusion=SimpleNamespace(), reranker=None,
    )
    trace = RetrievalTrace(query="q", requested_strategy="hybrid", actual_strategy="hybrid")
    candidates = [
        RetrievalCandidate(chunk=bad_chunk, rrf_score=1.0),
        RetrievalCandidate(chunk=good_chunk, rrf_score=0.9),
    ]

    # 此前这里会抛 date.fromisoformat ValueError；现在应容错并继续
    selected = pipeline._filter_and_adjust(candidates, "2026-08-01", [], False, trace)

    assert {c.chunk.chunk_id for c in selected} == {"legacy.md::0", "policy.md::0"}
    assert "invalid_date_metadata_treated_as_missing" in trace.warnings
