from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone

from domain.errors import ConflictError, NotFoundError
from domain.models import EvaluationRun, EvaluationRunCreate
from evaluation.baseline import DATASET_VERSION
from evaluation.baseline import load_dataset
from evaluation.retrieval_eval import evaluate, save_results
from infrastructure.database import ProductDatabase, dumps, loads
from config import ROOT

logger = logging.getLogger("mindgraph.evaluation")


class EvaluationService:
    def __init__(self, database: ProductDatabase) -> None:
        self.database = database

    def create(self, payload: EvaluationRunCreate) -> EvaluationRun:
        configuration = payload.model_dump(mode="json")
        fingerprint = hashlib.sha256(dumps(configuration).encode()).hexdigest()
        active = self.database.fetch_all("SELECT configuration_json FROM evaluation_runs WHERE status IN ('queued','running')")
        if any(hashlib.sha256(row["configuration_json"].encode()).hexdigest() == fingerprint for row in active):
            raise ConflictError("An identical evaluation is already active")
        run = EvaluationRun(run_id=str(uuid.uuid4()), status="queued", dataset_name=payload.dataset_name,
            dataset_version=DATASET_VERSION, retrieval_strategy=",".join(payload.retrieval_strategies),
            chat_model=payload.chat_model, configuration=configuration, progress_messages=["queued"],
            index_version=self._compatible_index_version(payload.dataset_name), prompt_version=payload.prompt_version,
            provider=payload.chat_provider)
        self._save(run)
        logger.info("evaluation_queued", extra={"run_id": run.run_id, "strategies": run.retrieval_strategy})
        return run

    def execute(self, run_id: str) -> None:
        run = self.get(run_id)
        run.status, run.started_at, run.progress_messages = "running", datetime.now(timezone.utc), [*run.progress_messages, "running"]
        self._save(run, update=True)
        try:
            result = evaluate(
                run.configuration["repetitions"], run.configuration["warmups"],
                "hybrid_rerank" in run.configuration["retrieval_strategies"],
                split=self._dataset_split(run.dataset_name), index_version=run.index_version,
            )
            paths = save_results(result, update_official_report=False)
            selected = run.configuration["retrieval_strategies"]
            run.summary_metrics = {name: result["summary"][name] for name in selected if name in result.get("summary", {})}
            run.category_metrics = {name: result["per_category"][name] for name in selected if name in result.get("per_category", {})}
            run.failed_cases = [item for name in selected for item in result.get("details", {}).get(name, []) if "failure_category" in item]
            run.result_files = [path.name for path in paths]
            run.status, run.progress_messages = "completed", [*run.progress_messages, "completed"]
        except Exception as exc:
            run.status, run.error = "failed", f"{type(exc).__name__}: {exc}"
            run.progress_messages = [*run.progress_messages, "failed"]
        run.finished_at = datetime.now(timezone.utc)
        self._save(run, update=True)
        logger.info("evaluation_finished", extra={"run_id": run.run_id, "status": run.status})

    def list(self) -> list[EvaluationRun]:
        return [self._row(row) for row in self.database.fetch_all("SELECT * FROM evaluation_runs ORDER BY rowid DESC")]

    def get(self, run_id: str) -> EvaluationRun:
        row = self.database.fetch_one("SELECT * FROM evaluation_runs WHERE run_id=?", (run_id,))
        if not row:
            raise NotFoundError("Evaluation run not found")
        return self._row(row)

    def _save(self, run: EvaluationRun, update: bool = False) -> None:
        values = (run.status, run.dataset_name, run.dataset_version, run.retrieval_strategy, run.chat_model,
            run.started_at.isoformat() if run.started_at else None, run.finished_at.isoformat() if run.finished_at else None,
            dumps(run.configuration), dumps(run.summary_metrics), dumps(run.category_metrics), dumps(run.failed_cases),
            dumps(run.result_files), dumps(run.progress_messages), run.error,
            run.index_version, run.prompt_version, run.provider)
        if update:
            self.database.execute("UPDATE evaluation_runs SET status=?,dataset_name=?,dataset_version=?,retrieval_strategy=?,chat_model=?,started_at=?,finished_at=?,configuration_json=?,summary_metrics_json=?,category_metrics_json=?,failed_cases_json=?,result_files_json=?,progress_messages_json=?,error=?,index_version=?,prompt_version=?,provider=? WHERE run_id=?", values + (run.run_id,))
        else:
            self.database.execute("""INSERT INTO evaluation_runs (
                run_id,status,dataset_name,dataset_version,retrieval_strategy,chat_model,started_at,finished_at,
                configuration_json,summary_metrics_json,category_metrics_json,failed_cases_json,result_files_json,
                progress_messages_json,error,index_version,prompt_version,provider
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (run.run_id,) + values)

    @staticmethod
    def _row(row) -> EvaluationRun:
        return EvaluationRun(run_id=row["run_id"], status=row["status"], dataset_name=row["dataset_name"],
            dataset_version=row["dataset_version"], retrieval_strategy=row["retrieval_strategy"], chat_model=row["chat_model"],
            started_at=row["started_at"], finished_at=row["finished_at"], configuration=loads(row["configuration_json"], {}),
            summary_metrics=loads(row["summary_metrics_json"], {}), category_metrics=loads(row["category_metrics_json"], {}),
            failed_cases=loads(row["failed_cases_json"], []), result_files=loads(row["result_files_json"], []),
            progress_messages=loads(row["progress_messages_json"], []), error=row["error"],
            index_version=row.get("index_version"), prompt_version=row.get("prompt_version"), provider=row.get("provider"))

    @staticmethod
    def _current_index_version() -> str | None:
        try:
            return (ROOT / "data" / "retrieval_indexes" / "CURRENT").read_text(encoding="utf-8").strip()
        except OSError:
            return None

    @staticmethod
    def _dataset_split(dataset_name: str) -> str | None:
        if dataset_name.endswith("_development"):
            return "development"
        if dataset_name.endswith("_regression"):
            return "regression"
        return None

    def _compatible_index_version(self, dataset_name: str) -> str | None:
        split = self._dataset_split(dataset_name)
        cases = [case for case in load_dataset() if split is None or case["split"] == split]
        gold_ids = {chunk_id for case in cases for chunk_id in case["gold_chunk_ids"]}
        index_root = ROOT / "data" / "retrieval_indexes"
        best_version, best_score = None, (0, "", "")
        for directory in index_root.iterdir() if index_root.exists() else []:
            chunks_path = directory / "chunks.json"
            if not chunks_path.exists():
                continue
            try:
                chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            indexed_ids = {item.get("chunk_id") for item in chunks}
            overlap = len(gold_ids & indexed_ids)
            metadata_path = directory / "metadata.json"
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
            except (OSError, json.JSONDecodeError):
                metadata = {}
            created_at = metadata.get("index_created_at") or metadata.get("created_at") or ""
            score = (overlap, created_at, directory.name)
            if score > best_score:
                best_version, best_score = directory.name, score
        if gold_ids and best_score[0] == 0:
            raise ValueError("No index version is compatible with this dataset's Gold chunk labels")
        return best_version or self._current_index_version()
