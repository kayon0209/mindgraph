import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from scripts.run_answer_evaluation import answer_run_label, collect_live_predictions


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_answer_evaluation.py"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_answer_evaluation_cli_dry_run_emits_auditable_summary(tmp_path: Path) -> None:
    """Catches the answer evaluator becoming unusable without a database write."""
    golden = tmp_path / "golden.jsonl"
    predictions = tmp_path / "predictions.jsonl"
    _write_jsonl(
        golden,
        [
            {
                "case_id": "no-answer",
                "expected_behavior": "abstain",
                "evaluation_date": "2026-08-18",
                "gold_vault_paths": [],
                "historical_vault_paths": [],
                "required_facts": [],
                "forbidden_facts": [],
                "dataset_version": "test-1",
            }
        ],
    )
    _write_jsonl(
        predictions,
        [
            {
                "case_id": "no-answer",
                "result_state": "insufficient_evidence",
                "answer": "未找到足够依据。",
                "citations": [],
            }
        ],
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--golden",
            str(golden),
            "--predictions",
            str(predictions),
            "--dry-run",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["evaluator_version"] == "deterministic-answer-v1"
    assert payload["dataset_version"] == "test-1"
    assert payload["metrics"]["refusal_correctness"] == 1.0
    assert payload["sample_size"] == 1
    assert payload["written"] is False


def test_live_collection_applies_golden_date_and_historical_scope() -> None:
    """Catches version-comparison cases being evaluated with current-only retrieval."""
    requests = []

    class Answerer:
        def answer(self, request):
            requests.append(request)
            return SimpleNamespace(
                model_dump=lambda mode: {
                    "result_state": "answered",
                    "answer": "结论",
                    "citations": [],
                }
            )

    predictions = collect_live_predictions(
        [
            {
                "case_id": "history",
                "question": "旧版与新版有什么不同？",
                "evaluation_date": "2026-08-18",
                "historical_vault_paths": ["policies/v1.md"],
            },
            {
                "case_id": "current",
                "question": "当前规则是什么？",
                "evaluation_date": "2026-08-18",
                "historical_vault_paths": [],
            },
        ],
        Answerer(),
        strategy="hybrid",
        graph_enabled=False,
    )

    assert [item["case_id"] for item in predictions] == ["history", "current"]
    assert requests[0].query_date == "2026-08-18"
    assert requests[0].include_historical is True
    assert requests[1].include_historical is False
    assert all(request.graph_enabled is False for request in requests)


def test_answer_run_label_cannot_collide_with_retrieval_ablation_strategy() -> None:
    """Catches answer metrics shadowing a retrieval-only Hybrid ledger entry."""
    assert answer_run_label("hybrid", graph_enabled=True) == "answer_eval_hybrid_graph"
    assert answer_run_label("hybrid", graph_enabled=False) == "answer_eval_hybrid"
