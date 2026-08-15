import os
import sys
import unittest
from pathlib import Path

from retrieval.embeddings import BGEEmbeddingProvider
from retrieval.indexing import load_current_index
from retrieval.reranker import CrossEncoderReranker


@unittest.skipUnless(os.getenv("RUN_RETRIEVAL_INTEGRATION") == "true", "set RUN_RETRIEVAL_INTEGRATION=true")
class RealModelIntegrationTests(unittest.TestCase):
    def test_cached_bge_index_and_reranker(self):
        dense = load_current_index(BGEEmbeddingProvider(local_files_only=True), Path("data/retrieval_indexes"))
        candidates, _ = dense.search("差旅费报销时限", 3)
        self.assertTrue(candidates)
        reranked = CrossEncoderReranker(local_files_only=True).rerank("差旅费报销时限", candidates, 2)
        self.assertEqual(len(reranked), 2)
        self.assertIsNotNone(reranked[0].reranker_score)


if __name__ == "__main__":
    unittest.main()
