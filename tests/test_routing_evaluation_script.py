import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_routing_evaluation.py"


def test_routing_evaluation_cli_dry_run_is_auditable(tmp_path: Path) -> None:
    dataset = tmp_path / "routing.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "case_id": "fact",
                "question": "差旅费多久提交？",
                "expected_route": "factual",
                "expected_strategy": "hybrid",
                "expected_graph_enabled": False,
                "dataset_version": "routing-test-v1",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--dataset", str(dataset), "--dry-run"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["evaluator_version"] == "deterministic-routing-v1"
    assert payload["dataset_version"] == "routing-test-v1"
    assert payload["metrics"]["route_accuracy"] == 1.0
    assert payload["written"] is False
