"""MindGraph 检索消融评测（BM25 / BM25+Vector / BM25+Vector+Graph）。

复用现有 MindGraph 检索管线与 evaluation_runs 表（前端评测看板直接读取该表）。
三组策略：
    - bm25              ：纯稀疏检索（图谱关闭）
    - bm25_vector       ：Hybrid（稀疏+稠密，图谱关闭）
    - bm25_vector_graph ：Hybrid + 图谱一跳扩展（图谱开启）

用法：
    python scripts/run_ablation.py
    python scripts/run_ablation.py --golden evaluation/datasets/mindgraph_golden_v2.jsonl
    python scripts/run_ablation.py --dry-run          # 计算指标但不写库（沙箱/离线验证用）

依赖：
    - 需先构建 MindGraph 索引；公开样本可使用仓库内 demo-vault/
    - 需要真实 BGE 嵌入（首次自动下载，见 .env 的 BGE_LOCAL_FILES_ONLY）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src"
PROJECT_ROOT = ROOT.parent
# 同时需要 src（api/application/retrieval 等）与项目根（evaluation 包）
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PROJECT_ROOT))

from api.dependencies import get_container  # noqa: E402
from infrastructure.database import dumps  # noqa: E402

# (展示名, 管线 strategy, graph_enabled)
STRATEGIES = [
    ("bm25", "bm25", False),
    ("bm25_vector", "hybrid", False),
    ("bm25_vector_graph", "hybrid", True),
]
K_VALUES = (1, 3, 5)
TOP_K = 5


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_or_generate_golden(db, golden_path: Path, force: bool = False) -> list[dict]:
    """加载人工冻结的 Golden Set，禁止用运行数据库反向生成或覆盖。"""
    del db  # 保留参数仅为兼容旧调用方；Golden 标签必须独立于运行数据库。
    if force:
        raise ValueError("独立 Golden Set 禁止由运行数据库生成或覆盖")
    if not golden_path.is_file():
        raise FileNotFoundError(f"Golden Set 不存在：{golden_path}")
    cases = [json.loads(line) for line in golden_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not cases or any("gold_vault_paths" not in case for case in cases):
        raise ValueError(f"Golden Set 为空或 schema 无效：{golden_path}")
    print(f"[golden] loaded {len(cases)} independent cases from {golden_path}")
    return cases


def evaluate_strategy(pipeline, cases: list[dict], strategy: str, graph_enabled: bool) -> dict:
    cases = [case for case in cases if case.get("expected_behavior", "answer") == "answer"]
    if not cases:
        raise ValueError("Golden Set 没有可用于检索评测的 answer 样本")
    recalls = {k: [] for k in K_VALUES}
    mrrs: list[float] = []
    hit_rates: list[float] = []
    latencies: list[float] = []
    for case in cases:
        t0 = time.perf_counter()
        trace = pipeline.retrieve(case["question"], strategy, graph_enabled=graph_enabled)
        latencies.append((time.perf_counter() - t0) * 1000)
        ranked = [c.chunk.metadata.get("vault_path") for c in trace.final_selected_chunks]
        gold = set(case["gold_vault_paths"])
        for k in K_VALUES:
            top = set(ranked[:k])
            recalls[k].append(len(top & gold) / max(1, len(gold)))
        hit_rates.append(1.0 if (set(ranked[:TOP_K]) & gold) else 0.0)
        rank = next((i + 1 for i, nid in enumerate(ranked) if nid in gold), None)
        mrrs.append(1.0 / rank if rank else 0.0)
    n = max(1, len(cases))
    return {
        "sample_size": len(cases),
        "recall_at_1": sum(recalls[1]) / n,
        "recall_at_3": sum(recalls[3]) / n,
        "recall_at_5": sum(recalls[5]) / n,
        "mrr": sum(mrrs) / n,
        "document_hit_rate": sum(hit_rates) / n,
        "chunk_hit_rate": sum(hit_rates) / n,
        "mean_retrieval_latency_ms": sum(latencies) / n,
    }


def _should_enable_graph(graph_metrics: dict[str, float], baseline_metrics: dict[str, float]) -> bool:
    from evaluation.ablation_runner import evaluate_graph_gate

    return bool(evaluate_graph_gate(graph_metrics, baseline_metrics)["eligible"])


def main() -> None:
    ap = argparse.ArgumentParser(description="MindGraph 检索消融评测")
    ap.add_argument("--golden", default=str(PROJECT_ROOT / "evaluation" / "datasets" / "mindgraph_golden_v2.jsonl"))
    ap.add_argument("--top-k", type=int, default=TOP_K)
    ap.add_argument("--dataset-name", default="mindgraph_enterprise_v2")
    ap.add_argument("--dry-run", action="store_true", help="计算指标但不写库")
    args = ap.parse_args()

    container = get_container()
    db = container.database
    golden = load_or_generate_golden(db, Path(args.golden))

    if not golden:
        print("[abort] Golden Set 为空，无法评测。")
        return

    index_built = (container.mindgraph_index_root / "CURRENT").exists()
    if not index_built and not args.dry_run:
        print("[abort] 未检测到 MindGraph 索引（data/mindgraph_indexes/CURRENT 不存在）。")
        print("        请先构建索引：python scripts/sync_vault.py --vault demo-vault")
        print("        （离线/无 BGE 时可用 --dry-run 验证逻辑，但不会写库）")
        return

    pipeline = container.mindgraph_pipeline(top_k=args.top_k, graph_enabled=False)
    started = _utc_iso()
    results: list[tuple[str, dict]] = []
    for name, strategy, graph_enabled in STRATEGIES:
        metrics = evaluate_strategy(pipeline, golden, strategy, graph_enabled)
        results.append((name, metrics))
        print(f"[{name:18s}] R@1={metrics['recall_at_1']:.3f} R@3={metrics['recall_at_3']:.3f} "
              f"R@5={metrics['recall_at_5']:.3f} MRR={metrics['mrr']:.3f} hit@{args.top_k}={metrics['document_hit_rate']:.3f}")

    decision = None
    if len(results) >= 3:
        baseline = results[1][1]
        graph_metrics = results[2][1]
        decision = {
            "eligible": _should_enable_graph(graph_metrics, baseline),
            "default_route_recommendation": "conditional_only" if _should_enable_graph(graph_metrics, baseline) else "keep_graph_disabled",
        }
        print(f"[gate] graph eligible: {decision['eligible']} -> {decision['default_route_recommendation']}")

    if args.dry_run:
        print("\n[dry-run] 未写入 evaluation_runs。")
        return

    finished = _utc_iso()
    dataset_version = "auto-" + datetime.now(timezone.utc).strftime("%Y%m%d")
    for name, metrics in results:
        run_id = str(uuid.uuid4())
        db.execute(
            """INSERT INTO evaluation_runs (
                run_id, status, dataset_name, dataset_version, retrieval_strategy, chat_model,
                started_at, finished_at, configuration_json, summary_metrics_json,
                category_metrics_json, failed_cases_json, result_files_json, progress_messages_json, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id, "completed", args.dataset_name, dataset_version, name, "retrieval-only",
                started, finished,
                dumps({"top_k": args.top_k, "golden": Path(args.golden).name, "dataset_version": next(iter({case.get("dataset_version") for case in golden}), None), "sample_size": len(golden), "mode": "REAL_INDEX"}),
                dumps({**metrics, **({"graph_gate": decision} if decision and name.endswith("graph") else {})}),
                dumps({"sample_size": metrics.get("sample_size", 0)}),
                dumps([]),
                dumps([]),
                dumps(["completed"]),
                None,
            ),
        )
        print(f"[written] {name} -> run_id={run_id}")
    print(f"\n[done] 已写入 {len(results)} 条策略结果到 evaluation_runs；前端评测看板将立即显示。")


if __name__ == "__main__":
    main()
