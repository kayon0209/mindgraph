"""MindGraph 检索消融评测（BM25 / BM25+Vector / BM25+Vector+Graph）。

复用现有 MindGraph 检索管线与 evaluation_runs 表（前端评测看板直接读取该表）。
三组策略：
    - bm25              ：纯稀疏检索（图谱关闭）
    - bm25_vector       ：Hybrid（稀疏+稠密，图谱关闭）
    - bm25_vector_graph ：Hybrid + 图谱一跳扩展（图谱开启）

用法：
    python scripts/run_ablation.py
    python scripts/run_ablation.py --golden evaluation/datasets/mindgraph_golden.jsonl
    python scripts/run_ablation.py --dry-run          # 计算指标但不写库（沙箱/离线验证用）
    python scripts/run_ablation.py --generate-only    # 仅生成/刷新 Golden Set 文件

依赖：
    - 需先构建 MindGraph 索引：python scripts/sync_vault.py --vault D:/ObsidianVault
    - 需要真实 BGE 嵌入（首次自动下载，见 .env 的 BGE_LOCAL_FILES_ONLY）
"""
from __future__ import annotations

import argparse
import json
import re
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

DATE_TITLE_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$")

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
    """加载 Golden Set；不存在则从真实库自动派生：
    - graph_link：confirmed 关系（source 标题 → target 笔记），检验图谱扩展是否生效；
    - self_recall：非日期标题的笔记（query=标题，gold=自身），补足样本量。

    gold key 使用 **vault_path**（重扫描不变），而非 note_id（--reset 后会变），
    避免重新同步 Vault 后评测「归零」。
    """
    if golden_path.exists() and not force:
        cases = [json.loads(line) for line in golden_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        # 兼容旧 schema（gold_note_ids）：强制重生成
        if cases and "gold_vault_paths" not in cases[0]:
            print("[golden] 检测到旧 schema（gold_note_ids），强制重生成")
            force = True
        else:
            print(f"[golden] loaded {len(cases)} cases from {golden_path}")
            return cases
    print(f"[golden] auto-generating from DB -> {golden_path}")
    cases: list[dict] = []
    seen: set = set()

    # 1) graph_link：来自 confirmed 关系
    confirmed = db.fetch_all(
        "SELECT source_note_id, target_note_id, relation_type FROM note_relations WHERE status='confirmed'"
    )
    meta = {row["note_id"]: (row["title"] or "", row["vault_path"] or "")
            for row in db.fetch_all("SELECT note_id, title, vault_path FROM notes")}
    for rel in confirmed:
        q = meta.get(rel["source_note_id"], ("", ""))[0].strip()
        tgt_path = meta.get(rel["target_note_id"], ("", ""))[1]
        if not q or not tgt_path:
            continue
        key = (q, tgt_path)
        if key in seen:
            continue
        seen.add(key)
        cases.append({"question": q, "gold_vault_paths": [tgt_path], "category": "graph_link"})

    # 2) self_recall：非日期标题的笔记，确定性采样补足到约 50 条
    notes = db.fetch_all("SELECT note_id, title, vault_path FROM notes")
    pool = [n for n in notes if n["title"] and n["vault_path"] and not DATE_TITLE_RE.match(n["title"].strip())]
    pool.sort(key=lambda n: n["vault_path"])
    target = 50
    for n in pool:
        if len(cases) >= target:
            break
        q = n["title"].strip()
        if q in seen:
            continue
        seen.add(q)
        cases.append({"question": q, "gold_vault_paths": [n["vault_path"]], "category": "self_recall"})

    golden_path.parent.mkdir(parents=True, exist_ok=True)
    golden_path.write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + ("\n" if cases else ""),
        encoding="utf-8",
    )
    print(f"[golden] wrote {len(cases)} cases ({golden_path})")
    return cases


def evaluate_strategy(pipeline, cases: list[dict], strategy: str, graph_enabled: bool) -> dict:
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
        "recall_at_1": sum(recalls[1]) / n,
        "recall_at_3": sum(recalls[3]) / n,
        "recall_at_5": sum(recalls[5]) / n,
        "mrr": sum(mrrs) / n,
        "document_hit_rate": sum(hit_rates) / n,
        "chunk_hit_rate": sum(hit_rates) / n,
        "mean_retrieval_latency_ms": sum(latencies) / n,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="MindGraph 检索消融评测")
    ap.add_argument("--golden", default=str(ROOT.parent / "evaluation" / "datasets" / "mindgraph_golden.jsonl"))
    ap.add_argument("--top-k", type=int, default=TOP_K)
    ap.add_argument("--dataset-name", default="mindgraph_auto")
    ap.add_argument("--dry-run", action="store_true", help="计算指标但不写库")
    ap.add_argument("--generate-only", action="store_true", help="仅生成 Golden Set 文件")
    args = ap.parse_args()

    container = get_container()
    db = container.database
    golden = load_or_generate_golden(db, Path(args.golden), force=args.generate_only)

    if args.generate_only:
        return

    if not golden:
        print("[abort] Golden Set 为空，无法评测。")
        return

    index_built = (container.mindgraph_index_root / "CURRENT").exists()
    if not index_built and not args.dry_run:
        print("[abort] 未检测到 MindGraph 索引（data/mindgraph_indexes/CURRENT 不存在）。")
        print("        请先构建索引：python scripts/sync_vault.py --vault D:/ObsidianVault")
        print("        （离线/无 BGE 时可用 --dry-run 验证逻辑，但不会写库）")
        return

    pipeline = container.mindgraph_pipeline(top_k=args.top_k, graph_enabled=True)
    started = _utc_iso()
    results: list[tuple[str, dict]] = []
    for name, strategy, graph_enabled in STRATEGIES:
        metrics = evaluate_strategy(pipeline, golden, strategy, graph_enabled)
        results.append((name, metrics))
        print(f"[{name:18s}] R@1={metrics['recall_at_1']:.3f} R@3={metrics['recall_at_3']:.3f} "
              f"R@5={metrics['recall_at_5']:.3f} MRR={metrics['mrr']:.3f} hit@{args.top_k}={metrics['document_hit_rate']:.3f}")

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
                dumps({"top_k": args.top_k, "golden": Path(args.golden).name, "sample_size": len(golden)}),
                dumps(metrics),
                dumps({}),
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
