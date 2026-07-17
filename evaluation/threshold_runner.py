from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from application.evaluation_governance_service import EvaluationGovernanceService


def run(source: Path, thresholds: list[float]):
    data = json.loads(source.read_text(encoding="utf-8")); rows = []
    for detail in data["details"]["hybrid"]:
        final = detail["trace"]["final_selected_chunks"]
        score = max((item.get("rrf_score") or 0.0 for item in final), default=0.0)
        rows.append({"score": score, "correct": detail["metrics"]["recall_at_5"] > 0, "answerable": True})
    return EvaluationGovernanceService.threshold_experiment(rows, thresholds)


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--source", required=True); parser.add_argument("--thresholds", default="0.02,0.025,0.03")
    args = parser.parse_args(); source = Path(args.source); result = run(source, [float(value) for value in args.thresholds.split(",")])
    out = ROOT / "evaluation" / "results" / "governance"; out.mkdir(parents=True, exist_ok=True); stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out / f"threshold_{stamp}.json"; csv_path = out / f"threshold_{stamp}.csv"; md_path = ROOT / "docs" / "evaluation" / "threshold-experiment.md"
    sample_size = len(json.loads(source.read_text(encoding="utf-8"))["details"]["hybrid"])
    json_path.write_text(json.dumps({"source": str(source), "sample_size": sample_size, "results": result, "limitation": "Existing non-holdout retrieval cases; MVP comparison only"}, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle: writer = csv.DictWriter(handle, fieldnames=result[0]); writer.writeheader(); writer.writerows(result)
    lines = ["# Evidence-threshold experiment", "", f"Source: `{source}`. Sample size: {sample_size} historical retrieval cases; no significance test; not a holdout.", "", "| Threshold | Coverage | Correct answer | Incorrect answer | Refusal | Correct refusal | False refusal | Unsupported answer |", "|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for item in result: lines.append("| {threshold} | {answer_coverage:.4f} | {correct_answer_rate:.4f} | {incorrect_answer_rate:.4f} | {refusal_rate:.4f} | {correct_refusal_rate:.4f} | {false_refusal_rate:.4f} | {unsupported_answer_rate:.4f} |".format(**item))
    lines += ["", "No production threshold is selected from this small, previously tuned dataset."]
    md_path.write_text("\n".join(lines), encoding="utf-8"); print(json_path); print(csv_path); print(md_path)


if __name__ == "__main__": main()
