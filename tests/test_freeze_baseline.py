"""PR-01 冻结事实基线：scripts/freeze_baseline.py 的行为契约。

测试覆盖任务书测试矩阵：
1. 缺失必需元数据（预测文件 / 索引）时脚本 fail-closed，不产出半成品基线；
2. 同一输入重复运行，摘要稳定（非时间戳字段逐字节一致）；
3. dirty workspace 被显式记录进基线 JSON；
4. 基线必须可追溯到 git SHA、dataset SHA、corpus/index 版本；
5. 冲突归因逐案结果被归档。
"""

from __future__ import annotations

import importlib
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


_GOLDEN_DEFAULTS: dict[str, Any] = {
    "query_type": "exact_fact",
    "difficulty": "easy",
    "expected_route": "factual",
    "graph_needed": False,
    "acl_context": {},
    "source": "human-authored-test",
    "validation_status": "approved",
    "notes": "freeze-baseline fixture",
}


def _golden_row(**overrides: Any) -> dict[str, Any]:
    """构造一条满足 golden 契约的记录；缺字段会被 validate_golden_cases 拒绝。"""
    row = {**_GOLDEN_DEFAULTS, **overrides}
    missing = [key for key in _REQUIRED_GOLDEN_FIELDS if key not in row]
    assert not missing, f"golden fixture missing keys: {missing}"
    return row


def _write_golden(path: Path, rows: list[dict[str, Any]]) -> None:
    payload = [_golden_row(**row) for row in rows]
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


# P0-4 用：一条满足 golden 契约的 answer 案例（evaluator 会做契约校验）。
_IDENTITY_CASE = _golden_row(
    case_id="case-identity",
    question="差旅费报销时限是多久？",
    category="fact",
    split="development",
    expected_behavior="answer",
    gold_vault_paths=["policies/travel.md"],
    required_facts=["fixture"],
    forbidden_facts=[],
    dataset_version="freeze-test-v1",
    label_source="human-authored-test",
)


def _make_trace(case: dict[str, Any]) -> Any:
    """构造一个能通过 retrieval evaluator 的最小 trace（answer 案例带命中）。

    导入侧必须与 evaluator 一致：evaluator 的权威侧是生产代码使用的
    ``retrieval.types``（``src/`` 无 ``__init__.py``，``src.retrieval.types``
    是同一个文件的第二身份，两边混用会让 isinstance 恒为 False）。
    """
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


def test_freeze_baseline_fails_closed_when_predictions_missing(tmp_path: Path) -> None:
    """测试矩阵 1：缺失必需元数据时脚本失败，不产出基线文件。"""
    golden = tmp_path / "golden.jsonl"
    _write_golden(golden, GOLDEN_CASES)
    output_dir = tmp_path / "baseline"
    from scripts import freeze_baseline

    with pytest.raises(FileNotFoundError):
        freeze_baseline.collect_baseline(
            golden=golden,
            predictions=tmp_path / "missing.jsonl",
            retrieve=None,
            output_dir=output_dir,
        )
    assert not list(output_dir.glob("*.json")) if output_dir.exists() else True
    assert not list(output_dir.glob("*.md")) if output_dir.exists() else True


def test_freeze_baseline_fails_closed_when_traces_incomplete(fixture_root: dict[str, Path]) -> None:
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
    # 时间戳与**延迟类**字段由运行环境决定（CPU 负载 / 预热 / 磁盘缓存），
    # 不属于被测系统的属性；剔除后其余部分必须逐字节相等 —— 这就是 PR-01
    # 「同一输入重复运行摘要稳定」的可验证形式（名单集中在 VOLATILE_PATHS）。
    assert freeze_baseline.strip_volatile(first) == freeze_baseline.strip_volatile(second)
    # 反向保证：稳定不是因为「什么都没比」。质量指标必须真的在里面。
    assert freeze_baseline.strip_volatile(first)["retrieval_layer"]["summary"]["recall_at_k"] is not None
    assert freeze_baseline.strip_volatile(first)["answer_layer"]["per_case"]


def test_strip_volatile_removes_only_declared_fields() -> None:
    """`strip_volatile` 的边界：只剔延迟与时间戳，质量/用量字段一个都不能少。

    这条守的是「基线还有比对价值」：如果哪天有人图省事把 `operational` 整块
    或 `retrieval_layer` 整块剔掉，摘要当然"稳定"了，但基线也就没用了。
    """
    from scripts import freeze_baseline

    sample: dict[str, Any] = {
        "run_id": "r1",
        "captured_at": "2026-09-11T00:00:00Z",
        "dataset": {"sha256": "abc", "sha256_method": "canonical-jsonl-source-line-v1"},
        "index": {"version": "mg-x"},
        "retrieval_layer": {
            "summary": {"recall_at_k": 0.9, "p50_retrieval_ms": 1.0, "p95_retrieval_ms": 2.0},
            "details": [{"case_id": "c1", "scored": True, "total_retrieval_ms": 3.0}],
        },
        "answer_layer": {
            "operational": {"mean_total_latency_ms": 4.0, "mean_total_tokens": 5.0},
            "metrics": {"citation_precision": 0.8},
        },
    }
    stripped = freeze_baseline.strip_volatile(sample)

    assert "run_id" not in stripped
    assert "captured_at" not in stripped
    assert "p50_retrieval_ms" not in stripped["retrieval_layer"]["summary"]
    assert "p95_retrieval_ms" not in stripped["retrieval_layer"]["summary"]
    assert "total_retrieval_ms" not in stripped["retrieval_layer"]["details"][0]
    assert "mean_total_latency_ms" not in stripped["answer_layer"]["operational"]

    # 必须保留的（基线之所以有用的部分）
    assert stripped["retrieval_layer"]["summary"]["recall_at_k"] == 0.9
    assert stripped["retrieval_layer"]["details"][0]["scored"] is True
    assert stripped["answer_layer"]["operational"]["mean_total_tokens"] == 5.0
    assert stripped["answer_layer"]["metrics"]["citation_precision"] == 0.8
    assert stripped["dataset"]["sha256"] == "abc"
    assert stripped["index"]["version"] == "mg-x"

    # 不修改入参（避免调用方拿到被掏空的 payload）
    assert sample["run_id"] == "r1"
    assert sample["retrieval_layer"]["summary"]["p50_retrieval_ms"] == 1.0


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
    # 该 fixture 是 abstain + 空 Gold（数据契约禁止 abstain 带 gold_vault_paths），
    # 按新口径属 not_a_conflict → 不计分。这里只保证「逐案可定位 + 类别被归档」；
    # 判分本身由 tests/test_conflict_scoring.py 覆盖。
    assert conflict_cases[0]["conflict_kind"] == "not_a_conflict"
    assert conflict_cases[0]["conflict_accuracy"] is None


def test_freeze_baseline_cli_fails_closed_when_predictions_missing(fixture_root: dict[str, Path]) -> None:
    """CLI 冒烟：predictions 缺失时退出码非 0、错误可读、且不写任何产物。

    脚本没有 --dry-run 开关；「不产半成品」由 fail-closed 保证而非 dry-run。
    """
    cli_out = fixture_root["tmp"] / "cli-out"
    completed = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--golden", str(fixture_root["golden"]),
         "--predictions", str(fixture_root["tmp"] / "missing.jsonl"),
         "--output-dir", str(cli_out)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert completed.returncode != 0
    combined = (completed.stderr or "") + (completed.stdout or "")
    assert "missing" in combined.lower() or "filenotfound" in combined.lower()
    assert not cli_out.exists()


# ---------------------------------------------------------------------------
# PR-01 补强：现有 7 个用例只覆盖任务书字面要求，抓不到以下四类缺陷。
# ---------------------------------------------------------------------------


def test_retrieval_trace_identity_single_across_import_paths() -> None:
    """P0-4：`src/` 无 __init__.py → 同一文件两个模块身份，isinstance 恒为 False。

    生产管线返回裸 `retrieval.types.RetrievalTrace`；若评测器绑定到
    `src.retrieval.types.RetrievalTrace`，`evaluate_retrieval_cases` 的
    isinstance 检查就永远失败、评测静默失效（修复前靠调用方 monkey-patch 绕过）。
    评测器作为入口先导入，别名因此确定性地指向生产侧模块。
    """
    from evaluation import mindgraph_retrieval_eval as evaluator
    import retrieval.types as production_identity

    assert evaluator.RetrievalTrace is production_identity.RetrievalTrace
    assert evaluator.RetrievalTrace.__module__ == production_identity.__name__

    # 别名写回后，`src.retrieval.types` 必须解析到同一个模块对象
    import src.retrieval.types as aliased

    assert aliased is production_identity


@pytest.mark.parametrize("import_path", ["retrieval.types", "src.retrieval.types"])
def test_evaluator_accepts_trace_from_either_import_identity(import_path: str) -> None:
    """P0-4 功能回归：两条导入路径构造的 trace 都必须被 evaluator 接受。

    修复前用 `retrieval.types` 侧构造 trace 会抛
    TypeError: case_id '…': retrieve must return RetrievalTrace。
    """
    from evaluation.mindgraph_retrieval_eval import evaluate_retrieval_cases

    types_module = importlib.import_module(import_path)
    chunk = types_module.Chunk(
        chunk_id="policies/travel.md::1", text="fixture", document_id="policies/travel.md",
        chunk_index=1, section_path=None, metadata={"vault_path": "policies/travel.md"},
    )
    trace = types_module.RetrievalTrace(
        query="差旅费报销时限是多久？", requested_strategy="hybrid", actual_strategy="hybrid",
        candidate_counts={"dense": 1, "sparse": 1, "fused": 1, "final": 1},
        final_selected_chunks=[types_module.RetrievalCandidate(chunk=chunk, final_rank=1)],
        latency_ms={"total_retrieval_ms": 1.0},
    )
    report = evaluate_retrieval_cases([_IDENTITY_CASE], lambda _case: trace, top_k=5)
    assert report["summary"]["recall_at_k"] == 1.0


def test_freeze_baseline_records_retrieval_config() -> None:
    """P0-2 / guardrail §4：基线必须绑定 chunking/embedding/reranker 等版本。

    直接调用真实 `_retrieval_config`（不 monkeypatch），确保它读的是运行时真值。
    """
    from scripts import freeze_baseline

    config = freeze_baseline._retrieval_config(top_k=5)
    assert config["missing_required_keys"] == []
    assert config["strategy"] == "hybrid"
    assert config["top_k"] == 5
    assert config["dense_model"]
    assert config["sparse"] == "bm25"
    assert config["fusion"] == "rrf"
    assert config["chunk_size"] and config["chunk_overlap"]
    assert isinstance(config["reranker_enabled"], bool)
    assert config["bm25_k1"] is not None and config["bm25_b"] is not None
    assert config["rrf_constant"] is not None


def test_freeze_baseline_fails_closed_when_config_incomplete(
    fixture_root: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """P0-2 fail-closed：配置读不出来时拒绝产出基线，不写「看起来完整」的配置块。"""
    from scripts import freeze_baseline

    monkeypatch.setattr(
        freeze_baseline, "_retrieval_config",
        lambda top_k: {"strategy": None, "missing_required_keys": ["strategy", "dense_model"]},
    )
    out = fixture_root["tmp"] / "out-no-config"
    with pytest.raises(RuntimeError, match="retrieval config incomplete"):
        freeze_baseline.collect_baseline(
            golden=fixture_root["golden"],
            predictions=fixture_root["predictions"],
            retrieve=lambda case: _make_trace(case),
            output_dir=out,
        )
    assert not list(out.glob("*.json")) if out.exists() else True


def test_freeze_baseline_declares_dataset_digest_method(fixture_root: dict[str, Path]) -> None:
    """P0-3：裸字节哈希随 core.autocrlf 变值，跨平台不可比 → 必须声明口径。"""
    from scripts import freeze_baseline

    result = freeze_baseline.collect_baseline(
        golden=fixture_root["golden"],
        predictions=fixture_root["predictions"],
        retrieve=lambda case: _make_trace(case),
        output_dir=fixture_root["tmp"] / "out-digest",
    )
    payload = json.loads(Path(result["baseline_path"]).read_text(encoding="utf-8"))
    assert payload["dataset"]["sha256_method"] == "canonical-jsonl-source-line-v1"
    # 与 GOLDEN_DATASET_CARD 声明的口径一致（同一函数、同一算法）
    from evaluation.mindgraph_retrieval_eval import dataset_sha256

    assert payload["dataset"]["sha256"] == dataset_sha256(fixture_root["golden"])
    # 卡内口径必须可被机器读到，避免「数值对得上但不知对的是哪种口径」
    assert "canonical-jsonl-source-line-v1" in (
        freeze_baseline.PROJECT_ROOT / "docs" / "GOLDEN_DATASET_CARD.md"
    ).read_text(encoding="utf-8")


def test_freeze_baseline_declares_index_root_and_exposes_both_roots(
    fixture_root: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """P0-1：两个同名 CURRENT 索引根（chunk 数相差数倍）必须显式声明与暴露。"""
    from scripts import freeze_baseline

    payload = json.loads(
        Path(
            freeze_baseline.collect_baseline(
                golden=fixture_root["golden"],
                predictions=fixture_root["predictions"],
                retrieve=lambda case: _make_trace(case),
                output_dir=fixture_root["tmp"] / "out-roots",
            )["baseline_path"]
        ).read_text(encoding="utf-8")
    )
    index = payload["index"]
    assert index["root"] == "data/mindgraph_indexes"
    assert index["index_root_policy"] == "mindgraph_pipeline_root_v1"

    labels = {entry["label"] for entry in index["known_roots"]}
    assert "authoritative_for_this_baseline" in labels
    # 另一个根必须**指名它服务的是哪套数据集**，而不是含糊的「evaluation_service 在用」——
    # 它服务的是 expense_qa_v1，与 golden v2 的标签交集为 0（见 tests/test_index_root_registry.py），
    # 所以它不是本基线的对照物，reader 不该拿它的版本号去比。
    assert any(label.startswith("serves_other_dataset:") for label in labels), labels
    assert all(entry.get("bound_dataset") for entry in index["known_roots"]), index["known_roots"]
    roots = {entry["root"] for entry in index["known_roots"]}
    assert roots == {"data/mindgraph_indexes", "data/retrieval_indexes"}
    # 两个根必须给出可区分的版本，否则 reader 无法判断是否可比
    authoritative = next(
        entry for entry in index["known_roots"]
        if entry["label"] == "authoritative_for_this_baseline"
    )
    assert authoritative["version"] == index["version"]
    # 权威根必须与名义上的另一个根指向不同版本——相同就意味着索引根已收敛，
    # 那时 index_root_policy 与已知边界说明都要重写。
    others = [e for e in index["known_roots"] if e["label"] != "authoritative_for_this_baseline"]
    assert all(entry["version"] != index["version"] for entry in others)
