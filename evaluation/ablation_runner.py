from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
METRICS = ("recall_at_1", "recall_at_3", "recall_at_5", "mrr", "document_hit_rate", "chunk_hit_rate", "mean_retrieval_latency_ms")


def run(source: Path) -> dict:
    data = json.loads(source.read_text(encoding="utf-8"))
    rows = [{"retrieval_strategy": strategy, **{metric: values.get(metric) for metric in METRICS}}
            for strategy, values in data["summary"].items()]
    return {
        "source": str(source),
        "dataset_version": data["dataset_version"],
        "sample_size": data["retrieval_eligible_cases"],
        "ablation_variable": "retrieval_strategy",
        "frozen_controls": data.get("controls", {}),
        "results": rows,
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
    lines.extend(["", "## Limitations", "", *[f"- {item}" for item in result["limitations"]]])
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    print(json_path)
    print(csv_path)
    print(markdown_path)


if __name__ == "__main__":
    main()
