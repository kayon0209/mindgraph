"""Unified offline evaluation runner with auditable manifests.

The runner orchestrates the existing deterministic evaluators for routing,
answer quality, and ablation-ledger reshaping. It intentionally does not invent
new scoring logic.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from application.adaptive_retrieval_router import AdaptiveRetrievalRouter
from evaluation.ablation_runner import run as run_ablation_report
from evaluation.answer_eval import evaluate_answer_predictions
from evaluation.manifest import build_manifest, to_json
from evaluation.mindgraph_retrieval_eval import (
    DEFAULT_DATASET_PATH,
    dataset_sha256,
    evaluate_retrieval_cases,
    load_golden_dataset,
)
try:
    from src.retrieval.types import Chunk, RetrievalCandidate, RetrievalTrace
except ModuleNotFoundError:
    from retrieval.types import Chunk, RetrievalCandidate, RetrievalTrace
from evaluation.routing_eval import evaluate_routing_cases
from evaluation.threshold_runner import run as run_threshold_report

DEFAULT_MANIFEST_DIR = PROJECT_ROOT / "evaluation" / "results" / "manifests"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "evaluation" / "results" / "unified"
DEFAULT_ROUTING_DATASET = PROJECT_ROOT / "evaluation" / "datasets" / "mindgraph_routing.jsonl"
DEFAULT_ABLATION_SOURCE = PROJECT_ROOT / "evaluation" / "results" / "retrieval_v2" / "comparison_20260713T153349Z.json"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"JSONL file not found: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_artifacts(manifest: dict[str, Any], payload: dict[str, Any], *, manifest_dir: Path, output_dir: Path, suite: str) -> dict[str, str]:
    manifest_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{manifest['run_id']}_{suite}.json"
    result_path = output_dir / f"{manifest['run_id']}_{suite}.json"
    manifest_path.write_text(to_json(manifest), encoding="utf-8")
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"manifest": str(manifest_path), "result": str(result_path)}


def _routing_run(dataset: Path) -> dict[str, Any]:
    cases = _load_jsonl(dataset)
    manifest = build_manifest(
        root=PROJECT_ROOT,
        suite="routing",
        dataset=dataset,
        dataset_version=cases[0]["dataset_version"] if cases else None,
        configuration={"dataset": dataset.name, "evaluator": "deterministic-routing-v1"},
        evaluator_version="deterministic-routing-v1",
        model={"embedder": "not_used", "reranker": "not_used", "llm": "not_used"},
    )
    summary = evaluate_routing_cases(cases, AdaptiveRetrievalRouter())
    return {"manifest": manifest, "summary": summary, "dataset_version": cases[0]["dataset_version"] if cases else None}


def _answer_run(dataset: Path, predictions: Path) -> dict[str, Any]:
    cases = load_golden_dataset(dataset)
    prediction_rows = _load_jsonl(predictions)
    manifest = build_manifest(
        root=PROJECT_ROOT,
        suite="answer",
        dataset=dataset,
        dataset_version=cases[0]["dataset_version"] if cases else None,
        configuration={"dataset": dataset.name, "predictions": predictions.name, "evaluator": "deterministic-answer-v1"},
        evaluator_version="deterministic-answer-v1",
        model={"embedder": "not_used", "reranker": "not_used", "llm": "predictions_input"},
    )
    summary = evaluate_answer_predictions(cases, prediction_rows)
    return {"manifest": manifest, "summary": summary, "dataset_version": cases[0]["dataset_version"] if cases else None}


def _ablation_run(source: Path) -> dict[str, Any]:
    if not source.is_file():
        raise FileNotFoundError(f"ablation source file not found: {source}")
    payload = run_ablation_report(source)
    manifest = build_manifest(
        root=PROJECT_ROOT,
        suite="ablation",
        dataset=source,
        dataset_version=payload.get("dataset_version"),
        configuration={"source": source.name, "evaluator": "ablation-ledger-v1"},
        evaluator_version="ablation-ledger-v1",
        model={"embedder": "source_ledger", "reranker": "source_ledger", "llm": "not_used"},
    )
    return {"manifest": manifest, "payload": payload, "dataset_version": payload.get("dataset_version")}


def _trace_from_dict(raw: dict[str, Any]) -> RetrievalTrace:
    def candidates(values: list[dict[str, Any]]) -> list[RetrievalCandidate]:
        result = []
        for item in values:
            chunk_data = item.get("chunk") or {}
            result.append(
                RetrievalCandidate(
                    chunk=Chunk(**chunk_data),
                    dense_score=item.get("dense_score"),
                    dense_rank=item.get("dense_rank"),
                    sparse_score=item.get("sparse_score"),
                    sparse_rank=item.get("sparse_rank"),
                    rrf_score=item.get("rrf_score"),
                    fused_rank=item.get("fused_rank"),
                    reranker_score=item.get("reranker_score"),
                    final_rank=item.get("final_rank"),
                    original_score=item.get("original_score"),
                    authority_adjustment=item.get("authority_adjustment", 0.0),
                    adjusted_score=item.get("adjusted_score"),
                )
            )
        return result

    return RetrievalTrace(
        query=raw["query"],
        requested_strategy=raw["requested_strategy"],
        actual_strategy=raw["actual_strategy"],
        degraded=raw.get("degraded", False),
        degradation_reason=raw.get("degradation_reason"),
        candidate_counts=raw.get("candidate_counts", {}),
        dense_results=candidates(raw.get("dense_results", [])),
        sparse_results=candidates(raw.get("sparse_results", [])),
        fused_results=candidates(raw.get("fused_results", [])),
        reranked_results=candidates(raw.get("reranked_results", [])),
        final_selected_chunks=candidates(raw.get("final_selected_chunks", [])),
        latency_ms=raw.get("latency_ms", {}),
        index_version=raw.get("index_version"),
        applied_filters=raw.get("applied_filters", {}),
        warnings=raw.get("warnings", []),
    )


def _retrieval_run(*, dataset: Path, trace_source: Path | None, top_k: int) -> dict[str, Any]:
    cases = load_golden_dataset(dataset)
    source_path = trace_source or DEFAULT_ABLATION_SOURCE
    if not source_path.is_file():
        raise FileNotFoundError(f"retrieval trace source not found: {source_path}")
    source = json.loads(source_path.read_text(encoding="utf-8"))
    details_by_case = source.get("details", {})
    strategy = source.get("strategy") or source.get("retrieval_strategy") or "hybrid"
    if isinstance(details_by_case, dict) and strategy in details_by_case:
        details_by_case = details_by_case[strategy]
    if isinstance(details_by_case, dict):
        details_by_case = list(details_by_case.values())
    if not isinstance(details_by_case, list):
        raise ValueError("trace source must contain a list of case details")
    traces = {row["case_id"]: _trace_from_dict(row["trace"]) for row in details_by_case if row.get("trace")}
    if not traces:
        raise ValueError("trace source contains no serialized traces")
    available_case_ids = set(traces)
    requested_case_ids = {case["case_id"] for case in cases}
    if not requested_case_ids.issubset(available_case_ids):
        missing = sorted(requested_case_ids - available_case_ids)
        raise ValueError(
            "trace source and dataset are not from the same evaluation version; "
            f"missing case_id(s): {', '.join(missing[:5])}"
        )

    def retrieve(case: dict[str, Any]) -> RetrievalTrace:
        try:
            return traces[case["case_id"]]
        except KeyError as exc:
            raise ValueError(f"trace source missing case_id {case['case_id']!r}") from exc

    manifest = build_manifest(
        root=PROJECT_ROOT,
        suite="retrieval",
        dataset=dataset,
        dataset_version=cases[0]["dataset_version"] if cases else None,
        configuration={"dataset": dataset.name, "trace_source": source_path.name, "top_k": top_k, "evaluator": "mindgraph-retrieval-v1"},
        evaluator_version="mindgraph-retrieval-v1",
        index=source.get("index_metadata"),
        model={
            "embedder": (source.get("index_metadata") or {}).get("embedding_model_name") or "unknown_from_trace",
            "reranker": (source.get("index_metadata") or {}).get("reranker_model_name") or "not_used",
            "llm": "not_used",
        },
    )
    report = evaluate_retrieval_cases(cases, retrieve, top_k=top_k, dataset_digest=dataset_sha256(dataset))
    return {"manifest": manifest, "summary": report, "dataset_version": cases[0]["dataset_version"] if cases else None}


def _threshold_run(source: Path, thresholds: list[float]) -> dict[str, Any]:
    if not source.is_file():
        raise FileNotFoundError(f"threshold source file not found: {source}")
    source_payload = json.loads(source.read_text(encoding="utf-8"))
    details = source_payload.get("details", {}).get("hybrid", [])
    payload = {
        "source": str(source),
        "sample_size": len(details),
        "thresholds": thresholds,
        "results": run_threshold_report(source, thresholds),
        "limitation": "Existing non-holdout retrieval cases; no production threshold is inferred",
    }
    manifest = build_manifest(
        root=PROJECT_ROOT,
        suite="threshold",
        dataset=source,
        dataset_version=source_payload.get("dataset_version"),
        configuration={"source": source.name, "thresholds": thresholds, "evaluator": "threshold-governance-v1"},
        evaluator_version="threshold-governance-v1",
        index=source_payload.get("index_metadata"),
        model={"embedder": "source_ledger", "reranker": "source_ledger", "llm": "not_used"},
    )
    return {"manifest": manifest, "payload": payload, "dataset_version": source_payload.get("dataset_version")}


def _enrich_manifest(result: dict[str, Any]) -> None:
    manifest = result["manifest"]
    suite = manifest["suite"]
    payload = result.get("summary") or result.get("payload") or {}
    if isinstance(payload, dict):
        total = payload.get("sample_size")
        if total is None and isinstance(payload.get("counts"), dict):
            total = sum(value for value in payload["counts"].values() if isinstance(value, int))
    else:
        total = None
    total = int(total or 0)
    skipped = 0
    if suite == "retrieval" and isinstance(payload, dict):
        skipped = int((payload.get("counts") or {}).get("abstain", 0))
    manifest["execution"] = {
        "status": "completed",
        "total": total,
        "succeeded": max(total - skipped, 0),
        "failed": 0,
        "skipped": skipped,
    }
    metric_source = payload.get("summary", payload.get("metrics", {})) if isinstance(payload, dict) else {}
    if not isinstance(metric_source, dict):
        metric_source = {}
    manifest["performance"] = {
        "p50_ms": metric_source.get("p50_retrieval_ms", metric_source.get("p50_ms")),
        "p95_ms": metric_source.get("p95_retrieval_ms", metric_source.get("p95_ms")),
    }


def run_suite(
    suite: str,
    *,
    dataset: Path,
    predictions: Path | None = None,
    trace_source: Path | None = None,
    source: Path | None = None,
    top_k: int = 5,
    thresholds: list[float] | None = None,
    manifest_dir: Path | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    manifest_dir = manifest_dir or DEFAULT_MANIFEST_DIR
    output_dir = output_dir or DEFAULT_OUTPUT_DIR

    if suite == "routing":
        result = _routing_run(dataset)
    elif suite == "answer":
        if predictions is None:
            raise ValueError("answer suite requires --predictions")
        result = _answer_run(dataset, predictions)
    elif suite == "ablation":
        ablation_source = source or DEFAULT_ABLATION_SOURCE
        result = _ablation_run(ablation_source)
    elif suite == "retrieval":
        result = _retrieval_run(dataset=dataset, trace_source=trace_source, top_k=top_k)
    elif suite == "threshold":
        result = _threshold_run(source or dataset, thresholds or [0.02, 0.025, 0.03])
    else:
        raise ValueError(f"unsupported suite: {suite}")

    _enrich_manifest(result)
    manifest_data = result["manifest"]
    artifacts = _write_artifacts(
        result["manifest"],
        {key: value for key, value in result.items() if key != "manifest"},
        manifest_dir=manifest_dir,
        output_dir=output_dir,
        suite=suite,
    )
    result.update(artifacts)
    result["manifest_data"] = manifest_data
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified MindGraph offline evaluation runner")
    parser.add_argument("--suite", required=True, choices=["retrieval", "routing", "answer", "ablation", "threshold"])
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET_PATH))
    parser.add_argument("--predictions")
    parser.add_argument("--trace-source", default=str(DEFAULT_ABLATION_SOURCE))
    parser.add_argument("--source", default=str(DEFAULT_ABLATION_SOURCE))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--thresholds", default="0.02,0.025,0.03")
    parser.add_argument("--manifest-dir", default=str(DEFAULT_MANIFEST_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()

    result = run_suite(
        args.suite,
        dataset=Path(args.dataset),
        predictions=Path(args.predictions) if args.predictions else None,
        trace_source=Path(args.trace_source) if args.trace_source else None,
        source=Path(args.source) if args.source else None,
        top_k=args.top_k,
        thresholds=[float(value) for value in args.thresholds.split(",")],
        manifest_dir=Path(args.manifest_dir),
        output_dir=Path(args.output_dir),
    )
    print(json.dumps({"suite": args.suite, "manifest": result["manifest"], "result": result["result"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
