import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.run_ablation import evaluate_strategy, load_or_generate_golden


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluation" / "datasets" / "mindgraph_golden.jsonl"


def test_enterprise_golden_is_public_independent_and_resolvable() -> None:
    cases = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert len(cases) >= 12
    assert len({case["case_id"] for case in cases}) == len(cases)
    assert {case["expected_behavior"] for case in cases} >= {"answer", "abstain"}
    assert {case["category"] for case in cases} >= {"version", "cross_policy", "no_answer"}
    assert all(case["label_source"] == "human-authored-from-demo-vault" for case in cases)

    serialized = DATASET.read_text(encoding="utf-8")
    assert "D:/" not in serialized
    assert "D:\\" not in serialized
    assert ".workbuddy" not in serialized
    assert "抖音" not in serialized

    for case in cases:
        for vault_path in case["gold_vault_paths"]:
            assert (ROOT / "demo-vault" / vault_path).is_file(), (case["case_id"], vault_path)


def test_runtime_database_cannot_overwrite_independent_golden() -> None:
    with pytest.raises(ValueError, match="独立 Golden Set"):
        load_or_generate_golden(None, DATASET, force=True)


def test_retrieval_metrics_exclude_abstain_cases() -> None:
    class Pipeline:
        calls = 0

        def retrieve(self, question, strategy, graph_enabled):
            del question, strategy, graph_enabled
            self.calls += 1
            chunk = SimpleNamespace(metadata={"vault_path": "policies/expense-general-v2.md"})
            return SimpleNamespace(final_selected_chunks=[SimpleNamespace(chunk=chunk)])

    pipeline = Pipeline()
    metrics = evaluate_strategy(
        pipeline,
        [
            {
                "question": "提交期限？",
                "expected_behavior": "answer",
                "gold_vault_paths": ["policies/expense-general-v2.md"],
            },
            {"question": "宠物医疗？", "expected_behavior": "abstain", "gold_vault_paths": []},
        ],
        "hybrid",
        False,
    )

    assert pipeline.calls == 1
    assert metrics["sample_size"] == 1
    assert metrics["recall_at_1"] == 1.0
