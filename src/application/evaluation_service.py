"""评测运行服务：两套评测栈的显式分派。

**为什么是分派而不是"统一到一套"**

仓库里有两套各自完整的评测栈，它们的差别是**期望证据的粒度**，不是"哪套写错了"：

======================  ==========================  ================================
栈                       数据集 / 标签粒度             索引根
======================  ==========================  ================================
v2（线上问答同源）        ``mindgraph_golden_v2``       ``data/mindgraph_indexes``
（须显式传名）            文档级 ``gold_vault_paths``    ``mg-`` 版本，25 篇 / 581 chunks
v1（legacy）             ``expense_qa_v1``             ``data/retrieval_indexes``
（历史报销评测）          chunk 级 ``gold_chunk_ids``    ``m3-`` 版本，4 篇 / 69 chunks
======================  ==========================  ================================

**默认入口仍是 v1**：``EvaluationRunCreate.dataset_name`` 的默认值保持
``expense_qa_v1`` 不变——改默认入口等于改对外 API 行为，属须单独授权的红线项。
本模块只提供**分派能力**：不传名字 = 与迁移前逐位相同的行为；要跑线上栈必须显式传
``mindgraph_golden_v2``。

实测（2026-09-11）两栈标签交集为 **0**：v1 的 ``差旅费报销管理办法.md::16`` 对不上
``mg-`` 根的 32 位 hex ID，v2 的 ``policies/expense-general-v1.md`` 也对不上 ``m3-`` 根。
所以「换根」不是无痛操作——照做会让 ``_compatible_index_version`` 抛
``ValueError: No index version is compatible with this dataset's Gold chunk labels``。

**这个模块的最大风险不是"跑不通"，而是"跑得通但测错了系统"**：两套栈的数字看起来
同样正常，接错了不会报错。因此这里做三件事：

1. ``_DATASET_ALIASES``：数据集名 → 栈，**显式登记**，未知名字直接报错
   （以前是按后缀猜 split、猜不出就当"全部"，未知名字会被静默接受）；
2. 每个栈自带 ``label_key`` / ``gold_fields`` / ``index_root``，三者在同一处声明，
   杜绝"用 A 的标签去量 B 的索引"；
3. ``execute`` 后校验 ``summary_metrics`` 非空——v2 的返回结构与 v1 不同，
   若解析写错，API 会返回**空指标且状态为 completed**（已实测复现）。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import logging
from pathlib import Path
from typing import Any
import uuid

from application.index_metadata import index_root_spec, normalize_label, version_label_set
from config import ROOT
from domain.errors import ConflictError, NotFoundError
from domain.models import EvaluationRun, EvaluationRunCreate
from evaluation.mindgraph_retrieval_eval import (
    DEFAULT_DATASET_PATH as V2_DATASET_PATH,
)
from evaluation.mindgraph_retrieval_eval import (
    dataset_sha256,
    evaluate_retrieval_cases,
    load_golden_dataset,
)
from infrastructure.database import ProductDatabase, dumps, loads

logger = logging.getLogger("mindgraph.evaluation")

#: 检索顶部 K。v2 与线上问答、``freeze_baseline`` 口径一致（默认 5）。
_DEFAULT_TOP_K = 5
#: 与 ``scripts/freeze_baseline.py`` 的 LIVE_GRAPH_ENABLED 保持一致：
#: 图扩展当前默认关闭（note_relations 只有 6 条手工种子），开了也不改变排名。
_LIVE_GRAPH_ENABLED = False
V2_EVALUATOR = "retrieval_v2"
V1_EVALUATOR = "retrieval_v1"
_V2_RESULT_ROOT = ROOT / "evaluation" / "results" / "mindgraph_v2"

#: 检索回调工厂：(strategy, top_k, graph_enabled) -> (case -> RetrievalTrace)
RetrieveFactory = Callable[[str, int, bool], Callable[[dict[str, Any]], Any]]


@dataclass(frozen=True)
class DatasetSpec:
    """一套评测栈的完整绑定：数据集 → 标签口径 → 索引根 → 评估器。

    四个字段必须同时成立才算"这个数据集跑在这套栈上"；分开写在不同文件里，
    就会出现「数据集换了、索引根没换」这类只能靠数字异常才发现的错配。
    """

    name: str
    dataset_path: Path
    index_root: str
    label_key: str
    gold_fields: tuple[str, ...]
    evaluator: str
    default_prompt_version: str | None
    description: str


_DATASET_SPECS: dict[str, DatasetSpec] = {
    "mindgraph_golden_v2": DatasetSpec(
        name="mindgraph_golden_v2",
        dataset_path=V2_DATASET_PATH,
        index_root="mindgraph_indexes",
        label_key="vault_path",
        gold_fields=("gold_vault_paths",),
        evaluator=V2_EVALUATOR,
        default_prompt_version=None,
        description="线上 MindGraph 检索管线（golden v2，文档级标签）；非请求体默认值，须显式传名",
    ),
    "expense_qa_v1": DatasetSpec(
        name="expense_qa_v1",
        dataset_path=ROOT / "evaluation" / "datasets" / "expense_qa_v1.jsonl",
        index_root="retrieval_indexes",
        label_key="chunk_id",
        gold_fields=("gold_chunk_ids",),
        evaluator=V1_EVALUATOR,
        default_prompt_version="expense-policy-v1",
        description="历史 M1/M2 评测栈（expense_qa_v1，chunk 级标签）；请求体默认值仍指向它（对外行为未改）",
    ),
}

#: 数据集别名 → (栈名, split)。显式登记，不按后缀猜。
#: ``expense_qa_development`` / ``expense_qa_regression`` 是治理层 datasets 表的注册 id
#: （见 api/dependencies.py 的 _register_builtin_datasets），历史调用方在用，故保留。
_DATASET_ALIASES: dict[str, tuple[str, str | None]] = {
    "mindgraph_golden_v2": ("mindgraph_golden_v2", None),
    "mindgraph_golden_v2_development": ("mindgraph_golden_v2", "development"),
    "mindgraph_golden_v2_regression": ("mindgraph_golden_v2", "regression"),
    "expense_qa_v1": ("expense_qa_v1", None),
    "expense_qa_development": ("expense_qa_v1", "development"),
    "expense_qa_regression": ("expense_qa_v1", "regression"),
}

#: 线上栈的数据集名（与 ``data/mindgraph_indexes`` 同源）。
#: **注意：它不是 ``EvaluationRunCreate.dataset_name`` 的默认值**——改那个默认值等于
#: 改对外 API 行为，属须单独授权的红线项。当前默认值仍是 ``expense_qa_v1``；
#: 要跑线上栈必须**显式**传这个名字。
ONLINE_DATASET_NAME = "mindgraph_golden_v2"


def resolve_dataset(dataset_name: str) -> tuple[DatasetSpec, str | None]:
    """解析 ``dataset_name`` 为（栈规格, split）；未登记即报错。

    以前这里是「按 ``_development``/``_regression`` 后缀猜 split，猜不出就返回 None
    （等于全量）」，且不校验名字——传错名字会把请求静默跑到默认栈上。

    迁移后新增了另一套栈，**默认值没动**（仍是 ``expense_qa_v1``），所以"传错名字
    被静默接受"这件事变得更危险：请求里写着 ``mindgraph_golden_v2`` 却因为拼错而被
    当成默认栈跑，出来的是另一个系统的成绩，而 run 记录上写着 v2。故未登记即报错。
    """
    entry = _DATASET_ALIASES.get(dataset_name)
    if entry is None:
        known = ", ".join(sorted(_DATASET_ALIASES))
        raise ValueError(f"unknown dataset_name {dataset_name!r}; known: {known}")
    spec_name, split = entry
    return _DATASET_SPECS[spec_name], split


def load_dataset_cases(spec: DatasetSpec) -> list[dict[str, Any]]:
    """读该栈的数据集案例。

    v1 分支**延迟导入** ``evaluation.baseline``：它顶层会拉进 ``rag_engine`` /
    ``document_loader``（历史报销链路的整套依赖），而默认的 v2 路径并不需要。
    """
    if spec.evaluator == V2_EVALUATOR:
        return load_golden_dataset(spec.dataset_path)
    from evaluation.baseline import load_dataset

    return load_dataset(spec.dataset_path)


def dataset_version_of(spec: DatasetSpec) -> str:
    """从数据集**首行**读版本号，而不是用代码里的常量。

    v1 原先写死 ``evaluation.baseline.DATASET_VERSION``（"1.0.0"），而 v2 的
    ``dataset_version`` 是 "2.4.0" 且随数据集演进。用常量会让 run 记录里的版本号与
    实际评测的数据脱钩——这正是 guardrail §4 要防的「指标与数据版本不绑定」。
    """
    try:
        with spec.dataset_path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    return str(json.loads(line).get("dataset_version") or "unknown")
    except (OSError, json.JSONDecodeError):
        pass
    return "unknown"


def _v2_summary_metrics(report: dict[str, Any]) -> dict[str, Any]:
    """把 ``evaluate_retrieval_cases`` 的扁平 summary 规整成跨栈同形状的指标字典。

    ``sample_size`` 与 ``scored_cases`` 必须同时出现：golden v2 的 90 题里有 14 条
    ``expected_behavior=abstain``，它们按定义不参与检索指标，所以**均值的分母是 76 不是 90**。
    只留一个数，报告就会把 76 题的成绩说成"90 题的结果"。
    """
    summary = report.get("summary") or {}
    counts = report.get("counts") or {}
    abstained = int(counts.get("abstain", 0) or 0)
    sample_size = int(report.get("sample_size", 0) or 0)
    diagnostics = report.get("graph_diagnostics") or {}
    return {
        "recall_at_k": summary.get("recall_at_k"),
        "precision_at_k": summary.get("precision_at_k"),
        "mrr": summary.get("mrr"),
        "ndcg_at_k": summary.get("ndcg_at_k"),
        "full_set_recall": summary.get("full_set_recall"),
        "mean_evidence_size": summary.get("mean_evidence_size"),
        "p50_retrieval_ms": summary.get("p50_retrieval_ms"),
        "p95_retrieval_ms": summary.get("p95_retrieval_ms"),
        "sample_size": sample_size,
        "scored_cases": sample_size - abstained,
        "abstained_cases": abstained,
        "top_k": report.get("top_k"),
        "evaluator_version": report.get("evaluator_version"),
        "graph_activation_rate": diagnostics.get("activation_rate"),
        "graph_comparable_for_gain": diagnostics.get("comparable_for_graph_gain"),
    }


def _v2_failure_detail(row: dict[str, Any], strategy: str) -> dict[str, Any]:
    """把 v2 的单条失败明细转成 ``evaluation_runs`` 的通用形状。

    ``failure_category`` 是主流程的筛选键（与 v1 同名），**必须存在**：缺了它，
    主流程那条 ``if "failure_category" in item`` 会把全部失败静默丢弃，
    而 v2 恰恰有十余条 ``recall_at_k < 1`` 的案例。
    """
    metrics = row.get("metrics") or {}
    return {
        "case_id": row.get("case_id"),
        "strategy": strategy,
        "failure_category": row.get("failure_stage") or "retrieval_miss",
        "recall_at_k": metrics.get("recall_at_k"),
        "precision_at_k": metrics.get("precision_at_k"),
        "gold_vault_paths": row.get("gold_vault_paths"),
        "evidence_stages": row.get("evidence_stages"),
    }


def _render_v2_report(run: EvaluationRun, result: dict[str, Any]) -> str:
    lines = [
        "# MindGraph v2 检索评测",
        "",
        f"- run_id：`{run.run_id}`",
        f"- 数据集：`{result['dataset_name']}` / `{result['dataset_version']}`",
        f"- 数据集 digest：`{str(result.get('dataset_sha256'))[:16]}…`",
        f"- 索引根：`data/{result['index_root']}` / `{result['index_version']}`",
        f"- top_k：`{result['top_k']}`　图扩展：`{result['graph_enabled']}`",
        "",
        "## 指标",
        "",
        "| 策略 | Recall@K | nDCG@K | MRR | precision@K | 计分/样本 | p50 ms | p95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for strategy, metrics in result["summary"].items():
        lines.append(
            f"| {strategy} | {metrics['recall_at_k']} | {metrics['ndcg_at_k']} | {metrics['mrr']} "
            f"| {metrics['precision_at_k']} | {metrics['scored_cases']}/{metrics['sample_size']} "
            f"| {metrics['p50_retrieval_ms']} | {metrics['p95_retrieval_ms']} |"
        )
    lines.extend([
        "",
        "> `abstain` 类案例（无期望证据、应当拒答）不参与检索指标，因此**计分数 < 样本数**。",
        "> `precision@K` 受文档级标签粒度压制（gold 通常只有 1–2 个 vault_path，而 Top-K 恒为 K 个），",
        "> 它衡量的是「证据集是否干净」，不与 Recall 同权解读。",
        "",
        "## 失败案例",
        "",
    ])
    failures = [item for rows in result["details"].values() for item in rows]
    if not failures:
        lines.append("- 无")
    for item in failures:
        lines.append(
            f"- `{item['case_id']}`（{item['strategy']}）阶段 `{item['failure_category']}` "
            f"Recall@K={item['recall_at_k']}"
        )
    lines.append("")
    return "\n".join(lines)


def _save_v2_result(run: EvaluationRun, result: dict[str, Any]) -> tuple[Path, Path]:
    """v2 结果落盘。

    不能复用 v1 的 ``save_results``：它按 ``summary[strategy].keys()`` 生成 CSV 表头、
    用 v1 专用的 ``render_report``（写死 recall_at_1/3/5、document_hit_rate），
    吃 v2 的扁平 summary 会在 ``next(iter(...)).keys()`` 上直接抛 AttributeError。
    """
    _V2_RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = _V2_RESULT_ROOT / f"{stamp}_{run.run_id}.json"
    json_path.write_text(
        json.dumps({"run_id": run.run_id, **result}, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    md_path = _V2_RESULT_ROOT / f"{stamp}_{run.run_id}.md"
    md_path.write_text(_render_v2_report(run, result), encoding="utf-8")
    return json_path, md_path


class EvaluationService:
    def __init__(
        self,
        database: ProductDatabase,
        mindgraph_retrieve: RetrieveFactory | None = None,
    ) -> None:
        self.database = database
        # 由 api/dependencies.py 注入。不能在这里直接 import api 层（会形成
        # application → api 的反向依赖），也拿不到 container 的管线缓存。
        self._mindgraph_retrieve = mindgraph_retrieve

    # ── 写路径 ────────────────────────────────────────────────────────────────

    def create(self, payload: EvaluationRunCreate) -> EvaluationRun:
        spec, split = resolve_dataset(payload.dataset_name)
        configuration = payload.model_dump(mode="json")
        # 把解析结果写进 configuration：一个 run 事后必须能自证"我当时跑在哪套栈上、
        # 用的什么 top_k"，否则同样的 dataset_name 在不同版本代码下会给出不同数字
        # 而无人察觉。
        configuration["resolved"] = {
            "dataset_spec": spec.name,
            "index_root": spec.index_root,
            "label_key": spec.label_key,
            "evaluator": spec.evaluator,
            "split": split,
            "top_k": _DEFAULT_TOP_K,
            "graph_enabled": _LIVE_GRAPH_ENABLED,
        }
        fingerprint = hashlib.sha256(dumps(configuration).encode()).hexdigest()
        active = self.database.fetch_all("SELECT configuration_json FROM evaluation_runs WHERE status IN ('queued','running')")
        if any(hashlib.sha256(row["configuration_json"].encode()).hexdigest() == fingerprint for row in active):
            raise ConflictError("An identical evaluation is already active")
        run = EvaluationRun(
            run_id=str(uuid.uuid4()), status="queued", dataset_name=payload.dataset_name,
            dataset_version=dataset_version_of(spec),
            retrieval_strategy=",".join(payload.retrieval_strategies),
            chat_model=payload.chat_model, configuration=configuration, progress_messages=["queued"],
            index_version=self._compatible_index_version(spec, split),
            # prompt 只对答案层有意义。请求体的默认值仍是 v1 的 "expense-policy-v1"
            # （对外 schema 未动），所以这里**不能**直接照抄 payload：v2 是纯检索栈，
            # 把 v1 的版本号记到 v2 的 run 上会留下一个"看起来有效、实际没人用"的
            # 标签——正是 guardrail §4 要防的「指标与 prompt 版本脱钩」。
            # 规则：由**栈**决定是否记录 prompt 版本；default_prompt_version 为 None
            # 即表示该栈不消费 prompt（见 DatasetSpec）。
            prompt_version=payload.prompt_version if spec.default_prompt_version else None,
            provider=payload.chat_provider,
        )
        self._save(run)
        logger.info(
            "evaluation_queued",
            extra={"run_id": run.run_id, "strategies": run.retrieval_strategy,
                   "dataset": spec.name, "index_root": spec.index_root, "label_key": spec.label_key},
        )
        return run

    def execute(self, run_id: str) -> None:
        run = self.get(run_id)
        run.status, run.started_at, run.progress_messages = "running", datetime.now(UTC), [*run.progress_messages, "running"]
        self._save(run, update=True)
        try:
            spec, split = resolve_dataset(run.dataset_name)
            if spec.evaluator == V2_EVALUATOR:
                result = self._run_v2(run, spec, split)
                paths: tuple[Path, ...] = _save_v2_result(run, result)
            else:
                result, paths = self._run_v1(run, split)
            selected = run.configuration["retrieval_strategies"]
            run.summary_metrics = {name: result["summary"][name] for name in selected if name in result.get("summary", {})}
            run.category_metrics = {name: result["per_category"][name] for name in selected if name in result.get("per_category", {})}
            run.failed_cases = [item for name in selected for item in result.get("details", {}).get(name, []) if "failure_category" in item]
            run.result_files = [path.name for path in paths]
            # fail-closed：指标解析写错时（例如两栈返回结构不同而沿用旧解析）会得到
            # 空字典且状态仍是 completed，外部看不出任何异常。宁可让 run 失败。
            if not run.summary_metrics:
                raise RuntimeError(
                    f"evaluation produced no summary metrics for strategies {selected!r} "
                    f"(evaluator={spec.evaluator}, dataset={spec.name})"
                )
            run.status, run.progress_messages = "completed", [*run.progress_messages, "completed"]
        except Exception as exc:
            run.status, run.error = "failed", f"{type(exc).__name__}: {exc}"
            run.progress_messages = [*run.progress_messages, "failed"]
        run.finished_at = datetime.now(UTC)
        self._save(run, update=True)
        logger.info(
            "evaluation_finished",
            extra={"run_id": run.run_id, "status": run.status, "error": run.error},
        )

    # ── 读路径 ────────────────────────────────────────────────────────────────

    def list(self) -> list[EvaluationRun]:
        return [self._row(row) for row in self.database.fetch_all("SELECT * FROM evaluation_runs ORDER BY rowid DESC")]

    def get(self, run_id: str) -> EvaluationRun:
        row = self.database.fetch_one("SELECT * FROM evaluation_runs WHERE run_id=?", (run_id,))
        if not row:
            raise NotFoundError("Evaluation run not found")
        return self._row(row)

    # ── v1（legacy）执行 ──────────────────────────────────────────────────────

    @staticmethod
    def _run_v1(run: EvaluationRun, split: str | None) -> tuple[dict[str, Any], tuple[Path, ...]]:
        """历史报销栈的分支。

        ``evaluation.retrieval_eval`` 顶层会拉进 BGE/FAISS/BM25 的整套依赖，而默认的
        v2 路径只是转发到 ``mindgraph_pipeline``。延迟导入让**不跑 legacy 的路径
        不为它付导入成本**。
        """
        from evaluation.retrieval_eval import evaluate, save_results

        result = evaluate(
            run.configuration["repetitions"], run.configuration["warmups"],
            "hybrid_rerank" in run.configuration["retrieval_strategies"],
            split=split, index_version=run.index_version,
        )
        return result, save_results(result, update_official_report=False)

    # ── v2 执行 ───────────────────────────────────────────────────────────────

    def _run_v2(
        self, run: EvaluationRun, spec: DatasetSpec, split: str | None,
    ) -> dict[str, Any]:
        """跑 v2 检索评测，产出与 v1 **同形状**的 result。

        形状对齐（``{strategy: ...}`` 而非扁平）是刻意的：``execute`` 的落库逻辑
        因此不需要按栈分叉，`evaluation_runs` 里的历史记录与新记录也能用同一套读法。
        """
        if self._mindgraph_retrieve is None:
            raise RuntimeError(
                "mindgraph retrieval factory is not wired into EvaluationService; "
                "construct it with mindgraph_retrieve=... (see api/dependencies.py)"
            )
        cases = load_dataset_cases(spec)
        if split:
            cases = [case for case in cases if case.get("split") == split]
        resolved = run.configuration.get("resolved") or {}
        top_k = int(resolved.get("top_k") or _DEFAULT_TOP_K)
        graph_enabled = bool(resolved.get("graph_enabled", _LIVE_GRAPH_ENABLED))
        digest = dataset_sha256(spec.dataset_path)
        summary: dict[str, Any] = {}
        per_category: dict[str, Any] = {}
        details: dict[str, list[dict[str, Any]]] = {}
        for strategy in run.configuration["retrieval_strategies"]:
            retrieve = self._mindgraph_retrieve(strategy, top_k, graph_enabled)
            report = evaluate_retrieval_cases(cases, retrieve, top_k=top_k, dataset_digest=digest)
            summary[strategy] = _v2_summary_metrics(report)
            per_category[strategy] = report["summary"].get("stratified") or {}
            details[strategy] = [_v2_failure_detail(row, strategy) for row in report["failed_cases"]]
        return {
            "evaluator": V2_EVALUATOR,
            "dataset_name": spec.name,
            "dataset_version": dataset_version_of(spec),
            "dataset_sha256": digest,
            "index_root": spec.index_root,
            "index_version": run.index_version,
            "top_k": top_k,
            "graph_enabled": graph_enabled,
            "summary": summary,
            "per_category": per_category,
            "details": details,
        }

    # ── 共用工具 ──────────────────────────────────────────────────────────────

    def _save(self, run: EvaluationRun, update: bool = False) -> None:
        values = (run.status, run.dataset_name, run.dataset_version, run.retrieval_strategy, run.chat_model,
            run.started_at.isoformat() if run.started_at else None, run.finished_at.isoformat() if run.finished_at else None,
            dumps(run.configuration), dumps(run.summary_metrics), dumps(run.category_metrics), dumps(run.failed_cases),
            dumps(run.result_files), dumps(run.progress_messages), run.error,
            run.index_version, run.prompt_version, run.provider)
        if update:
            self.database.execute("UPDATE evaluation_runs SET status=?,dataset_name=?,dataset_version=?,retrieval_strategy=?,chat_model=?,started_at=?,finished_at=?,configuration_json=?,summary_metrics_json=?,category_metrics_json=?,failed_cases_json=?,result_files_json=?,progress_messages_json=?,error=?,index_version=?,prompt_version=?,provider=? WHERE run_id=?", values + (run.run_id,))
        else:
            self.database.execute("""INSERT INTO evaluation_runs (
                run_id,status,dataset_name,dataset_version,retrieval_strategy,chat_model,started_at,finished_at,
                configuration_json,summary_metrics_json,category_metrics_json,failed_cases_json,result_files_json,
                progress_messages_json,error,index_version,prompt_version,provider
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (run.run_id,) + values)

    @staticmethod
    def _row(row) -> EvaluationRun:
        return EvaluationRun(run_id=row["run_id"], status=row["status"], dataset_name=row["dataset_name"],
            dataset_version=row["dataset_version"], retrieval_strategy=row["retrieval_strategy"], chat_model=row["chat_model"],
            started_at=row["started_at"], finished_at=row["finished_at"], configuration=loads(row["configuration_json"], {}),
            summary_metrics=loads(row["summary_metrics_json"], {}), category_metrics=loads(row["category_metrics_json"], {}),
            failed_cases=loads(row["failed_cases_json"], []), result_files=loads(row["result_files_json"], []),
            progress_messages=loads(row["progress_messages_json"], []), error=row["error"],
            index_version=row.get("index_version"), prompt_version=row.get("prompt_version"), provider=row.get("provider"))

    @staticmethod
    def _current_index_version(spec: DatasetSpec) -> str | None:
        root = ROOT / index_root_spec(spec.index_root).root
        try:
            return (root / "CURRENT").read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def _compatible_index_version(self, spec: DatasetSpec, split: str | None) -> str | None:
        """在该栈自己的索引根里挑一个与数据集 gold 标签重叠最大的版本。

        标签口径由 ``spec.label_key`` 决定（v1 = ``chunk_id``，v2 = ``vault_path``），
        索引侧与数据集侧都过 ``normalize_label``——两边不同口径的话，重叠数就是
        归一化差异的产物，而不是"绑没绑上"的证据。
        """
        cases = load_dataset_cases(spec)
        if split:
            cases = [case for case in cases if case.get("split") == split]
        gold = {
            normalize_label(value)
            for case in cases
            for field_name in spec.gold_fields
            for value in (case.get(field_name) or [])
            if value
        }
        index_root = ROOT / index_root_spec(spec.index_root).root
        best_version, best_score = None, (0, "", "")
        for directory in index_root.iterdir() if index_root.exists() else []:
            if not directory.is_dir():
                continue
            labels, chunk_count = version_label_set(directory, spec.label_key)
            if chunk_count == 0:
                continue
            metadata_path = directory / "metadata.json"
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
            except (OSError, json.JSONDecodeError):
                metadata = {}
            created_at = metadata.get("index_created_at") or metadata.get("created_at") or ""
            score = (len(gold & labels), created_at, directory.name)
            if score > best_score:
                best_version, best_score = directory.name, score
        if gold and best_score[0] == 0:
            # 两种原因必须分开说：① 根/数据集确实分属两套语料（真缺陷，要改配置或根）；
            # ② 根压根不存在（data/ 被 gitignore，干净检出 / 未 provision 的 CI 都这样）。
            # 合并成一句话会把人往代码方向带——2026-09-12 实测被带偏 4 次。
            if not (index_root / "CURRENT").is_file():
                raise ValueError(
                    f"No index version under data/{spec.index_root} is compatible with dataset "
                    f"{spec.name!r}: 该索引根不存在或没有 CURRENT（{index_root}）。data/ 被 "
                    ".gitignore 忽略，干净 worktree 与未 provision 的 CI 都不会检出它——"
                    "**先确认运行期数据是否已复制过去**（见 AGENTS.md「提交态复核」），"
                    "再怀疑数据集与索引根分属两套语料"
                )
            raise ValueError(
                f"No index version under data/{spec.index_root} is compatible with dataset "
                f"{spec.name!r} (label_key={spec.label_key!r}); the dataset and the index root "
                "must belong to the same evaluation stack"
            )
        return best_version or self._current_index_version(spec)
