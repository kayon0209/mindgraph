"""Evaluate the deterministic adaptive router against a frozen routing matrix."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(SRC_ROOT))

from application.adaptive_retrieval_router import AdaptiveRetrievalRouter  # noqa: E402
from evaluation.routing_eval import evaluate_routing_cases  # noqa: E402


EVALUATOR_VERSION = "deterministic-routing-v1"
DEFAULT_DATASET = PROJECT_ROOT / "evaluation" / "datasets" / "mindgraph_routing.jsonl"


def _load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(f"routing dataset not found: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _dataset_version(cases: list[dict]) -> str:
    versions = {case.get("dataset_version") for case in cases}
    if len(versions) != 1 or None in versions:
        raise ValueError("routing cases must share one dataset_version")
    return str(versions.pop())


def _write_run(dataset_name: str, dataset_version: str, dataset: Path, summary: dict) -> str:
    from api.dependencies import get_container
    from infrastructure.database import dumps

    database = get_container().database
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    database.execute(
        """INSERT INTO evaluation_runs (
            run_id, status, dataset_name, dataset_version, retrieval_strategy, chat_model,
            started_at, finished_at, configuration_json, summary_metrics_json,
            category_metrics_json, failed_cases_json, result_files_json, progress_messages_json, error
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id,
            "completed",
            dataset_name,
            dataset_version,
            "adaptive_route_eval",
            None,
            now,
            now,
            dumps({"evaluator_version": EVALUATOR_VERSION, "dataset": dataset.name}),
            dumps({**summary["metrics"], "sample_size": summary["sample_size"]}),
            dumps({"route_distribution": summary["route_distribution"]}),
            dumps(summary["failed_cases"]),
            dumps([dataset.name]),
            dumps(["completed"]),
            None,
        ),
    )
    return run_id


def main() -> None:
    parser = argparse.ArgumentParser(description="MindGraph deterministic adaptive routing evaluation")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--dataset-name", default="mindgraph_adaptive_routing_v1")
    parser.add_argument("--dry-run", action="store_true", help="evaluate without writing evaluation_runs")
    args = parser.parse_args()

    dataset = Path(args.dataset)
    cases = _load_jsonl(dataset)
    dataset_version = _dataset_version(cases)
    summary = evaluate_routing_cases(cases, AdaptiveRetrievalRouter())
    payload = {
        "evaluator_version": EVALUATOR_VERSION,
        "dataset_version": dataset_version,
        **summary,
        "written": False,
    }
    if not args.dry_run:
        payload["run_id"] = _write_run(args.dataset_name, dataset_version, dataset, summary)
        payload["written"] = True
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
