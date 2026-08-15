from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from domain.errors import ConflictError, NotFoundError
from infrastructure.database import ProductDatabase, dumps, loads


class JudgeResult(BaseModel):
    correctness: float = Field(ge=0, le=1)
    completeness: float = Field(ge=0, le=1)
    citation_support: float = Field(ge=0, le=1)
    evidence_consistency: float = Field(ge=0, le=1)
    refusal_appropriate: bool
    reason: str


class EvaluationGovernanceService:
    def __init__(self, database: ProductDatabase) -> None:
        self.database = database

    def register_dataset(self, dataset_id: str, version: str, dataset_type: str, purpose: str, cases: list[dict], annotation_status: str, change_note: str):
        if dataset_type not in {"development", "regression", "holdout", "adversarial"}: raise ValueError("Invalid dataset type")
        if self.database.fetch_one("SELECT 1 FROM datasets WHERE dataset_id=? AND version=?", (dataset_id, version)): raise ConflictError("Dataset version already exists")
        distribution = Counter(case.get("category", "unknown") for case in cases); now = datetime.now(timezone.utc).isoformat()
        self.database.execute("INSERT INTO datasets VALUES (?,?,?,?,?,?,?,?,?)", (dataset_id, version, dataset_type, purpose, now, len(cases), dumps(distribution), annotation_status, dumps([{"at": now, "note": change_note}])))
        return self.get_dataset(dataset_id, version)

    def get_dataset(self, dataset_id, version):
        row = self.database.fetch_one("SELECT * FROM datasets WHERE dataset_id=? AND version=?", (dataset_id, version))
        if not row: raise NotFoundError("Dataset not found")
        return {**row, "category_distribution": loads(row.pop("category_distribution_json"), {}), "change_history": loads(row.pop("change_history_json"), [])}

    def list_datasets(self):
        return [self.get_dataset(row["dataset_id"], row["version"]) for row in self.database.fetch_all("SELECT dataset_id,version FROM datasets ORDER BY created_at")]

    def annotate(self, dataset_id, dataset_version, case_id, payload, reviewer=None, status="draft"):
        self.get_dataset(dataset_id, dataset_version)
        if status not in {"draft", "reviewed", "approved", "disputed"}: raise ValueError("Invalid annotation status")
        now = datetime.now(timezone.utc).isoformat(); annotation_id = str(uuid.uuid4())
        self.database.execute("INSERT INTO annotations VALUES (?,?,?,?,?,?,?,?,?)", (annotation_id, dataset_id, dataset_version, str(case_id), dumps(payload), reviewer, status, now, now))
        return {"annotation_id": annotation_id, "review_status": status}

    def list_annotations(self, dataset_id: str, dataset_version: str, include_holdout_labels: bool = False):
        dataset = self.get_dataset(dataset_id, dataset_version)
        if dataset["dataset_type"] == "holdout" and not include_holdout_labels:
            return []
        rows = self.database.fetch_all(
            "SELECT * FROM annotations WHERE dataset_id=? AND dataset_version=? ORDER BY created_at",
            (dataset_id, dataset_version),
        )
        return [{**row, "payload": loads(row.pop("payload_json"), {})} for row in rows]

    def add_human_review(self, run_id, case_id, reviewer, scores, reason=None):
        required = {"correctness", "completeness", "citation_support", "evidence_consistency", "actionability", "refusal_appropriateness"}
        if set(scores) != required or any(not 0 <= float(value) <= 1 for value in scores.values()): raise ValueError("Invalid human-review scores")
        review_id = str(uuid.uuid4()); now = datetime.now(timezone.utc).isoformat()
        self.database.execute("INSERT INTO human_reviews VALUES (?,?,?,?,?,?,?)", (review_id, run_id, str(case_id), reviewer, dumps(scores), reason, now))
        return {"review_id": review_id, "single_reviewer": True, "created_at": now}

    def list_human_reviews(self, run_id: str):
        rows = self.database.fetch_all("SELECT * FROM human_reviews WHERE run_id=? ORDER BY created_at", (run_id,))
        reviewer_count = len({row["reviewer"] for row in rows})
        return [{**row, "scores": loads(row.pop("scores_json"), {}), "single_reviewer": reviewer_count < 2} for row in rows]

    def create_prompt(self, prompt_id, version, content, notes, status="active"):
        checksum = hashlib.sha256(content.encode()).hexdigest(); now = datetime.now(timezone.utc).isoformat()
        try: self.database.execute("INSERT INTO prompts VALUES (?,?,?,?,?,?,?)", (prompt_id, version, content, checksum, now, notes, status))
        except Exception as exc: raise ConflictError("Prompt version is immutable and already exists") from exc
        return {"prompt_id": prompt_id, "version": version, "checksum": checksum, "status": status}

    def list_prompts(self):
        return [{key: value for key, value in row.items() if key != "content"}
                for row in self.database.fetch_all("SELECT * FROM prompts ORDER BY prompt_id,created_at")]

    @staticmethod
    def validate_judge_result(raw: str | dict[str, Any]): return JudgeResult.model_validate_json(raw) if isinstance(raw, str) else JudgeResult.model_validate(raw)

    @staticmethod
    def threshold_experiment(rows: list[dict[str, Any]], thresholds: list[float]):
        output = []
        for threshold in thresholds:
            answered = [row for row in rows if row["score"] >= threshold]; refused = [row for row in rows if row["score"] < threshold]
            total = max(len(rows), 1); correct_answers = sum(row["correct"] for row in answered); unsupported = sum(not row["correct"] for row in answered)
            correct_refusals = sum(not row["answerable"] for row in refused); false_refusals = sum(row["answerable"] for row in refused)
            output.append({"threshold": threshold, "answer_coverage": len(answered)/total, "correct_answer_rate": correct_answers/total,
                "incorrect_answer_rate": unsupported/total, "refusal_rate": len(refused)/total, "correct_refusal_rate": correct_refusals/total,
                "false_refusal_rate": false_refusals/total, "unsupported_answer_rate": unsupported/total})
        return output

    @staticmethod
    def validate_ablation(configuration: dict[str, list[Any]]):
        allowed = {"retrieval_strategy", "final_top_k", "reranker_enabled", "parent_context_enabled", "context_budget", "prompt_version", "chat_model"}
        unknown = set(configuration) - allowed
        if unknown: raise ValueError(f"Unknown ablation variables: {sorted(unknown)}")
        if not configuration or any(not values for values in configuration.values()): raise ValueError("Every ablation variable needs values")
        return {"variables": configuration, "freeze_unlisted_variables": True}
