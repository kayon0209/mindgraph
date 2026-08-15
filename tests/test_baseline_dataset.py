import unittest

from evaluation.baseline import current_chunk_catalog, load_dataset, summarize


class BaselineDatasetTests(unittest.TestCase):
    def test_dataset_has_fixed_splits_and_valid_gold_labels(self):
        cases = load_dataset()
        self.assertEqual(len(cases), 34)
        self.assertEqual({case["split"] for case in cases}, {"development", "regression"})
        self.assertEqual(sum(case["split"] == "development" for case in cases), 20)
        self.assertEqual(sum(case["split"] == "regression" for case in cases), 14)
        self.assertTrue(current_chunk_catalog())


    def test_summary_ignores_nullable_metrics(self):
        detail = {
            "category": "x",
            "split": "development",
            "metrics": {
                "retrieval": {name: 1.0 for name in ("recall_at_1", "recall_at_3", "recall_at_5", "reciprocal_rank", "document_hit", "chunk_hit")},
                "generation": {
                    "answer_correctness": 1.0,
                    "citation_accuracy": None,
                    "evidence_consistency": None,
                    "completeness": 1.0,
                    "no_answer_accuracy": None,
                    "refusal_accuracy": None,
                },
                "system": {"stages": {name: None if name == "ttft_ms" else 1.0 for name in ("query_embedding_ms", "vector_search_ms", "context_build_ms", "generation_ms", "ttft_ms", "total_latency_ms")}},
            },
        }
        summary = summarize([detail])
        self.assertIsNone(summary["overall"]["generation"]["citation_accuracy"])
        self.assertIsNone(summary["overall"]["system"]["ttft_ms"])


if __name__ == "__main__":
    unittest.main()
