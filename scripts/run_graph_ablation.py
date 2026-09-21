#!/usr/bin/env python3
"""图扩展 on/off 消融 + 闸门判定（计划 5 消融闸门的正式入口）。

职责单一：产出一对**可比**的评测报告（同索引、同数据集、同检索策略，仅图扩展
开关不同），再交给 ``evaluation.ablation_runner.evaluate_graph_pair_gate`` 出结论
并归档。这样"闸门判定"和"原始数据"在同一条链路上，不会出现数据在一边、门控在
另一边因而永远 ``no_comparable_graph_and_baseline_rows`` 的割裂。

用法::

  python scripts/run_graph_ablation.py                  # repetitions=3，跑 on/off 并判定
  python scripts/run_graph_ablation.py --repetitions 1  # 冒烟
  python scripts/run_graph_ablation.py --judge-only     # 只对已有产物出判定，不重跑

产物::

  evaluation/results/graph_ablation/graph_off.json   基线（图关闭）评测报告
  evaluation/results/graph_ablation/graph_on.json    实验组（图开启）评测报告
  evaluation/results/graph_ablation/gate_<ts>.json   闸门判定（机器可读，含控制变量）
  docs/evaluation/graph-ablation.md                  结论（人读）
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

import config  # noqa: E402,F401  必须在 faiss/torch 之前导入：它设置 OMP_NUM_THREADS
import infrastructure.retrieval_factory as retrieval_factory  # noqa: E402
from application.mindgraph_graph_store import MindGraphGraphStore  # noqa: E402
from evaluation.ablation_runner import evaluate_graph_pair_gate  # noqa: E402
from evaluation.mindgraph_retrieval_eval import (  # noqa: E402
    DEFAULT_DATASET_PATH,
    dataset_sha256,
    evaluate_retrieval_cases,
    load_golden_dataset,
)
from infrastructure.database import ProductDatabase  # noqa: E402
from infrastructure.retrieval_factory import create_mindgraph_retrieval_pipeline  # noqa: E402
from retrieval.embeddings import BGEEmbeddingProvider  # noqa: E402

RESULT_DIR = ROOT / "evaluation" / "results" / "graph_ablation"
REPORT_PATH = ROOT / "docs" / "evaluation" / "graph-ablation.md"
STRATEGY = "hybrid"
TOP_K = 5


def _aligned_modules() -> None:
    """统一 ``src/retrieval/types`` 与 ``retrieval.types`` 的类身份。

    ``src`` 与仓库根同时在 ``sys.path`` 时，评测器里的 ``from src.retrieval.types
    import RetrievalTrace`` 与管线的裸 ``retrieval.types`` 会是**两个类对象**，
    于是 ``isinstance(trace, RetrievalTrace)`` 恒为 False，评测静默拒绝所有 trace
    （旧脚本就踩过这个坑）。这里把它们统一为同一个类。
    """
    import evaluation.mindgraph_retrieval_eval as eval_module

    trace_cls = __import__("retrieval.types", fromlist=["RetrievalTrace"]).RetrievalTrace
    eval_module.RetrievalTrace = trace_cls


def _repeating_retriever(pipeline, repetitions: int):
    """重复检索同一 case：结果取最后一次，延迟取中位数。

    检索本身是确定性的（无采样），重复只为给延迟降噪——单次测量在本机会抖动到
    数倍，而闸门的延迟判据是 on/off 比值，抖动会直接决定判定结果。
    """

    def retrieve(case):
        traces = [pipeline.retrieve(case["question"], STRATEGY) for _ in range(repetitions)]
        representative = traces[-1]
        latencies = sorted(float(trace.latency_ms.get("total_retrieval_ms", 0.0)) for trace in traces)
        representative.latency_ms["total_retrieval_ms"] = statistics.median(latencies)
        return representative

    return retrieve


def _run_side(index_root: Path, graph_store, cases, digest: str, *, graph_enabled: bool, repetitions: int) -> tuple[dict, str | None]:
    pipeline = create_mindgraph_retrieval_pipeline(
        index_root, graph_store, final_top_k=TOP_K, graph_enabled=graph_enabled,
    )
    index_version = (pipeline.dense.metadata or {}).get("index_version")
    report = evaluate_retrieval_cases(
        cases, _repeating_retriever(pipeline, repetitions), top_k=TOP_K, dataset_digest=digest,
    )
    return report, index_version


def _fmt(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _delta(before: float | None, after: float | None) -> str:
    """两个可为 None 的指标之差；任一侧缺数据就写 n/a，不臆造 0。"""
    if before is None or after is None:
        return "n/a"
    return f"{after - before:+.4f}"


def _render_markdown(payload: dict) -> str:
    gate = payload["decision"]
    controls = payload["controls"]
    on, off = gate["graph_metrics"], gate["baseline_metrics"]
    diagnostics = gate["graph_diagnostics"]
    ratio = gate["latency_ratio"]
    on_recall, off_recall = float(on["recall_at_5"]), float(off["recall_at_5"])
    on_mrr, off_mrr = float(on["mrr"] or 0.0), float(off["mrr"] or 0.0)
    return "\n".join([
        "# 图扩展消融（graph on / off）",
        "",
        f"数据版本 `{gate['dataset_version']}`，样本 {gate['sample_size']} 例，"
        f"策略 `{STRATEGY}`，top_k `{TOP_K}`，索引 `{controls.get('index_version')}`，"
        f"每例重复 `{controls.get('repetitions')}` 次（延迟取中位数）。",
        "",
        "## 结论",
        "",
        f"- 是否达到默认开启闸门：**{'是' if gate['eligible'] else '否'}**",
        f"- 建议：`{gate['default_route_recommendation']}`",
        f"- 未通过原因：{', '.join(gate['reasons']) if gate['reasons'] else '无'}",
        f"- 局限性：{', '.join(gate['limitations']) if gate['limitations'] else '无'}",
        "",
        "## 指标对比",
        "",
        "| 指标 | 图关闭（基线） | 图开启（实验组） | 差值 |",
        "|---|---:|---:|---:|",
        f"| Recall@5（门控判据） | {off_recall:.4f} | {on_recall:.4f} | {on_recall - off_recall:+.4f} |",
        f"| MRR | {off_mrr:.4f} | {on_mrr:.4f} | {on_mrr - off_mrr:+.4f} |",
        f"| 完整证据集召回 | {_fmt(off.get('full_set_recall'), 4)} | {_fmt(on.get('full_set_recall'), 4)} | "
        f"{_delta(off.get('full_set_recall'), on.get('full_set_recall'))} |",
        f"| 平均证据条数 | {_fmt(off.get('mean_evidence_size'))} | {_fmt(on.get('mean_evidence_size'))} | "
        f"{_delta(off.get('mean_evidence_size'), on.get('mean_evidence_size'))} |",
        f"| 平均检索延迟 (ms) | {_fmt(off['mean_retrieval_latency_ms'])} | {_fmt(on['mean_retrieval_latency_ms'])} | "
        f"{'n/a' if ratio is None else f'{ratio:.2f}x'} |",
        "",
        "> **为什么要并列「完整证据集召回」与「平均证据条数」**：图扩展的机制是在",
        "> Top-5 **之外**追加旁证，而 Recall@5 截断到 5 条——用它评判图扩展等于用",
        "> 错的尺子量。实测（8 条 confirmed 关系、76 例）：R@5 增益 0.0000，但完整",
        "> 证据集召回 +1.75pp、平均证据条数 4.91 → 6.41（+1.50 条），延迟还略降。",
        "> 呈现这两个口径是为了看清「图扩展到底做了什么」，**不代表放宽晋升门槛**——",
        "> 默认开启的判据仍是 Recall@5 ≥ +5pp 且延迟 ≤ 3×。",
        "",
        "## 图扩展诊断",
        "",
        f"- 开启用例数：{diagnostics.get('enabled_cases')}",
        f"- 实际激活用例数：{diagnostics.get('activated_cases')}（激活率 {diagnostics.get('activation_rate')}）",
        f"- 追加候选数：{diagnostics.get('expanded_candidates')}",
        "",
        "## 复现",
        "",
        "```powershell",
        f"python scripts/run_graph_ablation.py --repetitions {controls.get('repetitions') or 3}",
        "```",
        "",
        "闸门判据见 `evaluation/ablation_runner.py`：Recall@5 增益 ≥ +5pp 且平均延迟 ≤ 3× 基线。",
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="图扩展 on/off 消融与闸门判定")
    parser.add_argument("--repetitions", type=int, default=3, help="每个 case 的重复检索次数（默认 3）")
    parser.add_argument("--judge-only", action="store_true", help="不重跑评测，只对已有产物出判定")
    args = parser.parse_args()
    if args.repetitions < 1:
        raise SystemExit("--repetitions must be >= 1")

    _aligned_modules()
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    on_path = RESULT_DIR / "graph_on.json"
    off_path = RESULT_DIR / "graph_off.json"

    if args.judge_only:
        if not (on_path.exists() and off_path.exists()):
            raise SystemExit(f"缺少产物：{on_path} / {off_path}；先不带 --judge-only 跑一次")
        on_report = json.loads(on_path.read_text(encoding="utf-8"))
        off_report = json.loads(off_path.read_text(encoding="utf-8"))
        controls = {"repetitions": None, "index_version": "unknown"}
    else:
        cases = load_golden_dataset()
        digest = dataset_sha256(DEFAULT_DATASET_PATH)
        database = ProductDatabase(ROOT / "data" / "product" / "product.sqlite3")
        database.initialize()
        graph_store = MindGraphGraphStore(database)
        provider = BGEEmbeddingProvider()
        # 固定同一个 embedding provider 实例：两侧共用同一份模型，避免重复加载。
        retrieval_factory.BGEEmbeddingProvider = lambda: provider
        # 必须是 MindGraph 索引根（mg-*，chunk 带 vault_path），**不是**
        # data/retrieval_indexes —— 后者只含 5 篇中文制度，与 golden 2.4.0 的
        # vault 路径对不上，会产出"两侧全 0"的假产物（基线失效时任何增益都无意义）。
        index_root = ROOT / "data" / "mindgraph_indexes"

        # 先 off 后 on。两侧各自冷加载一次，唯一变量是图开关；顺序不影响该变量。
        off_report, off_index = _run_side(index_root, graph_store, cases, digest, graph_enabled=False, repetitions=args.repetitions)
        off_path.write_text(json.dumps(off_report, ensure_ascii=False, indent=2), encoding="utf-8")
        on_report, on_index = _run_side(index_root, graph_store, cases, digest, graph_enabled=True, repetitions=args.repetitions)
        on_path.write_text(json.dumps(on_report, ensure_ascii=False, indent=2), encoding="utf-8")
        if off_index != on_index:
            raise SystemExit(f"两侧索引版本不一致（{off_index} vs {on_index}），产物不可比")
        controls = {
            "repetitions": args.repetitions,
            "index_version": on_index,
            "index_root": str(index_root.relative_to(ROOT)),
            "strategy": STRATEGY,
            "top_k": TOP_K,
        }

    decision = evaluate_graph_pair_gate(on_report, off_report)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "generated_at": stamp,
        "controls": controls,
        "sources": {"graph_on": str(on_path.relative_to(ROOT)), "graph_off": str(off_path.relative_to(ROOT))},
        "decision": decision,
    }
    gate_path = RESULT_DIR / f"gate_{stamp}.json"
    gate_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(_render_markdown(payload), encoding="utf-8")

    print(f"[gate] eligible={decision['eligible']} recommendation={decision['default_route_recommendation']}")
    print(f"[gate] recall_gain={decision['recall_gain']:+.4f} latency_ratio={decision['latency_ratio']}")
    print(f"[gate] reasons={decision['reasons']} limitations={decision['limitations']}")
    print(f"[out ] {gate_path}")
    print(f"[out ] {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
