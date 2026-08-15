from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from application.evaluation_governance_service import EvaluationGovernanceService
from domain.errors import ConflictError
from infrastructure.database import ProductDatabase


class GovernanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.database = ProductDatabase(Path(self.temp.name) / "product.sqlite3")
        self.database.initialize()
        self.service = EvaluationGovernanceService(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def test_dataset_types_immutability_and_holdout_label_isolation(self):
        dataset = self.service.register_dataset("holdout", "0.1", "holdout", "future", [], "incomplete", "structure")
        self.assertEqual(dataset["case_count"], 0)
        with self.assertRaises(ConflictError):
            self.service.register_dataset("holdout", "0.1", "holdout", "changed", [], "incomplete", "overwrite")
        with self.assertRaises(ValueError):
            self.service.register_dataset("bad", "1", "training", "bad", [], "draft", "bad")
        self.service.annotate("holdout", "0.1", "case-1", {"gold_chunk_ids": ["secret"]}, "reviewer", "approved")
        self.assertEqual(self.service.list_annotations("holdout", "0.1"), [])
        self.assertEqual(len(self.service.list_annotations("holdout", "0.1", True)), 1)

    def test_annotation_prompt_review_judge_threshold_and_ablation(self):
        self.service.register_dataset("dev", "1", "development", "tuning", [{"category": "rule"}], "reviewed", "initial")
        annotation = self.service.annotate("dev", "1", "1", {"required_facts": ["10 days"]}, "alice", "reviewed")
        self.assertEqual(annotation["review_status"], "reviewed")
        prompt = self.service.create_prompt("answer", "1", "Use evidence", "initial")
        self.assertEqual(len(prompt["checksum"]), 64)
        with self.assertRaises(ConflictError):
            self.service.create_prompt("answer", "1", "Overwrite", "bad")
        scores = {name: 1 for name in ("correctness", "completeness", "citation_support", "evidence_consistency", "actionability", "refusal_appropriateness")}
        review = self.service.add_human_review("run", "1", "alice", scores, "supported")
        self.assertTrue(review["single_reviewer"])
        judge = self.service.validate_judge_result({"correctness": 1, "completeness": 1, "citation_support": 1, "evidence_consistency": 1, "refusal_appropriate": True, "reason": "ok"})
        self.assertEqual(judge.correctness, 1)
        with self.assertRaises(ValidationError):
            self.service.validate_judge_result({"correctness": 2})
        result = self.service.threshold_experiment([
            {"score": 0.9, "correct": True, "answerable": True},
            {"score": 0.2, "correct": False, "answerable": False},
        ], [0.5])[0]
        self.assertEqual(result["answer_coverage"], 0.5)
        self.assertEqual(result["correct_refusal_rate"], 0.5)
        self.assertTrue(self.service.validate_ablation({"final_top_k": [3, 5]})["freeze_unlisted_variables"])
        with self.assertRaises(ValueError):
            self.service.validate_ablation({"secret_knob": [1]})


if __name__ == "__main__":
    unittest.main()
