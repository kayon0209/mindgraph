import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_routing_evaluation.py"
UNIFIED_SCRIPT = ROOT / "evaluation" / "runner.py"


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


def test_unified_runner_writes_manifest_and_result_for_routing(tmp_path: Path) -> None:
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
    manifest_dir = tmp_path / "manifests"
    output_dir = tmp_path / "results"
    completed = subprocess.run(
        [
            sys.executable,
            str(UNIFIED_SCRIPT),
            "--suite",
            "routing",
            "--dataset",
            str(dataset),
            "--manifest-dir",
            str(manifest_dir),
            "--output-dir",
            str(output_dir),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    manifest = json.loads(Path(payload["manifest"]).read_text(encoding="utf-8"))
    result = json.loads(Path(payload["result"]).read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == "mindgraph-eval-manifest-v1"
    assert manifest["suite"] == "routing"
    assert manifest["dataset"]["version"] == "routing-test-v1"
    assert len(manifest["dataset"]["sha256"]) == 64
    assert result["summary"]["metrics"]["route_accuracy"] == 1.0
