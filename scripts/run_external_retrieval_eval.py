"""Run retrieval evaluation over golden v2 (now 50) and emit Recall@5/MRR report."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json, time
from pathlib import Path
from infrastructure.database import ProductDatabase
from application.mindgraph_graph_store import MindGraphGraphStore
from application.mindgraph_index_service import MindGraphIndexService
import config
from evaluation.mindgraph_retrieval_eval import load_golden_dataset, evaluate_retrieval_cases
from retrieval.types import RetrievalTrace
import infrastructure.retrieval_factory as rf

ROOT = Path(config.ROOT)
db = ProductDatabase(ROOT / "data" / "product" / "product.sqlite3")
db.initialize()
index_root = ROOT / "data" / "retrieval_indexes"
graph_store = MindGraphGraphStore(db)

# Ensure index is current and contains external docs
cur_path = index_root / "CURRENT"
cur = cur_path.read_text(encoding="utf-8").strip() if cur_path.exists() else None
print(f"CURRENT index: {cur}")
# Use real BGE provider (local files)
from retrieval.embeddings import BGEEmbeddingProvider
try:
    provider = BGEEmbeddingProvider()
    print(f"embedding provider: {provider.model_name} dim={provider.dimension}")
except Exception as e:
    print(f"embedding provider failed: {e}")
    sys.exit(2)

def make_pipeline(graph_enabled: bool):
    from infrastructure.retrieval_factory import create_mindgraph_retrieval_pipeline
    # monkey patch to ensure BGE is used
    rf.BGEEmbeddingProvider = lambda: provider
    return create_mindgraph_retrieval_pipeline(index_root, graph_store, final_top_k=5, graph_enabled=graph_enabled)

cases = load_golden_dataset()
print(f"golden cases: {len(cases)} version={cases[0]['dataset_version'] if cases else '?'}")
# split counts
from collections import Counter
print("query_type counts:", Counter(c["query_type"] for c in cases))
print("split counts:", Counter(c["split"] for c in cases))

def eval_for(graph_enabled: bool):
    pipeline = make_pipeline(graph_enabled)
    def retrieve(case):
        q = case["question"]
        # use hybrid for all; pipeline respects graph_enabled
        trace = pipeline.retrieve(q, "hybrid", access_scope=None)
        return trace
    report = evaluate_retrieval_cases(cases, retrieve, top_k=5)
    return report

for ge in [False, True]:
    print(f"\n===== graph_enabled={ge} =====")
    report = eval_for(ge)
    summary = report["summary"]
    counts = report["counts"]
    print(f"counts: {counts}")
    print(f"summary Recall@5={summary['recall_at_k']} Precision@5={summary['precision_at_k']} MRR={summary['mrr']}")
    # per file
    # show failed
    failed = report["failed_cases"]
    print(f"failed: {len(failed)} cases")
    for f in failed[:10]:
        print(f"  {f['case_id']} recall={f['metrics']['recall_at_k']} gold={f['gold_vault_paths']} evidence={f.get('evidence_stages')}")
    # save
    out = ROOT / f"evaluation/results/retrieval_v2_external_graph_{'on' if ge else 'off'}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {out}")

# Also run variant: filter to external_policy only
print("\n===== external_policy subset (4 cases) =====")
ext_cases = [c for c in cases if c["query_type"] == "external_policy"]
print(f"ext cases: {len(ext_cases)}")
for ge in [False, True]:
    pipeline = make_pipeline(ge)
    def retrieve(case):
        return pipeline.retrieve(case["question"], "hybrid")
    report = evaluate_retrieval_cases(ext_cases, retrieve, top_k=5)
    print(f"graph={ge} recall@5={report['summary']['recall_at_k']} mrr={report['summary']['mrr']}")
