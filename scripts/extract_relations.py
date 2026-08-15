#!/usr/bin/env python3
"""MindGraph 关系抽取自动化触发脚本。

复用应用容器（自动拿到 DB / 激活索引 / Chat Provider），运行 RelationExtractionService
把"语义相似 + 规则"候选关系写入 note_relations(status='proposed')。

用法：
  python scripts/extract_relations.py                      # 默认：离线 BGE 语义相似度，top_k=5, 阈值0.5
  python scripts/extract_relations.py --method all        # 额外加入标签/标题规则候选
  python scripts/extract_relations.py --use-llm           # 候选再用 LLM 精炼（需配置 Chat Provider）
  python scripts/extract_relations.py --dry-run           # 只预测不落库
  python scripts/extract_relations.py --top-k 8 --threshold 0.6 --max 500

幂等：已存在的 pair（任意状态）不会重复写入。
"""
import argparse
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
for p in (SRC, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)


def main() -> int:
    ap = argparse.ArgumentParser(description="MindGraph 自动关系抽取")
    ap.add_argument("--method", choices=["embedding", "all"], default="embedding")
    ap.add_argument("--top-k", type=int, default=5, dest="top_k")
    ap.add_argument("--threshold", type=float, default=0.5, dest="similarity_threshold")
    ap.add_argument("--max", type=int, default=300, dest="max_candidates")
    ap.add_argument("--use-llm", action="store_true", dest="use_llm")
    ap.add_argument("--dry-run", action="store_true", dest="dry_run")
    args = ap.parse_args()

    try:
        from api.dependencies import get_container
    except Exception as exc:
        print(f"[abort] 无法导入应用容器：{exc}")
        traceback.print_exc()
        return 1

    container = get_container()
    svc = container.relation_extraction
    print(
        f"[run] method={args.method} top_k={args.top_k} threshold={args.similarity_threshold} "
        f"max={args.max_candidates} use_llm={args.use_llm} dry_run={args.dry_run}"
    )
    try:
        result = svc.extract(
            method=args.method,
            top_k=args.top_k,
            similarity_threshold=args.similarity_threshold,
            max_candidates=args.max_candidates,
            use_llm=args.use_llm,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"[error] 抽取失败：{exc}")
        return 1

    print(f"[result] ok={result.get('ok')} dry_run={result.get('dry_run')}")
    print(f"  notes_scanned       = {result.get('notes_scanned')}")
    print(f"  candidates(pre-dedup)= {result.get('candidates_before_dedup')}")
    print(f"  filtered_noise      = {result.get('filtered_noise')}")
    print(f"  inserted            = {result.get('inserted')}")
    print(f"  skipped_existing    = {result.get('skipped_existing')}")
    print(f"  truncated_by_max    = {result.get('truncated_by_max')}")
    print(f"  conflicts_flagged   = {result.get('conflicts_flagged')}")
    print(f"  remaining_proposed  = {result.get('remaining_proposed')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
