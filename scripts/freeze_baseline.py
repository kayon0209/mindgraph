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

from evaluation.answer_eval import ANSWER_METRICS, evaluate_answer_case, evaluate_answer_predictions  # noqa: E402
from evaluation.mindgraph_retrieval_eval import (  # noqa: E402
    dataset_sha256,
    evaluate_retrieval_cases,
    load_golden_dataset,
)

BASELINE_VERSION = "mindgraph-baseline-v1"
DEFAULT_GOLDEN = PROJECT_ROOT / "evaluation" / "datasets" / "mindgraph_golden_v2.jsonl"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "evaluation" / "results" / "baseline"
MINDGRAPH_INDEX_ROOT = PROJECT_ROOT / "data" / "mindgraph_indexes"
# 质量指标（ANSWER_METRICS）与运营指标（延迟/token/成本/覆盖率）分桶展示；
# 运营指标缺失用量记 None，绝不记 0。
QUALITY_METRIC_KEYS = frozenset(ANSWER_METRICS)

RetrieveFn = Callable[[dict[str, Any]], Any]

# 重复运行时允许变化的字段（时间戳类）；摘要稳定性测试会剔除它们。
_VOLATILE_KEYS = ("run_id", "captured_at")


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
    root = index_root or MINDGRAPH_INDEX_ROOT
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
        f"(sha256 `{payload['dataset']['sha256'][:12]}…`, {payload['dataset']['case_count']} cases)",
        f"- Index: `{index.get('version')}` "
        f"({index.get('note_count')} notes / {index.get('chunk_count')} chunks, "
        f"embedding {index.get('embedding_model')})",
        f"- Answer layer source: `{payload['answer_layer']['prediction_source']}` "
        f"(provider `{payload['answer_layer']['chat_provider']}`, model `{payload['answer_layer']['chat_model']}`)",
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

    if retrieve is None:
        from api.dependencies import get_container
        import evaluation.mindgraph_retrieval_eval as _eval

        if not ((index_root or MINDGRAPH_INDEX_ROOT) / "CURRENT").exists():
            raise FileNotFoundError(
                f"no active MindGraph index under {index_root or MINDGRAPH_INDEX_ROOT}; "
                "build one first (scripts/sync_vault.py) or inject traces"
            )
        # src/ 与仓库根同时在 sys.path 时，evaluator 的 `from src.retrieval.types`
        # 会与管线的裸 `retrieval.types` 形成两套模块对象，isinstance 必然失配；
        # 与 scripts/run_external_eval2.py 相同手法：把 evaluator 的绑定统一到
        # 管线实际返回的类。
        import retrieval.types as _live_types

        setattr(_eval, "RetrievalTrace", _live_types.RetrievalTrace)
        pipeline = get_container().mindgraph_pipeline(top_k=top_k, graph_enabled=False)
        live_retrieve: RetrieveFn = lambda case: pipeline.retrieve(  # noqa: E731
            case["question"], "hybrid", graph_enabled=False
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
            "path": str(golden), "name": golden.name,
            "version": cases[0].get("dataset_version") if cases else None,
            "sha256": dataset_sha256(golden),
            "case_count": len(cases),
            "corpus": _corpus_digest(cases),
        },
        "index": _index_state(index_root),
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
        "index": payload["index"].get("version"),
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
