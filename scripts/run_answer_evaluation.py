"""Run deterministic answer-level evaluation against the independent Golden Set.

Predictions are JSONL rows with ``case_id``, ``result_state``, ``answer`` and
``citations``. Each citation must carry ``vault_path``, ``policy_status``,
``effective_from`` and optional ``effective_to``. The prediction set must contain
exactly one row for every Golden case.
"""

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

from evaluation.answer_eval import evaluate_answer_predictions  # noqa: E402
from domain.models import ChatRequest  # noqa: E402


EVALUATOR_VERSION = "deterministic-answer-v1"
DEFAULT_GOLDEN = PROJECT_ROOT / "evaluation" / "datasets" / "mindgraph_golden.jsonl"


def _load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(f"JSONL file not found: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _dataset_version(cases: list[dict]) -> str:
    versions = {case.get("dataset_version") for case in cases}
    if len(versions) != 1 or None in versions:
        raise ValueError("Golden cases must share one dataset_version")
    return str(versions.pop())


def collect_live_predictions(
    cases: list[dict],
    answerer,
    *,
    strategy: str,
    graph_enabled: bool,
    provider: str | None = None,
    model: str | None = None,
) -> list[dict]:
    """Run every Golden question through the current production chat service."""
    predictions = []
    for case in cases:
        request = ChatRequest(
            question=case["question"],
            retrieval_strategy=strategy,
            query_date=case.get("evaluation_date"),
            include_historical=bool(case.get("historical_vault_paths")),
            graph_enabled=graph_enabled,
            chat_provider=provider,
            chat_model=model,
            include_retrieval_trace=True,
        )
        payload = answerer.answer(request).model_dump(mode="json")
        predictions.append({"case_id": case["case_id"], **payload})
    return predictions


def _save_predictions(path: Path, predictions: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in predictions) + "\n",
        encoding="utf-8",
    )


def answer_run_label(strategy: str, *, graph_enabled: bool) -> str:
    suffix = "_graph" if graph_enabled else ""
    return f"answer_eval_{strategy}{suffix}"


def _write_run(args: argparse.Namespace, payload: dict, failed_cases: list[dict]) -> str:
    from api.dependencies import get_container
    from infrastructure.database import dumps

    db = get_container().database
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        """INSERT INTO evaluation_runs (
            run_id, status, dataset_name, dataset_version, retrieval_strategy, chat_model,
            started_at, finished_at, configuration_json, summary_metrics_json,
            category_metrics_json, failed_cases_json, result_files_json, progress_messages_json, error
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id,
            "completed",
            args.dataset_name,
            payload["dataset_version"],
            answer_run_label(args.strategy, graph_enabled=not args.no_graph),
            args.model,
            now,
            now,
            dumps(
                {
                    "evaluator_version": EVALUATOR_VERSION,
                    "golden": Path(args.golden).name,
                    "predictions": payload["prediction_source"],
                }
            ),
            dumps({**payload["metrics"], "sample_size": payload["sample_size"], "failed_case_count": payload["failed_case_count"]}),
            dumps({}),
            dumps(failed_cases),
            dumps([payload["prediction_source"]]),
            dumps(["completed"]),
            None,
        ),
    )
    return run_id


def main() -> None:
    parser = argparse.ArgumentParser(description="MindGraph deterministic answer-level evaluation")
    parser.add_argument("--golden", default=str(DEFAULT_GOLDEN))
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--predictions", help="existing answer prediction JSONL")
    source.add_argument("--live", action="store_true", help="run every Golden question through the configured service")
    parser.add_argument("--output", help="live prediction JSONL output path")
    parser.add_argument("--dataset-name", default="mindgraph_enterprise_answer_v2")
    parser.add_argument(
        "--strategy",
        default="auto",
        choices=["auto", "dense", "bm25", "hybrid", "hybrid_rerank"],
    )
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--no-graph", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="score without writing evaluation_runs")
    args = parser.parse_args()

    cases = _load_jsonl(Path(args.golden))
    if args.live:
        from api.dependencies import get_container

        predictions = collect_live_predictions(
            cases,
            get_container().mindgraph_chat,
            strategy=args.strategy,
            graph_enabled=not args.no_graph,
            provider=args.provider,
            model=args.model,
        )
        output = Path(args.output) if args.output else (
            PROJECT_ROOT
            / "evaluation"
            / "results"
            / f"answer_predictions_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
        )
        _save_predictions(output, predictions)
        prediction_source = str(output.relative_to(PROJECT_ROOT)) if output.is_relative_to(PROJECT_ROOT) else output.name
    else:
        predictions = _load_jsonl(Path(args.predictions))
        prediction_source = Path(args.predictions).name
    summary = evaluate_answer_predictions(cases, predictions)
    payload = {
        "evaluator_version": EVALUATOR_VERSION,
        "dataset_version": _dataset_version(cases),
        **summary,
        "prediction_source": prediction_source,
        "written": False,
    }
    if not args.dry_run:
        payload["run_id"] = _write_run(args, payload, summary["failed_cases"])
        payload["written"] = True
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
