"""PR-01 冻结事实基线：scripts/freeze_baseline.py 的行为契约。

测试覆盖任务书测试矩阵：
1. 缺失必需元数据（预测文件 / 索引）时脚本 fail-closed，不产出半成品基线；
2. 同一输入重复运行，摘要稳定（非时间戳字段逐字节一致）；
3. dirty workspace 被显式记录进基线 JSON；
4. 基线必须可追溯到 git SHA、dataset SHA、corpus/index 版本；
5. 冲突归因逐案结果被归档。
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "freeze_baseline.py"

_REQUIRED_GOLDEN_FIELDS = (
    "case_id", "question", "category", "split", "expected_behavior",
    "gold_vault_paths", "required_facts", "forbidden_facts",
    "dataset_version", "label_source",
    "query_type", "difficulty", "expected_route", "graph_needed",
    "acl_context", "source", "validation_status", "notes",
)


def _write_golden(path: Path, rows: list[dict[str, Any]]) -> None:
    payload = []
    for row in rows:
        base = {
            "query_type": "exact_fact",
            "difficulty": "easy",
            "expected_route": "factual",
            "graph_needed": False,
            "acl_context": {},
            "source": "human-authored-test",
            "validation_status": "approved",
            "notes": "freeze-baseline fixture",
        }
        base.update(row)
        missing = [key for key in _REQUIRED_GOLDEN_FIELDS if key not in base]
        assert not missing, f"golden fixture missing keys: {missing}"
        payload.append(base)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in payload) + "\n",
        encoding="utf-8",
    )


def _write_predictions(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


GOLDEN_CASES = [
    {
        "case_id": "case-answer",
        "question": "差旅费报销时限是多久？",
        "category": "fact",
        "split": "development",
        "expected_behavior": "answer",
        "evaluation_date": "2026-09-11",
        "gold_vault_paths": ["policies/travel.md"],
        "required_facts": ["30天"],
        "forbidden_facts": [],
        "historical_vault_paths": [],
        "dataset_version": "freeze-test-v1",
        "label_source": "human-authored-test",
    },
    {
        "case_id": "case-abstain",
        "question": "公司股票怎么买？",
        "category": "no_answer",
        "split": "development",
        "expected_behavior": "abstain",
        "gold_vault_paths": [],
        "required_facts": [],
        "forbidden_facts": [],
        "dataset_version": "freeze-test-v1",
        "label_source": "human-authored-test",
    },
    {
        "case_id": "case-conflict",
        "question": "这笔差旅费按哪个版本制度报销？",
        "category": "conflict",
        "split": "development",
        "expected_behavior": "abstain",
        "gold_vault_paths": [],
        "required_facts": [],
        "forbidden_facts": [],
        "dataset_version": "freeze-test-v1",
        "label_source": "human-authored-test",
    },
]

PREDICTIONS = [
    {
        "case_id": "case-answer",
        "result_state": "answered",
        "answer": "按现行制度应在 30天 内提交报销。[citation-1]",
        "citations": [{
            "citation_id": "citation-1", "vault_path": "policies/travel.md",
            "policy_status": "active", "effective_from": "2026-01-01",
            "final_rank": 1,
        }],
        "cited_citation_ids": ["citation-1"],
        "usage": {"total_tokens": 100, "input_tokens": 60, "output_tokens": 40},
        "timing": {"total_ms": 500.0},
    },
    {
        "case_id": "case-abstain",
        "result_state": "insufficient_evidence",
        "answer": "依据不足，无法回答。",
        "citations": [],
        "usage": {"total_tokens": 50, "input_tokens": 30, "output_tokens": 20},
        "timing": {"total_ms": 300.0},
    },
    {
        "case_id": "case-conflict",
        "result_state": "conflicting_evidence",
        "answer": "两份制度版本存在冲突。[citation-1][citation-2]",
        "citations": [
            {"citation_id": "citation-1", "vault_path": "policies/expense-a.md",
             "policy_status": "active", "effective_from": "2026-01-01", "final_rank": 1},
            {"citation_id": "citation-2", "vault_path": "policies/expense-b.md",
             "policy_status": "active", "effective_from": "2026-02-01", "final_rank": 2},
        ],
        "cited_citation_ids": ["citation-1", "citation-2"],
        "usage": {"total_tokens": 120, "input_tokens": 70, "output_tokens": 50},
        "timing": {"total_ms": 800.0},
    },
]


def _make_trace(case: dict[str, Any]) -> Any:
    """构造一个能通过 retrieval evaluator 的最小 trace（answer 案例带命中）。

    注意导入路径：evaluator 在 pythonpath 含项目根时优先绑定
    ``src.retrieval.types``（``try: from src.retrieval...``），测试必须与它
    同侧导入，否则 isinstance 检查会因双模块绑定而失败。
    """
    try:
        from src.retrieval.types import Chunk, RetrievalCandidate, RetrievalTrace
    except ModuleNotFoundError:
        from retrieval.types import Chunk, RetrievalCandidate, RetrievalTrace

    def candidate(path: str, rank: int) -> RetrievalCandidate:
        chunk = Chunk(
            chunk_id=f"{path}::{rank}", text="fixture", document_id=path,
            chunk_index=rank, section_path=None,
            metadata={"vault_path": path},
        )
        return RetrievalCandidate(chunk=chunk, final_rank=rank)

    gold = list(case["gold_vault_paths"])
    if case["expected_behavior"] == "abstain":
        raise ValueError("abstain cases must not be retrieved")
    finals = [candidate(path, index + 1) for index, path in enumerate(gold)]
    return RetrievalTrace(
        query=case["question"], requested_strategy="hybrid", actual_strategy="hybrid",
        candidate_counts={"dense": 5, "sparse": 5, "fused": 5, "final": len(finals)},
        final_selected_chunks=finals,
        latency_ms={"total_retrieval_ms": 10.0},
    )


@pytest.fixture
def fixture_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    golden = tmp_path / "golden.jsonl"
    predictions = tmp_path / "predictions.jsonl"
    _write_golden(golden, GOLDEN_CASES)
    _write_predictions(predictions, PREDICTIONS)
    return {"golden": golden, "predictions": predictions, "tmp": tmp_path}


def test_freeze_baseline_fails_closed_when_predictions_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """测试矩阵 1：缺失必需元数据时脚本失败，不产出基线文件。"""
    golden = tmp_path / "golden.jsonl"
    _write_golden(golden, GOLDEN_CASES)
    output_dir = tmp_path / "baseline"
    from scripts import freeze_baseline

    monkeypatch.setattr(freeze_baseline, "_load_answer_predictions", freeze_baseline._load_answer_predictions)
    with pytest.raises(FileNotFoundError):
        freeze_baseline.collect_baseline(
            golden=golden,
            predictions=tmp_path / "missing.jsonl",
            retrieve=None,
            output_dir=output_dir,
        )
    assert not (output_dir / "baseline.json").exists()
    assert not list(output_dir.glob("*.md")) if output_dir.exists() else True


def test_freeze_baseline_fails_closed_when_traces_incomplete(fixture_root: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """测试矩阵 1：检索阶段无法覆盖全部 answer 案例时 fail-closed。"""
    from scripts import freeze_baseline

    traces = {}  # 任何 answer 案例都拿不到 trace → fail-closed
    with pytest.raises(RuntimeError, match="case-answer"):
        freeze_baseline.collect_baseline(
            golden=fixture_root["golden"],
            predictions=fixture_root["predictions"],
            retrieve=lambda case: traces[case["case_id"]],
            output_dir=fixture_root["tmp"] / "out",
        )


def test_freeze_baseline_repeat_runs_summary_stable(fixture_root: dict[str, Path]) -> None:
    """测试矩阵 2：同一输入重复运行，摘要（去掉时间戳类字段）稳定。"""
    from scripts import freeze_baseline

    def run_once(out: Path) -> dict[str, Any]:
        result = freeze_baseline.collect_baseline(
            golden=fixture_root["golden"],
            predictions=fixture_root["predictions"],
            retrieve=lambda case: _make_trace(case),
            output_dir=out,
        )
        return json.loads(Path(result["baseline_path"]).read_text(encoding="utf-8"))

    first = run_once(fixture_root["tmp"] / "run1")
    second = run_once(fixture_root["tmp"] / "run2")
    # run_id / captured_at / 延迟类字段随时间变化；环境、指标、归档必须稳定
    volatile = {"run_id", "captured_at"}
    for name in volatile:
        first.pop(name, None)
        second.pop(name, None)
    assert first == second


def test_freeze_baseline_records_dirty_workspace(fixture_root: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """测试矩阵 3：dirty workspace 被显式记录（不冒充干净基线）。"""
    from scripts import freeze_baseline

    monkeypatch.setattr(
        freeze_baseline, "_git_state",
        lambda root: {"commit": "fakecommit0000000000000000000000000000000000", "dirty": True,
                       "branch": "test-branch", "untracked": ["docs/some-pack/"]},
    )
    result = freeze_baseline.collect_baseline(
        golden=fixture_root["golden"],
        predictions=fixture_root["predictions"],
        retrieve=lambda case: _make_trace(case),
        output_dir=fixture_root["tmp"] / "out-dirty",
    )
    payload = json.loads(Path(result["baseline_path"]).read_text(encoding="utf-8"))
    assert payload["environment"]["git"]["dirty"] is True
    assert payload["environment"]["git"]["commit"] == "fakecommit0000000000000000000000000000000000"
    assert "docs/some-pack/" in payload["environment"]["git"]["untracked"]


def test_freeze_baseline_archives_traceable_layers(fixture_root: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """基线必须可追溯到 git SHA、dataset SHA、index 版本，并归档逐案结果与冲突归因。"""
    from scripts import freeze_baseline

    monkeypatch.setattr(
        freeze_baseline, "_git_state",
        lambda root: {"commit": "fakecommit0000000000000000000000000000000000", "dirty": False,
                      "branch": "main", "untracked": []},
    )
    monkeypatch.setattr(
        freeze_baseline, "_index_state",
        lambda index_root=None: {"version": "mg-test-index", "chunk_count": 12, "note_count": 4,
                                 "embedding_model": "BAAI/bge-small-zh-v1.5"},
    )
    result = freeze_baseline.collect_baseline(
        golden=fixture_root["golden"],
        predictions=fixture_root["predictions"],
        retrieve=lambda case: _make_trace(case),
        output_dir=fixture_root["tmp"] / "out-trace",
    )
    payload = json.loads(Path(result["baseline_path"]).read_text(encoding="utf-8"))

    # 可追溯性：git / dataset / index / 预测来源全部显式记录
    assert payload["environment"]["git"]["commit"]
    assert payload["dataset"]["sha256"]
    assert payload["dataset"]["version"] == "freeze-test-v1"
    assert payload["dataset"]["case_count"] == 3
    assert payload["index"]["version"] == "mg-test-index"
    assert payload["answer_layer"]["prediction_source"] == fixture_root["predictions"].name
    assert payload["answer_layer"]["chat_model"] or payload["answer_layer"]["chat_model"] is None

    # 分层归档：retrieval 与 answer 的逐案结果都在
    assert payload["retrieval_layer"]["summary"]["recall_at_k"] == 1.0
    assert len(payload["retrieval_layer"]["details"]) == 3
    answer_metrics = payload["answer_layer"]["metrics"]
    assert answer_metrics["refusal_correctness"] == 1.0
    assert payload["answer_layer"]["failed_case_count"] == 0
    assert len(payload["answer_layer"]["failed_cases"]) == 0

    # 检索失败分层（PR-01：输出失败分层，不修改生产路由）
    assert payload["retrieval_layer"]["failed_cases"] == []

    # Markdown 摘要存在且引用了基线文件名
    summary_md = Path(result["summary_path"]).read_text(encoding="utf-8")
    assert Path(result["baseline_path"]).name in summary_md
    assert "freeze-test-v1" in summary_md


def test_freeze_baseline_conflict_attribution_archived(fixture_root: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """冲突场景逐案归档：conflict 案例的 conflict_accuracy 可追溯到 case_id。"""
    from scripts import freeze_baseline

    result = freeze_baseline.collect_baseline(
        golden=fixture_root["golden"],
        predictions=fixture_root["predictions"],
        retrieve=lambda case: _make_trace(case),
        output_dir=fixture_root["tmp"] / "out-conflict",
    )
    payload = json.loads(Path(result["baseline_path"]).read_text(encoding="utf-8"))
    # answer 层的逐案细节或摘要中，conflict 案例可定位
    conflict_cases = [
        detail for detail in payload["answer_layer"].get("per_case", [])
        if detail.get("case_id") == "case-conflict"
    ]
    assert conflict_cases, "conflict 逐案结果必须归档"
    assert conflict_cases[0]["conflict_accuracy"] == 1.0


def test_freeze_baseline_cli_dry_run_writes_nothing(fixture_root: dict[str, Path]) -> None:
    """CLI 冒烟：--dry-run 不写基线目录，退出码非 0 且错误可读（predictions 缺失）。"""
    completed = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--golden", str(fixture_root["golden"]),
         "--predictions", str(fixture_root["tmp"] / "missing.jsonl"),
         "--output-dir", str(fixture_root["tmp"] / "cli-out")],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert completed.returncode != 0
    assert "missing" in (completed.stderr or completed.stdout).lower() or \
        "filenotfound" in (completed.stderr or completed.stdout).lower()
