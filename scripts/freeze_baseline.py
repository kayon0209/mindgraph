"""PR-01 冻结事实基线：把当前质量、性能、成本与索引状态固化成机器可读 JSON。

编排既有评测器（不发明新评分逻辑）：
- 检索层：对 Golden answer 案例执行当前检索（或注入的 retrieve），复用
  :func:`evaluation.mindgraph_retrieval_eval.evaluate_retrieval_cases`
  （evidence-path 口径，输出 recall/MRR/NDGW + 失败分层）；
- 答案层：对冻结预测 JSONL 执行 deterministic-answer-v2 评分，复用
  :func:`evaluation.answer_eval.evaluate_answer_predictions`
  （citation / 拒答 / 版本 / 冲突 / ACL，缺失 token/cost 记 None 不记 0）；
- 环境：git SHA / dirty / Python / SQLite / dataset SHA / index 版本 / 预测来源。

产物（默认写到 evaluation/results/baseline/，该目录在 .gitignore 中）：
- ``baseline-<UTC 时间戳>.json``：完整逐案归档 + 摘要；
- ``baseline-<UTC 时间戳>.md``：人读摘要。

设计约束（01-GLOBAL-GUARDRAILS）：
- 不修改 Golden 标签、不调 chunk/rerank/prompt、不改生产路由；
- 任何一层缺失必需元数据 → fail-closed，不产出半成品基线；
- 重复运行时摘要稳定（仅 run_id / captured_at / 延迟类字段可变）。

用法：
    python scripts/freeze_baseline.py                          # live 检索 + 指定预测集
    python scripts/freeze_baseline.py --predictions <jsonl>   # 必填：冻结预测文件
    python scripts/freeze_baseline.py --help
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
import copy
from datetime import UTC, datetime
import json
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from application.index_metadata import (  # noqa: E402
    INDEX_ROOT_REGISTRY,
    index_root_spec,
)
from evaluation.answer_eval import ANSWER_METRICS, evaluate_answer_case, evaluate_answer_predictions  # noqa: E402
from evaluation.mindgraph_retrieval_eval import (  # noqa: E402
    dataset_sha256,
    evaluate_retrieval_cases,
    load_golden_dataset,
)

BASELINE_VERSION = "mindgraph-baseline-v1"
# 数据集摘要口径：evaluation.mindgraph_retrieval_eval.dataset_sha256() 的规范化 ——
# _jsonl_records() 先给每条记录注入 `_source_line`，再 sort_keys + 紧凑分隔符
# `(",", ":")`、`\n` 连接、补尾换行、UTF-8。注意「裸 canonical JSONL」（不注入
# `_source_line`）会算出另一个值，evaluation/manifest.py 的裸字节 sha256_file()
# 在 core.autocrlf=true 的 Windows 上又是第三个值（CRLF）。口径不写进基线，
# 跨平台/跨实现比对必然错位。
DATASET_DIGEST_METHOD = "canonical-jsonl-source-line-v1"
DEFAULT_GOLDEN = PROJECT_ROOT / "evaluation" / "datasets" / "mindgraph_golden_v2.jsonl"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "evaluation" / "results" / "baseline"

# MindGraph 检索管线实际读取的索引根（api/dependencies.py 的 mindgraph_index_root）。
# 路径与归属不再硬编码在这里：`application/index_metadata.py` 的 INDEX_ROOT_REGISTRY
# 是「哪个根服务哪套数据集」的单一事实源，且由 tests/test_index_root_registry.py
# 锁住绑定关系（根标签与数据集 gold 标签必须有交集）。
_AUTHORITATIVE_SPEC = index_root_spec("mindgraph_indexes")
AUTHORITATIVE_INDEX_ROOT = PROJECT_ROOT / _AUTHORITATIVE_SPEC.root
# 历史 M1/M2 评测栈（/api/v1/evaluations）用的根。它与 golden v2 **无标签交集**
# （实测 0/13）—— 它服务的是 expense_qa_v1（34 题，中文 chunk_id）。因此它不是
# 本基线的对照物，写进 known_roots 只为让「两个同名 CURRENT」这件事可见。
LEGACY_INDEX_ROOT = PROJECT_ROOT / index_root_spec("retrieval_indexes").root

# live 检索实际传入生产管线的姿势；硬编码在调用里，因此必须一并写进基线。
LIVE_STRATEGY = "hybrid"
LIVE_GRAPH_ENABLED = False

# 质量指标（ANSWER_METRICS）与运营指标（延迟/token/成本/覆盖率）分桶展示；
# 运营指标缺失用量记 None，绝不记 0。
QUALITY_METRIC_KEYS = frozenset(ANSWER_METRICS)

# guardrail §4「所有指标必须绑定 dataset、corpus、index、chunking、embedding、
# reranker、prompt、provider 和 model 版本」：这些键缺失即不可复现 → fail-closed。
REQUIRED_CONFIG_KEYS = (
    "strategy", "graph_enabled", "top_k", "dense_model", "sparse",
    "bm25_k1", "bm25_b", "fusion", "rrf_constant",
    "reranker_enabled", "chunk_size", "chunk_overlap",
)

RetrieveFn = Callable[[dict[str, Any]], Any]

# 重复运行时**允许**变化的字段。除这些路径外，两次运行的摘要必须逐字节相等 ——
# 这就是 PR-01 验收标准「同一输入重复运行摘要稳定」的可验证形式。
#
# 为什么要显式声明延迟：延迟是**运行环境**的函数（CPU 负载、模型预热、磁盘缓存），
# 不是被测系统的属性。live 路径实测两次 p50 相差 22%（20.01ms → 24.49ms），
# 所以「摘要稳定」若不排除延迟，这条标准在 live 上永远不成立；而用注入 trace 的
# 测试会**掩盖**它（注入的延迟是常量，看起来完全稳定）。声明出来，稳定才可验证。
VOLATILE_PATHS: tuple[tuple[str, ...], ...] = (
    ("run_id",),
    ("captured_at",),
    ("retrieval_layer", "summary", "p50_retrieval_ms"),
    ("retrieval_layer", "summary", "p95_retrieval_ms"),
    ("answer_layer", "operational", "mean_total_latency_ms"),
    ("answer_layer", "operational", "p50_total_latency_ms"),
    ("answer_layer", "operational", "p95_total_latency_ms"),
)
# 逐案延迟藏在列表元素里，单独声明：(列表路径, 元素内键名)
VOLATILE_LIST_ITEM_KEYS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("retrieval_layer", "details"), "total_retrieval_ms"),
)


def strip_volatile(payload: dict[str, Any]) -> dict[str, Any]:
    """剔除已声明的 volatile 字段，得到「两次运行应逐字节相等」的那部分。

    质量指标、索引版本、数据集摘要、配置快照**都不在** volatile 名单里，
    因此它们一旦漂移就会立刻显现——这正是基线要守的东西。
    """
    stripped = copy.deepcopy(payload)
    for path in VOLATILE_PATHS:
        node: Any = stripped
        for key in path[:-1]:
            node = node.get(key) if isinstance(node, dict) else None
            if node is None:
                break
        if isinstance(node, dict):
            node.pop(path[-1], None)
    for path, item_key in VOLATILE_LIST_ITEM_KEYS:
        node = stripped
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
            if node is None:
                break
        if isinstance(node, list):
            for item in node:
                if isinstance(item, dict):
                    item.pop(item_key, None)
    return stripped


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _git_state(root: Path) -> dict[str, Any]:
    """采集 git 基线：commit、dirty、分支与未跟踪文件。git 不可用时显式标记，不冒充干净。"""
    def run(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True, text=True, check=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip() or None

    commit = run("rev-parse", "HEAD")
    if commit is None:
        return {"commit": None, "dirty": None, "branch": None,
                "untracked": [], "note": "git_unavailable_or_not_a_repo"}
    branch = run("rev-parse", "--abbrev-ref", "HEAD") or "detached"
    status = run("status", "--porcelain") or ""
    dirty_lines = [line for line in status.splitlines() if line.strip()]
    untracked = [line[3:].strip() for line in dirty_lines if line.startswith("??")]
    return {
        "commit": commit,
        "dirty": bool(dirty_lines),
        "branch": branch,
        "untracked": untracked,
    }


def _index_state(index_root: Path | None = None) -> dict[str, Any]:
    """活跃 MindGraph 索引的版本与构成（语料摘要的一部分）。"""
    root = index_root or AUTHORITATIVE_INDEX_ROOT
    try:
        version = (root / "CURRENT").read_text(encoding="utf-8").strip() or None
    except OSError:
        return {"version": None, "note": "no_current_index"}
    if not version:
        return {"version": None, "note": "no_current_index"}
    metadata: dict[str, Any] = {}
    try:
        metadata = json.loads((root / version / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        metadata = {}  # 元数据缺失时保留 None 字段，不假装索引构成已知
    return {
        "version": version,
        "chunk_count": metadata.get("chunk_count"),
        "note_count": metadata.get("note_count"),
        "embedding_model": metadata.get("embedding_model_name"),
        "created_at": metadata.get("created_at"),
        "build_strategy": metadata.get("strategy"),
    }


def _display_path(path: Path) -> str:
    """仓库内路径转相对形式，便于跨机器比对；仓库外的原样返回。"""
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _index_roots_survey() -> list[dict[str, Any]]:
    """列出全部**已登记**的索引根，并标出本基线用的是哪个。

    仓库里 `data/mindgraph_indexes` 与 `data/retrieval_indexes` 都叫 CURRENT，
    但服务的是两套**数据粒度不同**的评测栈（vault_path vs 中文 chunk_id），
    实测标签交集为 0。不显式暴露这一点，任何「baseline 的 index.version」与
    「evaluation_runs.index_version」的比对都会错位。

    遍历登记表而不是硬编码两个路径：以后新增索引根如果没登记，
    tests/test_index_root_registry.py 会先失败，不会静默地从基线里消失。
    """
    survey: list[dict[str, Any]] = []
    for spec in INDEX_ROOT_REGISTRY:
        root = PROJECT_ROOT / spec.root
        state = _index_state(root)
        state["label"] = (
            "authoritative_for_this_baseline"
            if spec.name == _AUTHORITATIVE_SPEC.name
            else f"serves_other_dataset:{spec.dataset}"
        )
        state["root"] = _display_path(root)
        state["purpose"] = spec.purpose
        state["bound_dataset"] = spec.dataset
        survey.append(state)
    return survey


def _retrieval_config(top_k: int) -> dict[str, Any]:
    """检索配置快照（guardrail §4：指标必须绑定 chunking/embedding/reranker 版本）。

    取值来自运行时真值（infrastructure.settings + document_loader 常量）与 live
    路径实际传入生产管线的参数。读不到的键记 None 并计入 ``missing_required_keys``，
    由调用方 fail-closed —— 不产出「看起来完整」的配置块。
    """
    from document_loader import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE
    from infrastructure.settings import get_settings

    settings = get_settings()
    config: dict[str, Any] = {
        "strategy": LIVE_STRATEGY,
        "graph_enabled": LIVE_GRAPH_ENABLED,
        "top_k": top_k,
        "dense_model": settings.BGE_MODEL_NAME,
        "sparse": "bm25",
        "bm25_k1": settings.BM25_K1,
        "bm25_b": settings.BM25_B,
        "fusion": "rrf",
        "rrf_constant": settings.RRF_CONSTANT,
        "candidate_count": settings.RETRIEVAL_CANDIDATE_COUNT,
        "reranker_enabled": settings.RERANKER_ENABLED,
        "reranker_model": settings.RERANKER_MODEL_NAME,
        "rerank_top_n": settings.RERANK_TOP_N,
        "chunk_size": DEFAULT_CHUNK_SIZE,
        "chunk_overlap": DEFAULT_CHUNK_OVERLAP,
    }
    config["missing_required_keys"] = [
        key for key in REQUIRED_CONFIG_KEYS if config.get(key) is None
    ]
    return config


def _python_state() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
    }


def _load_answer_predictions(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"answer predictions not found: {path}")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise ValueError(f"answer predictions file is empty: {path}")
    return rows


def _uniform_field(rows: list[dict[str, Any]], field: str) -> str | None:
    """预测集里该字段一致时返回它，否则 None（不一致就不假装知道）。"""
    values = {str(row.get(field)) for row in rows if row.get(field)}
    return values.pop() if len(values) == 1 else None


def _corpus_digest(golden: list[dict[str, Any]]) -> dict[str, Any]:
    """语料摘要：golden 覆盖的 vault 路径数与分层构成（不存正文）。"""
    paths = sorted({path for case in golden for path in case.get("gold_vault_paths", [])})
    categories: dict[str, int] = {}
    for case in golden:
        categories[case.get("category", "unknown")] = categories.get(case.get("category", "unknown"), 0) + 1
    return {"gold_document_count": len(paths), "gold_documents": paths, "category_counts": categories}


def _write_markdown_summary(payload: dict[str, Any], path: Path, golden_name: str) -> None:
    git = payload["environment"]["git"]
    index = payload["index"]
    config = payload["retrieval_config"]
    retrieval = payload["retrieval_layer"]["summary"]
    metrics = payload["answer_layer"]["metrics"]
    operational = payload["answer_layer"]["operational"]
    lines = [
        f"# MindGraph 冻结事实基线 {payload['run_id']}",
        "",
        f"- Baseline schema: `{BASELINE_VERSION}`",
        f"- Captured at: {payload['captured_at']}",
        f"- Git: `{git['commit']}` (branch `{git['branch']}`, dirty={git['dirty']})",
        f"- Dataset: `{golden_name}` v{payload['dataset']['version']} "
        f"(sha256 `{payload['dataset']['sha256'][:12]}…` via `{payload['dataset']['sha256_method']}`, "
        f"{payload['dataset']['case_count']} cases)",
        f"- Index: `{index.get('version')}` "
        f"({index.get('note_count')} notes / {index.get('chunk_count')} chunks, "
        f"embedding {index.get('embedding_model')})",
        f"- Index root: `{index.get('root')}` (policy `{index.get('index_root_policy')}`)",
        f"- Answer layer source: `{payload['answer_layer']['prediction_source']}` "
        f"(provider `{payload['answer_layer']['chat_provider']}`, model `{payload['answer_layer']['chat_model']}`)",
        "",
        "## 检索配置（guardrail §4：指标必须绑定下列版本）",
        "",
    ]
    for name, value in config.items():
        lines.append(f"- {name}: {value}")
    lines += [
        "",
        "## 检索层（evidence-path 口径，answer 案例参与计分）",
        "",
        f"- sample: {payload['retrieval_layer']['sample_size']} "
        f"(answer {payload['retrieval_layer']['counts']['answer']}, "
        f"abstain {payload['retrieval_layer']['counts']['abstain']})",
        f"- recall@{payload['retrieval_layer']['top_k']}: {retrieval.get('recall_at_k')}",
        f"- MRR: {retrieval.get('mrr')} | full-set recall: {retrieval.get('full_set_recall')}",
        f"- P50/P95 retrieval: {retrieval.get('p50_retrieval_ms')} / {retrieval.get('p95_retrieval_ms')} ms",
        f"- 失败分层: {len(payload['retrieval_layer']['failed_cases'])} 案例 recall<1",
        "",
        "## 答案层（deterministic-answer-v2）",
        "",
    ]
    for name, value in metrics.items():
        lines.append(f"- {name}: {value}")
    lines += [
        "",
        "## 运营指标（缺失用量记 None，不记 0）",
        "",
    ]
    for name, value in operational.items():
        lines.append(f"- {name}: {value}")
    lines += [
        "",
        "## 已知边界",
        "",
        "- 检索层与答案层来自不同运行（检索=当前索引 live，答案=冻结预测文件）；",
        "  对比两层指标时必须同时核对 environment 与 prediction_source。",
        "- 索引根不止一个（见 index.known_roots）：本基线来自 "
        f"`{index.get('root')}`，与该根之外的 index_version 不可直接比对。",
        "- 本基线仅证明可追溯性，不代表任何发布门禁结论。",
        "",
        f"完整逐案归档见 `{path.stem}.json`。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def collect_baseline(
    *,
    golden: Path,
    predictions: Path,
    retrieve: RetrieveFn | None,
    output_dir: Path,
    top_k: int = 5,
    index_root: Path | None = None,
    git_root: Path | None = None,
) -> dict[str, str]:
    """采集一次完整基线；任何必需元数据缺失即抛错，不写半成品。

    ``retrieve`` 为 None 时对 answer 案例执行当前 MindGraph 检索管线（live）。
    """
    cases = load_golden_dataset(golden)  # 校验 schema；Golden 必须独立于运行库
    prediction_rows = _load_answer_predictions(predictions)

    git = _git_state(git_root or PROJECT_ROOT)
    # 真实运行中 git 不可用是元数据缺失 → fail-closed；测试沙箱可注入假 git 状态。
    if git_root is None and git["commit"] is None:
        raise RuntimeError("git state unavailable: baseline must trace to a commit")

    # guardrail §4：配置不全的基线不可复现，直接拒绝产出。
    retrieval_config = _retrieval_config(top_k)
    if retrieval_config["missing_required_keys"]:
        raise RuntimeError(
            "retrieval config incomplete (guardrail §4 requires metrics to bind "
            f"chunking/embedding/reranker versions): {retrieval_config['missing_required_keys']}"
        )

    if retrieve is None:
        from api.dependencies import get_container

        active_root = index_root or AUTHORITATIVE_INDEX_ROOT
        if not (active_root / "CURRENT").exists():
            raise FileNotFoundError(
                f"no active MindGraph index under {active_root}; "
                "build one first (scripts/sync_vault.py) or inject traces"
            )
        # 导入侧不必再打补丁：evaluation.mindgraph_retrieval_eval 已把
        # RetrievalTrace 的权威侧固定为生产代码使用的 `retrieval.types`，
        # 与 pipeline 返回的类恒为同一对象。
        pipeline = get_container().mindgraph_pipeline(
            top_k=top_k, graph_enabled=LIVE_GRAPH_ENABLED
        )
        live_retrieve: RetrieveFn = lambda case: pipeline.retrieve(  # noqa: E731
            case["question"], LIVE_STRATEGY, graph_enabled=LIVE_GRAPH_ENABLED
        )
        retrieve = live_retrieve
        retrieval_mode = "live_pipeline"
    else:
        retrieval_mode = "injected_traces"

    # 检索层：answer 案例必须有 trace（fail-closed：缺 case_id 直接报错）
    retrieval_report = evaluate_retrieval_cases(
        cases, retrieve, top_k=top_k, dataset_digest=dataset_sha256(golden),
    )

    # 答案层：预测集必须与 Golden 逐案例对齐（evaluate 内部校验缺失/多余并抛错）
    answer_summary = evaluate_answer_predictions(cases, prediction_rows)
    # 逐案归档：summarize 只保留失败案例明细，而 PR-01 要求逐案可追溯；
    # evaluate_answer_case 是 evaluate_answer_predictions 的内部构件，口径不变。
    answer_per_case = [
        evaluate_answer_case(case, row)
        for case, row in _pair_cases_predictions(cases, prediction_rows)
    ]

    payload: dict[str, Any] = {
        "baseline_version": BASELINE_VERSION,
        "run_id": f"baseline_{_utc_stamp()}",
        "captured_at": datetime.now(UTC).isoformat(),
        "environment": {
            "git": git,
            "runtime": _python_state(),
        },
        "dataset": {
            "path": _display_path(golden), "name": golden.name,
            "version": cases[0].get("dataset_version") if cases else None,
            "sha256": dataset_sha256(golden),
            "sha256_method": DATASET_DIGEST_METHOD,
            "case_count": len(cases),
            "corpus": _corpus_digest(cases),
        },
        "index": {
            **_index_state(index_root or AUTHORITATIVE_INDEX_ROOT),
            "root": _display_path(index_root or AUTHORITATIVE_INDEX_ROOT),
            "index_root_policy": "mindgraph_pipeline_root_v1",
            "known_roots": _index_roots_survey(),
        },
        "retrieval_config": retrieval_config,
        "retrieval_layer": {
            "mode": retrieval_mode,
            "evaluator_version": retrieval_report["evaluator_version"],
            "top_k": retrieval_report["top_k"],
            "sample_size": retrieval_report["sample_size"],
            "counts": retrieval_report["counts"],
            "summary": retrieval_report["summary"],
            "failed_cases": retrieval_report["failed_cases"],
            "details": retrieval_report["details"],
        },
        "answer_layer": {
            "evaluator_version": "deterministic-answer-v2",
            "prediction_source": predictions.name,
            "chat_provider": _uniform_field(prediction_rows, "actual_provider"),
            "chat_model": _uniform_field(prediction_rows, "model"),
            "sample_size": answer_summary["sample_size"],
            "metrics": {key: value for key, value in answer_summary["metrics"].items()
                        if key in QUALITY_METRIC_KEYS},
            "operational": {key: value for key, value in answer_summary["metrics"].items()
                            if key not in QUALITY_METRIC_KEYS},  # 延迟/token/成本/覆盖率；缺失记 None 不记 0
            "failed_case_count": answer_summary["failed_case_count"],
            "failed_cases": answer_summary["failed_cases"],
            "per_case": [
                {
                    "case_id": row["case_id"],
                    "conflict_accuracy": row.get("conflict_accuracy"),
                    # 不计分的案例必须能看出"为什么不计分"，否则归档里一个 None
                    # 无法区分"指标不适用"与"系统没跑"
                    "conflict_kind": row.get("conflict_kind"),
                    "conflict_applicable": row.get("conflict_applicable"),
                    "acl_leakage": row.get("acl_leakage"),
                    "refusal_correctness": row.get("refusal_correctness"),
                    "failures": row.get("failures", []),
                }
                for row in answer_per_case
            ],
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    baseline_path = output_dir / f"{payload['run_id']}.json"
    summary_path = output_dir / f"{payload['run_id']}.md"
    baseline_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    _write_markdown_summary(payload, summary_path, golden.name)
    print(json.dumps({
        "baseline_version": BASELINE_VERSION,
        "baseline_path": str(baseline_path),
        "summary_path": str(summary_path),
        "git": git["commit"], "dirty": git["dirty"],
        "dataset": payload["dataset"]["sha256"][:12],
        "dataset_sha256_method": DATASET_DIGEST_METHOD,
        "index": payload["index"].get("version"),
        "index_root": payload["index"].get("root"),
        "retrieval_config": payload["retrieval_config"],
    }, ensure_ascii=False, indent=2))
    return {"baseline_path": str(baseline_path), "summary_path": str(summary_path)}


def _pair_cases_predictions(
    cases: list[dict[str, Any]], predictions: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """按 case_id 把 Golden 与预测配对；evaluate_answer_predictions 已校验对齐。"""
    by_id = {row["case_id"]: row for row in predictions}
    return [(case, by_id[case["case_id"]]) for case in cases]


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze MindGraph factual baseline (PR-01)")
    parser.add_argument("--golden", default=str(DEFAULT_GOLDEN),
                        help="golden dataset JSONL (independent, human-frozen)")
    parser.add_argument("--predictions", required=True,
                        help="frozen answer prediction JSONL (e.g. evaluation/results/answer_predictions_*.jsonl)")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    collect_baseline(
        golden=Path(args.golden),
        predictions=Path(args.predictions),
        retrieve=None,
        output_dir=Path(args.output_dir),
        top_k=args.top_k,
    )


if __name__ == "__main__":
    main()
