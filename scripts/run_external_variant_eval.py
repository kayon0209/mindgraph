"""External subset eval: Chinese baseline vs query-variant RRF merge.

Dataset-driven: reads `query_translations` from each external_policy golden case
(human-translated EN, added 2026-08-27). Verifies the retrieval-layer
query_variants path (mindgraph_pipeline.py).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import config
from infrastructure.database import ProductDatabase
from application.mindgraph_graph_store import MindGraphGraphStore
import infrastructure.retrieval_factory as rf
from retrieval.embeddings import BGEEmbeddingProvider
from evaluation.mindgraph_retrieval_eval import load_golden_dataset, evaluate_retrieval_cases
import evaluation.mindgraph_retrieval_eval as _eval
_eval.RetrievalTrace = __import__("retrieval.types", fromlist=["RetrievalTrace"]).RetrievalTrace
from infrastructure.retrieval_factory import create_mindgraph_retrieval_pipeline


def main() -> None:
    db = ProductDatabase(Path(config.ROOT) / "data/product/product.sqlite3")
    db.initialize()
    prov = BGEEmbeddingProvider()
    rf.BGEEmbeddingProvider = lambda: prov
    index_root = Path(config.ROOT) / "data/retrieval_indexes"
    graph_store = MindGraphGraphStore(db)
    pipeline = create_mindgraph_retrieval_pipeline(index_root, graph_store, final_top_k=5, graph_enabled=False)

    cases = [c for c in load_golden_dataset() if c["query_type"] == "external_policy"]
    print(f"external cases: {len(cases)}\n")

    def run(subset, tag, variants_field):
        report = evaluate_retrieval_cases(
            subset,
            lambda c: pipeline.retrieve(c["question"], "hybrid", query_variants=c.get(variants_field)),
            top_k=5,
        )
        print(f"=== {tag}: Recall@5={report['summary']['recall_at_k']} MRR={report['summary']['mrr']}")
        for d in report["details"]:
            cid = d.get("case_id") or d.get("id")
            print(f"  {cid}: recall={d['metrics'].get('recall_at_k')}")
        print()
        return report

    # 中文基线（无变体）
    run(cases, "CHINESE baseline (no variants)", "_no_such_field")

    # 变体融合（中文原 query + 英文翻译变体）
    run(cases, "QUERY VARIANTS (zh + EN translations)", "query_translations")


if __name__ == "__main__":
    main()
