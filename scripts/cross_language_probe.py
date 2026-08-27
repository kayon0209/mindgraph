"""Cross-language recall probe — external subset (Chinese query vs English variant).

目的：量化"中文问题找不到英文 handbook"（外部子集 Recall@5=0.25）能否通过
英文翻译查询变体改善。复用现有混合检索管线（dense bge-zh + BM25 + RRF）。

结论仅作内部决策参考，不构成生产质量声明。
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

# 英文翻译变体（对照 mattermost.md 内容人工构造）
EN_VARIANTS = {
    "ext-mattermost-pay-usca-2026-08-27": "How are Mattermost United States and Canada staff members paid and reimbursed for expenses?",
    "ext-mattermost-pay-uk-2026-08-27": "How is Mattermost UK staff pay processed and reimbursed?",
    "ext-mattermost-pay-de-2026-08-27": "How do Mattermost Germany employees receive salary and expense reimbursements?",
    "ext-mattermost-pay-row-2026-08-27": "How do Mattermost Rest of World employees get paid?",
}


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

    def run(subset, tag):
        report = evaluate_retrieval_cases(subset, lambda c: pipeline.retrieve(c["question"], "hybrid"), top_k=5)
        qmap = {c["case_id"]: c["question"] for c in subset}
        print(f"=== {tag}: Recall@5={report['summary']['recall_at_k']} MRR={report['summary']['mrr']}")
        for d in report["details"]:
            cid = d.get("case_id") or d.get("id")
            print(f"  {cid}: hit={d['metrics'].get('hit_at_k')} question={qmap.get(cid, '?')[:60]!r}")
        print()
        return report

    run(cases, "CHINESE (baseline)")
    en_cases = []
    for c in cases:
        c2 = dict(c)
        c2["question"] = EN_VARIANTS.get(c["case_id"], c["question"])
        en_cases.append(c2)
    run(en_cases, "ENGLISH (translated variant)")


if __name__ == "__main__":
    main()
