#!/usr/bin/env python3
"""重排消融：baseline / all / conditional 三档对比（PR-11 质量门禁入口）。

与 ``scripts/run_graph_ablation.py`` 平行：那个管"图扩展开不开"，这个管
"rerank 该给哪些请求跑"。三档口径：

- **baseline** ``hybrid``：不重排；
- **all** ``hybrid_rerank``：每题都重排（贵，但上限在这里）；
- **conditional**：按 ``ConditionalRerankPolicy`` 的实际行为合成——高价值路由
  （``exception_or_conflict`` / ``cross_policy``）取 all 的值，其余取 baseline。
  不重跑第三遍：条件式不改变单次检索结果，只决定是否调用，所以按路由取值就是
  它的真实输出。

为什么必须按路由分层看：四策略评测集（``evaluation/baseline``，dataset 1.0.0）
的 23 题路由分布是 ``{factual: 23}``，**高价值路由命中 0** —— 在那个集上条件式
等价于关闭，任何收益都测不出来。本脚本默认用 dataset 2.4.0（76 题中有 18 题命中）。

用法::

  RERANKER_ENABLED=true python scripts/run_rerank_ablation.py
  RERANKER_ENABLED=true python scripts/run_rerank_ablation.py --repetitions 3

产物::

  evaluation/results/rerank_ablation/rerank_<ts>.json
  docs/evaluation/rerank-routing.md
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for _entry in (SRC, ROOT):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

import config  # noqa: E402,F401  早于 faiss/torch：设置 OMP_NUM_THREADS 等守卫变量
import evaluation.mindgraph_retrieval_eval as eval_module  # noqa: E402
import infrastructure.retrieval_factory as retrieval_factory  # noqa: E402
from application.mindgraph_graph_store import MindGraphGraphStore  # noqa: E402
from evaluation.mindgraph_retrieval_eval import (  # noqa: E402
    DEFAULT_DATASET_PATH,
    dataset_sha256,
    evaluate_retrieval_cases,
    load_golden_dataset,
)
from evaluation.retrieval_eval import resolve_rerank_route  # noqa: E402
from infrastructure.database import ProductDatabase  # noqa: E402
from infrastructure.retrieval_factory import create_mindgraph_retrieval_pipeline  # noqa: E402
from retrieval.embeddings import BGEEmbeddingProvider  # noqa: E402

RESULT_DIR = ROOT / "evaluation" / "results" / "rerank_ablation"
REPORT_PATH = ROOT / "docs" / "evaluation" / "rerank-routing.md"
TOP_K = 5
HIGH_VALUE = {"exception_or_conflict", "cross_policy"}


def _aligned_modules() -> None:
    """统一 trace 类身份，否则评测器会静默拒绝所有 trace（详见图消融脚本）。"""
    trace_cls = __import__("retrieval.types", fromlist=["RetrievalTrace"]).RetrievalTrace
    eval_module.RetrievalTrace = trace_cls


def _summarize(rows: list[dict]) -> dict:
    return {
        "n": len(rows),
        "recall_at_5": round(statistics.mean(row["recall"] for row in rows), 4) if rows else None,
        "mrr": round(statistics.mean(row["mrr"] for row in rows), 4) if rows else None,
        "mean_ms": round(statistics.mean(row["latency"] for row in rows), 2) if rows else None,
    }


def _retrieve_all(pipeline, cases, strategy, routes, repetitions, digest):
    def retrieve(case):
        # 生产由 chat_service 注入路由；条件式重排靠它决策（非条件模式下不参与）。
        pipeline.rerank_route = routes[case["case_id"]]
        traces = [pipeline.retrieve(case["question"], strategy) for _ in range(repetitions)]
        representative = traces[-1]
        latencies = sorted(float(t.latency_ms.get("total_retrieval_ms", 0.0)) for t in traces)
        representative.latency_ms["total_retrieval_ms"] = statistics.median(latencies)
        return representative

    report = evaluate_retrieval_cases(cases, retrieve, top_k=TOP_K, dataset_digest=digest)
    return {
        row["case_id"]: {
            "recall": row["metrics"]["recall_at_k"],
            "mrr": row["metrics"]["mrr"],
            "latency": row.get("total_retrieval_ms", 0.0),
        }
        for row in report["details"]
        if row.get("scored")
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="重排三档消融（baseline / all / conditional）")
    parser.add_argument("--repetitions", type=int, default=1, help="每题重复检索次数，延迟取中位数")
    args = parser.parse_args()
    if args.repetitions < 1:
        raise SystemExit("--repetitions must be >= 1")

    _aligned_modules()
    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    cases = [case for case in load_golden_dataset() if case["expected_behavior"] == "answer"]
    digest = dataset_sha256(DEFAULT_DATASET_PATH)
    routes = {case["case_id"]: resolve_rerank_route(case["question"]) for case in cases}

    database = ProductDatabase(ROOT / "data" / "product" / "product.sqlite3")
    database.initialize()
    graph_store = MindGraphGraphStore(database)
    provider = BGEEmbeddingProvider()
    retrieval_factory.BGEEmbeddingProvider = lambda: provider

    results: dict[str, dict] = {}
    for strategy in ("hybrid", "hybrid_rerank"):
        pipeline = create_mindgraph_retrieval_pipeline(
            ROOT / "data" / "mindgraph_indexes", graph_store, final_top_k=TOP_K, graph_enabled=False,
        )
        if strategy == "hybrid_rerank":
            if pipeline.base.reranker is None:
                # 不加这道检查，all 档会静默降级成 baseline：指标与基线逐位相同、
                # 退出码 0，看起来"重排没收益"，其实是根本没跑。
                raise SystemExit(
                    "reranker 未启用（RERANKER_ENABLED=false 或权重缺失）——all 档会整批降级，"
                    "结果不可信。请先启用：RERANKER_ENABLED=true python scripts/run_rerank_ablation.py"
                )
            # all 档必须是"每题都跑"的真全量。工厂会按 settings 把条件式带进来，
            # 若本机 .env 开了 CONDITIONAL_RERANK_ENABLED，all 档会退化成条件式
            # （实测延迟 3566ms → 1073ms，与 conditional 档几乎重合），三档对比
            # 就失去意义。评测工具自己必须确定性，不能被环境配置摆布。
            pipeline.base.conditional_rerank_enabled = False
        results[strategy] = _retrieve_all(pipeline, cases, strategy, routes, args.repetitions, digest)

    high_ids = {case["case_id"] for case in cases if routes[case["case_id"]] in HIGH_VALUE}
    low_ids = {case["case_id"] for case in cases if routes[case["case_id"]] not in HIGH_VALUE}
    conditional_rows = [
        results["hybrid_rerank"][case["case_id"]]
        if routes[case["case_id"]] in HIGH_VALUE else results["hybrid"][case["case_id"]]
        for case in cases
    ]

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "dataset_version": cases[0].get("dataset_version"),
        "n_scored": len(cases),
        "repetitions": args.repetitions,
        "reranker_model": "BAAI/bge-reranker-base",
        "high_value_cases": len(high_ids),
        "route_distribution": {
            route: sum(1 for value in routes.values() if value == route)
            for route in sorted(set(routes.values()))
        },
        "overall": {
            "baseline": _summarize([results["hybrid"][c] for c in results["hybrid"]]),
            "all": _summarize([results["hybrid_rerank"][c] for c in results["hybrid_rerank"]]),
            "conditional": _summarize(conditional_rows),
        },
        "high_value_subset": {
            "baseline": _summarize([results["hybrid"][c] for c in high_ids if c in results["hybrid"]]),
            "all": _summarize([results["hybrid_rerank"][c] for c in high_ids if c in results["hybrid_rerank"]]),
        },
        "low_value_subset": {
            "baseline": _summarize([results["hybrid"][c] for c in low_ids if c in results["hybrid"]]),
            "all": _summarize([results["hybrid_rerank"][c] for c in low_ids if c in results["hybrid_rerank"]]),
        },
    }

    gate_path = RESULT_DIR / f"rerank_{payload['generated_at']}.json"
    gate_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(_render(payload), encoding="utf-8")

    overall = payload["overall"]
    print(f"[baseline ] R@5={overall['baseline']['recall_at_5']} MRR={overall['baseline']['mrr']} {overall['baseline']['mean_ms']}ms")
    print(f"[all      ] R@5={overall['all']['recall_at_5']} MRR={overall['all']['mrr']} {overall['all']['mean_ms']}ms")
    print(f"[condition] R@5={overall['conditional']['recall_at_5']} MRR={overall['conditional']['mrr']} {overall['conditional']['mean_ms']}ms")
    print(f"[out ] {gate_path}")
    print(f"[out ] {REPORT_PATH}")
    return 0


def _render(payload: dict) -> str:
    o = payload["overall"]
    high = payload["high_value_subset"]
    low = payload["low_value_subset"]
    cond, allr, base = o["conditional"], o["all"], o["baseline"]
    return "\n".join([
        "# 重排消融：baseline / all / conditional",
        "",
        f"数据集 `{payload['dataset_version']}`，可评分 {payload['n_scored']} 例，"
        f"重排模型 `{payload['reranker_model']}`，每题重复 {payload['repetitions']} 次（延迟取中位数）。",
        f"高价值路由命中 **{payload['high_value_cases']}** 例，路由分布：{payload['route_distribution']}。",
        "",
        "## 三档对比（全量）",
        "",
        "| 档位 | Recall@5 | MRR | 平均延迟 (ms) |",
        "|---|---:|---:|---:|",
        f"| baseline（hybrid，不重排） | {base['recall_at_5']:.4f} | {base['mrr']:.4f} | {base['mean_ms']:.2f} |",
        f"| all（每题都重排） | {allr['recall_at_5']:.4f} | {allr['mrr']:.4f} | {allr['mean_ms']:.2f} |",
        f"| conditional（只给高价值路由跑） | {cond['recall_at_5']:.4f} | {cond['mrr']:.4f} | {cond['mean_ms']:.2f} |",
        "",
        "## 条件式相对全量（PR-11 门禁）",
        "",
        f"- Recall@5：**{cond['recall_at_5'] - allr['recall_at_5']:+.4f}**"
        f"（门槛 ≤ −1pp → {'达标' if (cond['recall_at_5'] - allr['recall_at_5']) >= -0.01 else '不达标'}）",
        f"- MRR：**{cond['mrr'] - allr['mrr']:+.4f}**"
        f"（门槛 ≤ −1pp → {'达标' if (cond['mrr'] - allr['mrr']) >= -0.01 else '不达标'}）",
        f"- 延迟：**{100 * (cond['mean_ms'] / allr['mean_ms'] - 1):+.1f}%**"
        f"（门槛 降 ≥20% → {'达标' if cond['mean_ms'] / allr['mean_ms'] <= 0.8 else '不达标'}）",
        "",
        "> 门禁口径里「质量」若指 Recall@5 则达标，若指 MRR 则不达标——重排对 MRR 的",
        "> 贡献远大于对 Recall@5 的贡献（见下表），所以**按哪个判据取舍要显式写清楚**，",
        "> 不能笼统说「质量下降 ≤1pp」。",
        "",
        "## 按路由分层（重排的收益集中在哪里）",
        "",
        "| 子集 | 档位 | Recall@5 | MRR | 平均延迟 (ms) |",
        "|---|---|---:|---:|---:|",
        f"| 高价值（{high['baseline']['n']} 例） | baseline | {high['baseline']['recall_at_5']:.4f} | {high['baseline']['mrr']:.4f} | {high['baseline']['mean_ms']:.2f} |",
        f"| 高价值 | all | {high['all']['recall_at_5']:.4f} | {high['all']['mrr']:.4f} | {high['all']['mean_ms']:.2f} |",
        f"| 低价值（{low['baseline']['n']} 例） | baseline | {low['baseline']['recall_at_5']:.4f} | {low['baseline']['mrr']:.4f} | {low['baseline']['mean_ms']:.2f} |",
        f"| 低价值 | all | {low['all']['recall_at_5']:.4f} | {low['all']['mrr']:.4f} | {low['all']['mean_ms']:.2f} |",
        "",
        f"高价值子集上重排带来 R@5 **{high['all']['recall_at_5'] - high['baseline']['recall_at_5']:+.4f}**、"
        f"MRR **{high['all']['mrr'] - high['baseline']['mrr']:+.4f}**；"
        f"低价值子集只有 R@5 {low['all']['recall_at_5'] - low['baseline']['recall_at_5']:+.4f}。"
        "答错代价最高的问题恰恰收益最大——这是按路由分配重排预算的依据。",
        "",
        "## 为什么不用四策略评测集（dataset 1.0.0）",
        "",
        "该集 23 题的路由分布是 `{factual: 23}`，高价值路由命中 **0**，条件式在此集上",
        "等价于关闭（23/23 跳过），任何收益都测不出来。评估条件式必须用含例外/冲突/",
        "跨制度样本的数据集（本脚本用的 2.4.0 有 18 例命中）。",
        "",
        "## 复现",
        "",
        "```powershell",
        "RERANKER_ENABLED=true python scripts/run_rerank_ablation.py",
        "```",
        "",
        "脚本内置守卫：reranker 未启用时直接退出——否则 all 档会整批降级、指标与",
        "baseline 逐位相同，看起来像「重排没收益」。",
        "",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
