import json
from pathlib import Path

import pytest

from evaluation.runner import run_suite


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_unified_runner_answer_requires_predictions(tmp_path: Path):
    golden = tmp_path / "golden.jsonl"
    _write_jsonl(
        golden,
        [
            {
                "case_id": "a",
                "question": "q",
                "category": "no_answer",
                "split": "development",
                "expected_behavior": "abstain",
                "gold_vault_paths": [],
                "required_facts": [],
                "forbidden_facts": [],
                "dataset_version": "test-v1",
                "label_source": "human-authored-test",
            }
        ],
    )
    with pytest.raises(ValueError, match="requires --predictions"):
        run_suite("answer", dataset=golden, manifest_dir=tmp_path / "m", output_dir=tmp_path / "o")


def test_unified_runner_emits_manifest_and_layered_answer_metrics(tmp_path: Path):
    golden = tmp_path / "golden.jsonl"
    predictions = tmp_path / "predictions.jsonl"
    _write_jsonl(
        golden,
        [
            {
                "case_id": "a",
                "question": "q",
                "category": "no_answer",
                "split": "development",
                "expected_behavior": "abstain",
                "gold_vault_paths": [],
                "required_facts": [],
                "forbidden_facts": [],
                "dataset_version": "test-v1",
                "label_source": "human-authored-test",
            }
        ],
    )
    _write_jsonl(
        predictions,
        [{"case_id": "a", "result_state": "insufficient_evidence", "answer": "依据不足", "citations": []}],
    )
    result = run_suite("answer", dataset=golden, predictions=predictions, manifest_dir=tmp_path / "m", output_dir=tmp_path / "o")
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    report = json.loads(Path(result["result"]).read_text(encoding="utf-8"))
    assert manifest["dataset"]["version"] == "test-v1"
    assert manifest["model"]["llm"] is None
    assert report["summary"]["metrics"]["refusal_correctness"] == 1.0
    assert report["summary"]["sample_size"] == 1


def test_retrieval_suite_requires_trace_source_file(tmp_path: Path):
    golden = tmp_path / "golden.jsonl"
    _write_jsonl(
        golden,
        [{
            "case_id": "a", "question": "q", "category": "fact", "split": "development",
            "expected_behavior": "answer", "gold_vault_paths": ["a.md"], "required_facts": ["x"],
            "forbidden_facts": [], "dataset_version": "test-v1", "label_source": "human-authored-test",
        }],
    )
    with pytest.raises(FileNotFoundError, match="retrieval trace source not found"):
        run_suite("retrieval", dataset=golden, trace_source=tmp_path / "missing.json", manifest_dir=tmp_path / "m", output_dir=tmp_path / "o")


def test_retrieval_suite_uses_default_trace_source_when_not_provided(tmp_path: Path):
    golden = tmp_path / "golden.jsonl"
    trace_source = tmp_path / "traces.json"
    _write_jsonl(
        golden,
        [{
            "case_id": "a", "question": "q", "category": "fact", "split": "development",
            "expected_behavior": "answer", "gold_vault_paths": ["a.md"], "required_facts": ["x"],
            "forbidden_facts": [], "dataset_version": "test-v1", "label_source": "human-authored-test",
        }],
    )
    trace_source.write_text(json.dumps({
        "details": {
            "hybrid": [{"case_id": "a", "trace": {
                "query": "q", "requested_strategy": "hybrid", "actual_strategy": "hybrid",
                "final_selected_chunks": [{"chunk": {"chunk_id": "a::0", "text": "x", "document_id": "a.md",
                    "chunk_index": 0, "section_path": None, "metadata": {}},
                    "final_rank": 1}],
            }}]
        }
    }), encoding="utf-8")
    result = run_suite("retrieval", dataset=golden, trace_source=trace_source,
                       manifest_dir=tmp_path / "m", output_dir=tmp_path / "o")
    assert result["summary"]["counts"]["answer"] == 1
    assert result["manifest"]
    assert result["result"]
