"""迁 V2 门禁：双栈分派 + 防「静默空指标」（2026-09-11）。

背景：``/api/v1/evaluations`` 原本写死只能跑历史报销栈（``expense_qa_v1``，chunk 级
标签、``data/retrieval_indexes``）。本次让它**认得两套栈**：线上栈
（``mindgraph_golden_v2``，文档级标签、``data/mindgraph_indexes``，与线上问答同源）
与历史栈。

**本次不动对外默认行为**：``EvaluationRunCreate.dataset_name`` 的默认值仍是
``expense_qa_v1``（改默认入口 = 改 API 行为，属须单独授权的红线项）。要跑线上栈
必须显式传 ``ONLINE_DATASET_NAME``。第 1 节就是这条边界的守门人。

**分派的难点不是"跑不通"，而是"跑得通但测错了系统"**：v1 的 ``execute()`` 读
``result["summary"][strategy]`` / ``result["per_category"]`` / ``result["details"]``，
而 v2 的 ``evaluate_retrieval_cases`` 返回扁平 summary + list 型 details。
只换数据源不换解析 → ``summary_metrics = {}`` 且 run 状态仍是 ``completed``
（已实测复现）。本文件把这类失败变成红灯。

除端到端用假 RetrievalTrace 外，标签/索引版本类断言直接读磁盘现状，不做猜测。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from application import evaluation_service as evaluation_service_module
from application.evaluation_service import (
    ONLINE_DATASET_NAME,
    V1_EVALUATOR,
    V2_EVALUATOR,
    EvaluationService,
    _v2_failure_detail,
    _v2_summary_metrics,
    resolve_dataset,
)
from application.index_metadata import index_root_spec
from domain.models import EvaluationRunCreate
from infrastructure.database import ProductDatabase
from retrieval.types import Chunk, RetrievalCandidate, RetrievalTrace

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ── 假检索：只造 RetrievalTrace，不碰索引、模型、网络 ──────────────────────────


def _candidate(vault_path: str, index: int) -> RetrievalCandidate:
    chunk = Chunk(
        chunk_id=f"{vault_path}::{index}",
        text=f"evidence {index}",
        document_id=vault_path,
        chunk_index=index,
        section_path=None,
        metadata={"vault_path": vault_path},
    )
    return RetrievalCandidate(chunk=chunk)


def _trace_for(case: dict, top_k: int, *, hit: bool) -> RetrievalTrace:
    gold = list(case.get("gold_vault_paths") or [])
    filler = [f"policies/__filler-{position}.md" for position in range(top_k)]
    paths = (gold + filler)[:top_k] if hit else ([f"policies/__miss-{i}.md" for i in range(top_k)])
    return RetrievalTrace(
        query=case["question"],
        requested_strategy="hybrid",
        actual_strategy="hybrid",
        final_selected_chunks=[_candidate(path, index) for index, path in enumerate(paths)],
        latency_ms={"total_retrieval_ms": 1.0},
    )


def fake_retrieve_factory(*, hit: bool = True):
    """返回一个合法的 ``RetrieveFactory``：不建索引、不加载模型。"""

    def factory(strategy: str, top_k: int, graph_enabled: bool):
        return lambda case: _trace_for(case, top_k, hit=hit)

    return factory


@pytest.fixture
def service(tmp_path, monkeypatch) -> EvaluationService:
    monkeypatch.setattr(evaluation_service_module, "_V2_RESULT_ROOT", tmp_path / "v2_results")
    database = ProductDatabase(tmp_path / "evaluation.sqlite3")
    database.initialize()
    return EvaluationService(database, mindgraph_retrieve=fake_retrieve_factory())


@pytest.fixture
def db_stub(tmp_path):
    database = ProductDatabase(tmp_path / "governance.sqlite3")
    database.initialize()
    return database


# ── 1. 默认入口保持 v1 不变，v2 须显式传名 ───────────────────────────────────


def test_default_entry_is_unchanged_until_schema_change_is_authorized() -> None:
    """本模块只加**分派能力**，不动对外默认行为——这条断言就是该边界的守门人。

    改 ``EvaluationRunCreate.dataset_name`` 的默认值 = 改 API 行为（红线项，须单独
    授权）。所以：不传名字必须与迁移前**逐位相同**（走 v1）；要跑线上栈必须显式传
    ``ONLINE_DATASET_NAME``。
    """
    payload = EvaluationRunCreate()
    assert payload.dataset_name == "expense_qa_v1"
    # schema 默认值不是线上栈 —— 一旦有人顺手改成 v2，这条会立刻红
    assert payload.dataset_name != ONLINE_DATASET_NAME
    # prompt 默认值也保持原样（v1 的 "expense-policy-v1"）
    assert payload.prompt_version == "expense-policy-v1"

    # 显式传名才到达 v2
    online = EvaluationRunCreate(dataset_name=ONLINE_DATASET_NAME)
    spec, split = resolve_dataset(online.dataset_name)
    assert spec.name == ONLINE_DATASET_NAME
    assert spec.index_root == "mindgraph_indexes"
    assert spec.label_key == "vault_path"
    assert spec.gold_fields == ("gold_vault_paths",)
    assert spec.evaluator == V2_EVALUATOR
    assert split is None


def test_prompt_version_is_decided_by_the_stack_not_the_request_body(
    service: EvaluationService,
) -> None:
    """请求体的 prompt 默认值不能照抄到 v2 的 run 上。

    这是本次最容易漏的一处：对外 schema 未动 ⇒ ``payload.prompt_version`` 恒为
    ``"expense-policy-v1"``（v1 的版本号）。若 ``create`` 直接照抄，v2 的 run 会带上
    一个"看起来有效、实际没人用"的 prompt 版本 —— guardrail §4 要防的正是这种
    「指标与 prompt 版本脱钩」，脱钩的版本号比没有版本号更坏。
    """
    payload = EvaluationRunCreate(dataset_name="mindgraph_golden_v2")
    assert payload.prompt_version == "expense-policy-v1"  # schema 默认值，未改

    assert service.create(payload).prompt_version is None  # v2 不消费 prompt
    # v1 照旧：栈的默认版本号照记，历史记录读法不变
    assert service.create(
        EvaluationRunCreate(dataset_name="expense_qa_v1")
    ).prompt_version == "expense-policy-v1"


@pytest.mark.parametrize(
    ("alias", "stack", "index_root", "label_key", "evaluator", "split"),
    [
        ("mindgraph_golden_v2", "mindgraph_golden_v2", "mindgraph_indexes", "vault_path", V2_EVALUATOR, None),
        ("mindgraph_golden_v2_development", "mindgraph_golden_v2", "mindgraph_indexes", "vault_path", V2_EVALUATOR, "development"),
        ("mindgraph_golden_v2_regression", "mindgraph_golden_v2", "mindgraph_indexes", "vault_path", V2_EVALUATOR, "regression"),
        ("expense_qa_v1", "expense_qa_v1", "retrieval_indexes", "chunk_id", V1_EVALUATOR, None),
        ("expense_qa_development", "expense_qa_v1", "retrieval_indexes", "chunk_id", V1_EVALUATOR, "development"),
        ("expense_qa_regression", "expense_qa_v1", "retrieval_indexes", "chunk_id", V1_EVALUATOR, "regression"),
    ],
)
def test_every_alias_maps_to_its_own_stack(alias, stack, index_root, label_key, evaluator, split) -> None:
    """别名 → 栈的映射必须显式且唯一：数据集与索引根不许跨栈混搭。"""
    spec, resolved_split = resolve_dataset(alias)
    assert (spec.name, spec.index_root, spec.label_key, spec.evaluator, resolved_split) == (
        stack, index_root, label_key, evaluator, split,
    )


def test_unknown_dataset_is_rejected_instead_of_silently_defaulted() -> None:
    """未登记的数据集必须报错。

    迁移前这里是"按后缀猜 split，猜不出当全量"，名字本身不校验——传错名字会把请求
    静默跑到默认栈上。默认栈换成 v2 之后，静默接受就等于把 v2 请求当成别的数据集跑。
    """
    with pytest.raises(ValueError, match="unknown dataset_name"):
        resolve_dataset("expense_qa")
    with pytest.raises(ValueError, match="unknown dataset_name"):
        resolve_dataset("mindgraph_golden")


# ── 2. 每套栈在自己的索引根里选版本 ───────────────────────────────────────────


def _index_version(service: EvaluationService, dataset_name: str) -> str | None:
    spec, split = resolve_dataset(dataset_name)
    return service._compatible_index_version(spec, split)


def test_v2_index_version_comes_from_the_online_root(service: EvaluationService) -> None:
    version = _index_version(service, "mindgraph_golden_v2")
    root = PROJECT_ROOT / index_root_spec("mindgraph_indexes").root
    assert version == (root / "CURRENT").read_text(encoding="utf-8").strip()


def test_v1_index_version_is_unchanged_by_the_migration(service: EvaluationService) -> None:
    """零回归：v1 仍绑在历史根上，选的还是同一个版本。"""
    version = _index_version(service, "expense_qa_v1")
    root = PROJECT_ROOT / index_root_spec("retrieval_indexes").root
    assert version == (root / "CURRENT").read_text(encoding="utf-8").strip()
    assert version != (PROJECT_ROOT / index_root_spec("mindgraph_indexes").root / "CURRENT").read_text(encoding="utf-8").strip()


# ── 3. 防静默：v2 与 v1 的返回结构不同，必须被规格化 ─────────────────────────


def test_v2_flat_summary_is_dropped_by_the_v1_reader() -> None:
    """反向断言：**只换数据源不换解析**会得到空指标，且不抛任何异常。

    这条是上面那句"接错栈是静默的"在测试里的机器证明 —— 它断言 v1 的读法读不出
    v2 的返回。哪天有人把 ``_run_v2`` 的规格化去掉、直接返回原始 report，
    这里描述的失败就会真实发生在 API 上。
    """
    raw_report: dict[str, Any] = {
        "summary": {"recall_at_k": 0.88, "mrr": 0.8, "ndcg_at_k": 0.78},
        "details": [{"case_id": 1}],
        "sample_size": 90,
        "counts": {"answer": 76, "abstain": 14},
    }
    strategies = ["hybrid"]
    v1_style = {name: raw_report["summary"][name] for name in strategies if name in raw_report["summary"]}
    assert v1_style == {}, "v1 的读法居然读出了 v2 的指标，本断言的证据链不成立"
    assert "per_category" not in raw_report
    assert isinstance(raw_report["details"], list)


def test_v2_summary_metrics_declare_the_scored_denominator() -> None:
    """计分分母必须与样本量分开声明。

    golden v2 的 90 题里有 14 条 ``expected_behavior=abstain``，按定义不参与检索指标
    —— 均值的分母是 **76 不是 90**。只留一个数，报告就会把 76 题的成绩说成"90 题的结果"。
    """
    metrics = _v2_summary_metrics({
        "summary": {"recall_at_k": 0.8833, "precision_at_k": 0.26, "mrr": 0.8,
                    "ndcg_at_k": 0.7811, "full_set_recall": 0.8833, "mean_evidence_size": 5.0,
                    "p50_retrieval_ms": 24.8, "p95_retrieval_ms": 112.4},
        "sample_size": 90,
        "counts": {"answer": 76, "abstain": 14},
        "top_k": 5,
        "evaluator_version": "mindgraph-retrieval-v2",
        "graph_diagnostics": {"activation_rate": 0.0, "comparable_for_graph_gain": False},
    })
    assert metrics["sample_size"] == 90
    assert metrics["scored_cases"] == 76
    assert metrics["abstained_cases"] == 14
    assert metrics["recall_at_k"] == 0.8833
    assert metrics["evaluator_version"] == "mindgraph-retrieval-v2"


def test_v2_failure_detail_keeps_the_filter_key() -> None:
    """``failure_category`` 是主流程的筛选键，缺了它失败案例会被静默丢弃。"""
    detail = _v2_failure_detail(
        {"case_id": 42, "metrics": {"recall_at_k": 0.5, "precision_at_k": 0.2},
         "failure_stage": "ranked_not_final", "gold_vault_paths": ["policies/a.md"]},
        "hybrid",
    )
    assert detail["failure_category"] == "ranked_not_final"
    assert detail["case_id"] == 42
    assert detail["strategy"] == "hybrid"


# ── 4. 端到端：run 落库、指标非空、口径自证 ───────────────────────────────────


def test_execute_completes_with_non_empty_metrics(service: EvaluationService) -> None:
    run = service.create(EvaluationRunCreate(dataset_name="mindgraph_golden_v2"))
    service.execute(run.run_id)
    finished = service.get(run.run_id)

    assert finished.status == "completed", finished.error
    assert finished.summary_metrics, "v2 run 产出了空指标 —— 解析与返回结构脱钩"
    assert "hybrid" in finished.summary_metrics
    metrics = finished.summary_metrics["hybrid"]
    assert metrics["sample_size"] == 90
    assert metrics["scored_cases"] == 76
    assert metrics["evaluator_version"] == "mindgraph-retrieval-v2"
    assert metrics["recall_at_k"] == 1.0  # 假检索每题都命中 gold

    # 口径自证：run 必须能说清自己跑在哪套栈、什么口径上
    assert finished.dataset_version == "2.4.0"
    # 请求体带的是 v1 的 "expense-policy-v1"，但这条 run 跑在 v2 上 —— 记 None
    assert finished.prompt_version is None
    assert finished.index_version == (
        PROJECT_ROOT / index_root_spec("mindgraph_indexes").root / "CURRENT"
    ).read_text(encoding="utf-8").strip()
    assert finished.configuration["resolved"]["label_key"] == "vault_path"
    assert finished.configuration["resolved"]["top_k"] == 5


def test_execute_records_failures_with_their_category(service: EvaluationService, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(evaluation_service_module, "_V2_RESULT_ROOT", tmp_path / "v2_results")
    service._mindgraph_retrieve = fake_retrieve_factory(hit=False)
    run = service.create(EvaluationRunCreate(dataset_name="mindgraph_golden_v2"))
    service.execute(run.run_id)
    finished = service.get(run.run_id)

    assert finished.status == "completed", finished.error
    assert finished.summary_metrics["hybrid"]["recall_at_k"] == 0.0
    assert finished.failed_cases, "全部漏检却没有失败案例 —— failure_category 没被写进去"
    assert all(item["failure_category"] for item in finished.failed_cases)
    assert all(item["strategy"] == "hybrid" for item in finished.failed_cases)


def test_execute_fails_closed_when_metrics_are_empty(service: EvaluationService, monkeypatch) -> None:
    """护栏本身也要被证明有效：指标为空时 run 必须是 failed，不是 completed。"""
    original = service._run_v2

    def empty_result(run, spec, split):
        result = original(run, spec, split)
        result["summary"] = {}
        result["per_category"] = {}
        result["details"] = {}
        return result

    monkeypatch.setattr(service, "_run_v2", empty_result)
    run = service.create(EvaluationRunCreate(dataset_name="mindgraph_golden_v2"))
    service.execute(run.run_id)
    finished = service.get(run.run_id)

    assert finished.status == "failed"
    assert "no summary metrics" in (finished.error or "")


def test_execute_requires_a_wired_retrieval_factory(tmp_path, monkeypatch) -> None:
    """没注入检索工厂时必须是显式失败，而不是悄悄回落到别的栈。"""
    monkeypatch.setattr(evaluation_service_module, "_V2_RESULT_ROOT", tmp_path / "v2_results")
    database = ProductDatabase(tmp_path / "unwired.sqlite3")
    database.initialize()
    unwired = EvaluationService(database)
    run = unwired.create(EvaluationRunCreate(dataset_name="mindgraph_golden_v2"))
    unwired.execute(run.run_id)
    finished = unwired.get(run.run_id)

    assert finished.status == "failed"
    assert "not wired" in (finished.error or "")


def test_split_alias_restricts_the_run_to_that_split(service: EvaluationService) -> None:
    run = service.create(EvaluationRunCreate(dataset_name="mindgraph_golden_v2_regression"))
    service.execute(run.run_id)
    finished = service.get(run.run_id)

    assert finished.status == "completed", finished.error
    assert finished.summary_metrics["hybrid"]["sample_size"] == 7


# ── 5. 治理层必须认识线上评测集 ───────────────────────────────────────────────


def test_golden_v2_is_registered_into_governance(db_stub) -> None:
    """缺这一步，``/api/v1/governance/datasets`` 对**线上评测集**是空的。

    注意：登记 ≠ 改默认入口。请求体默认值仍是 ``expense_qa_v1``，本用例只验证
    治理层认得 golden v2。
    """
    from api.dependencies import ServiceContainer
    from application.evaluation_governance_service import EvaluationGovernanceService

    stub = SimpleNamespace(
        root=PROJECT_ROOT,
        database=db_stub,
        governance=EvaluationGovernanceService(db_stub),
    )
    ServiceContainer._register_mindgraph_golden_v2_datasets(stub)  # type: ignore[arg-type]

    rows = {row["dataset_id"]: row for row in db_stub.fetch_all(
        "SELECT dataset_id, version, case_count FROM datasets ORDER BY dataset_id"
    )}
    assert rows["mindgraph_golden_v2_development"]["version"] == "2.4.0"
    assert rows["mindgraph_golden_v2_development"]["case_count"] == 83
    assert rows["mindgraph_golden_v2_regression"]["version"] == "2.4.0"
    assert rows["mindgraph_golden_v2_regression"]["case_count"] == 7

    # 幂等：重复装配不得抛 ConflictError
    ServiceContainer._register_mindgraph_golden_v2_datasets(stub)  # type: ignore[arg-type]
    assert db_stub.fetch_one(
        "SELECT COUNT(*) AS total FROM datasets"
    )["total"] == 2


# ── 6. 报错自解释：索引根缺失与标签不兼容必须分开说 ───────────────────────────
# 背景（2026-09-12）：干净 worktree 缺 data/（被 gitignore）时，旧报错
# "No index version ... must belong to the same evaluation stack" 把人往代码方向带
# （实测被带偏 4 次）。现在两种原因分成两条消息，这里守住"不合并回去"。


def _spec(name: str = "mindgraph_golden_v2") -> Any:
    spec, _ = resolve_dataset(name)
    return spec


def test_missing_index_root_reports_missing_runtime_data_not_incompatible_stack(
    service: EvaluationService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """索引根不存在（干净 worktree / 未 provision 的 CI）→ 指向运行期数据，不是代码。"""
    monkeypatch.setattr(evaluation_service_module, "ROOT", tmp_path)
    monkeypatch.setattr(
        evaluation_service_module, "load_dataset_cases",
        lambda spec: [{"gold_vault_paths": ["制度/差旅.md"]}],
    )
    root = tmp_path / "data" / "mindgraph_indexes"  # 故意不建 CURRENT
    with pytest.raises(ValueError) as excinfo:
        service._compatible_index_version(_spec(), None)
    message = str(excinfo.value)
    assert "该索引根不存在或没有 CURRENT" in message
    assert "运行期数据" in message
    assert ".gitignore" in message
    assert str(root) in message
    assert "must belong to the same evaluation stack" not in message


def test_incompatible_labels_still_report_the_evaluation_stack_mismatch(
    service: EvaluationService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """根存在但标签与数据集零重叠 → 保留原判词，别误报"数据没复制"。"""
    monkeypatch.setattr(evaluation_service_module, "ROOT", tmp_path)
    monkeypatch.setattr(
        evaluation_service_module, "load_dataset_cases",
        lambda spec: [{"gold_vault_paths": ["制度/差旅.md"]}],
    )
    version_dir = tmp_path / "data" / "mindgraph_indexes" / "v-other"
    version_dir.mkdir(parents=True)
    (tmp_path / "data" / "mindgraph_indexes" / "CURRENT").write_text("v-other\n", encoding="utf-8")
    (version_dir / "chunks.json").write_text(
        '[{"chunk_id": "c1", "metadata": {"title": "考勤", "path": "制度/考勤.md"}}]',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must belong to the same evaluation stack"):
        service._compatible_index_version(_spec(), None)
