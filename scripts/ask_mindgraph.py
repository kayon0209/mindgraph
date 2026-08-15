"""MindGraph 可信问答 CLI（M1-D4 验证用）。

直接走 get_container().mindgraph_chat，验证「Hybrid + 图谱扩展 + 关系证据」闭环。
不依赖 FastAPI server，便于本地快速验证。

用法：
    python scripts/ask_mindgraph.py "我之前关于 Agent 评测设计过哪些指标？"
    python scripts/ask_mindgraph.py "..." --no-graph     # 关闭图谱扩展（消融对比）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(ROOT))

from api.dependencies import get_container  # noqa: E402
from api.schemas.chat import ChatRequest  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="MindGraph 可信问答")
    ap.add_argument("question", help="提问内容")
    ap.add_argument("--no-graph", action="store_true", help="关闭图谱一跳扩展（消融对比）")
    ap.add_argument("--strategy", default="hybrid", choices=["dense", "bm25", "hybrid", "hybrid_rerank"])
    args = ap.parse_args()

    req = ChatRequest(
        question=args.question,
        retrieval_strategy=args.strategy,
        graph_enabled=not args.no_graph,
        include_retrieval_trace=True,
    )
    result = get_container().mindgraph_chat.answer(req)

    print("\n" + "=" * 70)
    print(f"策略: {result.actual_strategy} | 图谱: {'开启' if result.retrieval_trace and result.retrieval_trace.graph_enabled else '关闭'}")
    print("=" * 70)
    print("\n【回答】\n" + result.answer)

    if result.citations:
        print("\n【引用来源】")
        for c in result.citations:
            tag = " [图谱关联]" if c.document_id and isinstance(c.document_id, str) else ""
            print(f"  [{c.final_rank}] {c.document_name} / {c.section_path or '-'}")
    else:
        print("\n（无引用来源）")

    if result.retrieval_trace and result.retrieval_trace.graph_links:
        print("\n【知识图谱关联】")
        for g in result.retrieval_trace.graph_links:
            print(f"  {g['source_note_id'][:8]} --{g['relation_type']}--> 《{g['target_title']}》(置信度 {g['confidence']:.2f})")
    print()


if __name__ == "__main__":
    main()
