from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METRICS = ("recall_at_1", "recall_at_3", "recall_at_5", "mrr", "document_hit_rate", "chunk_hit_rate", "mean_retrieval_latency_ms")


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def evaluate_graph_gate(
    graph_metrics: dict[str, float],
    baseline_metrics: dict[str, float],
    *,
    min_recall_gain: float = 0.05,
    max_latency_multiplier: float = 3.0,
) -> dict[str, object]:
    graph_recall = float(graph_metrics.get("recall_at_5", 0.0))
    baseline_recall = float(baseline_metrics.get("recall_at_5", 0.0))
    graph_latency = float(graph_metrics.get("mean_retrieval_latency_ms", 0.0))
    baseline_latency = float(baseline_metrics.get("mean_retrieval_latency_ms", 0.0))
    recall_gain = graph_recall - baseline_recall
    latency_ratio = graph_latency / baseline_latency if baseline_latency > 0 else math.inf
    reasons: list[str] = []
    if recall_gain < min_recall_gain:
        reasons.append("recall_gain_below_threshold")
    if latency_ratio > max_latency_multiplier:
        reasons.append("latency_regression")
    eligible = not reasons
    return {
        "eligible": eligible,
        "reasons": reasons,
        "recall_gain": recall_gain,
        "latency_ratio": latency_ratio,
        "default_route_recommendation": "conditional_only" if eligible else "keep_graph_disabled",
    }


def evaluate_ablation(
    rows: list[dict[str, object]],
    *,
    graph_strategy: str,
    baseline_strategy: str,
) -> dict[str, object]:
    by_name = {str(row["retrieval_strategy"]): row for row in rows}
    if graph_strategy not in by_name or baseline_strategy not in by_name:
        raise ValueError("strategy rows must include both baseline and graph strategies")
    graph_row = by_name[graph_strategy]
    baseline_row = by_name[baseline_strategy]
    deltas = {
        metric: float(graph_row.get(metric, 0.0)) - float(baseline_row.get(metric, 0.0))
        for metric in METRICS
    }
    gate = evaluate_graph_gate(graph_row, baseline_row)
    return {
        "graph_strategy": graph_strategy,
        "baseline_strategy": baseline_strategy,
        "graph_metrics": {metric: graph_row.get(metric) for metric in METRICS},
        "baseline_metrics": {metric: baseline_row.get(metric) for metric in METRICS},
        "deltas": deltas,
        "decision": {
            "eligible": gate["eligible"],
            "reasons": gate["reasons"],
            "statistical_significance": False,
            "default_route_recommendation": gate["default_route_recommendation"],
        },
    }


def run(source: Path) -> dict:
    data = json.loads(source.read_text(encoding="utf-8"))
    rows = [{"retrieval_strategy": strategy, **{metric: values.get(metric) for metric in METRICS}}
            for strategy, values in data["summary"].items()]
    graph_strategy = next((row["retrieval_strategy"] for row in rows if row["retrieval_strategy"].endswith("_graph")), None)
    baseline_strategy = next((row["retrieval_strategy"] for row in rows if row["retrieval_strategy"] in {"bm25_vector", "hybrid"}), None)
    if graph_strategy is None or baseline_strategy is None:
        return {
            "source": str(source),
            "dataset_version": data.get("dataset_version"),
            "sample_size": data.get("retrieval_eligible_cases", 0),
            "ablation_variable": "retrieval_strategy",
            "frozen_controls": data.get("controls", {}),
            "results": rows,
            "decision": {"eligible": False, "reasons": ["no_comparable_graph_and_baseline_rows"], "statistical_significance": False, "default_route_recommendation": "keep_graph_disabled"},
            "deltas": {},
            "limitations": ["The source contains no explicitly comparable graph and baseline rows; no gate decision was inferred."],
        }
    decision = evaluate_ablation(rows, graph_strategy=graph_strategy, baseline_strategy=baseline_strategy)
    return {
        "source": str(source),
        "dataset_version": data["dataset_version"],
        "sample_size": data["retrieval_eligible_cases"],
        "ablation_variable": "retrieval_strategy",
        "frozen_controls": data.get("controls", {}),
        "results": rows,
        "decision": decision["decision"],
        "deltas": decision["deltas"],
        "limitations": [
            "Uses the existing development/regression-derived 23 retrieval-eligible cases, not an independent holdout.",
            "Only retrieval strategy changes; no generation-model conclusion is supported.",
            "No statistical significance test is claimed.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    result = run(Path(args.source))
    output = ROOT / "evaluation" / "results" / "governance"
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = output / f"ablation_{stamp}.json"
    csv_path = output / f"ablation_{stamp}.csv"
    markdown_path = ROOT / "docs" / "evaluation" / "retrieval-ablation.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=result["results"][0])
        writer.writeheader()
        writer.writerows(result["results"])
    lines = [
        "# Retrieval strategy ablation", "",
        f"Source: `{result['source']}`. Dataset version: `{result['dataset_version']}`. Sample size: {result['sample_size']} retrieval-eligible cases.", "",
        "| Strategy | R@1 | R@3 | R@5 | MRR | Doc hit | Chunk hit | Mean latency (ms) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in result["results"]:
        lines.append("| {retrieval_strategy} | {recall_at_1:.4f} | {recall_at_3:.4f} | {recall_at_5:.4f} | {mrr:.4f} | {document_hit_rate:.4f} | {chunk_hit_rate:.4f} | {mean_retrieval_latency_ms:.4f} |".format(**row))
    lines.extend([
        "",
        "## Decision",
        "",
        f"- Eligible for default graph path: `{result['decision']['eligible']}`",
        f"- Default recommendation: `{result['decision']['default_route_recommendation']}`",
        f"- Reasons: {', '.join(result['decision']['reasons']) if result['decision']['reasons'] else 'none'}",
        "",
        "## Limitations",
        "",
        *[f"- {item}" for item in result["limitations"]],
    ])
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    print(json_path)
    print(csv_path)
    print(markdown_path)


if __name__ == "__main__":
    main()
