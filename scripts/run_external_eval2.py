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

db = ProductDatabase(Path(config.ROOT)/"data/product/product.sqlite3")
db.initialize()
prov = BGEEmbeddingProvider()
print(f"provider {prov.model_name} dim={prov.dimension}")
rf.BGEEmbeddingProvider = lambda: prov
# src/ 与仓库根同时在 sys.path 时，eval 的 `from src.retrieval.types import ...`
# 会与管线的裸 `retrieval.types` 形成两套模块；统一为同一类对象，避免 isinstance 失配。
import evaluation.mindgraph_retrieval_eval as _eval
_eval.RetrievalTrace = __import__("retrieval.types", fromlist=["RetrievalTrace"]).RetrievalTrace
from infrastructure.retrieval_factory import create_mindgraph_retrieval_pipeline
index_root = Path(config.ROOT)/"data/retrieval_indexes"
graph_store = MindGraphGraphStore(db)
cases = load_golden_dataset()
print(f"golden {len(cases)}")
for ge in [False, True]:
    pipeline = create_mindgraph_retrieval_pipeline(index_root, graph_store, final_top_k=5, graph_enabled=ge)
    print(f"\n=== graph_enabled={ge} dense_chunks={len(pipeline.base.dense.chunks)} ===")
    def retrieve(case):
        return pipeline.retrieve(case["question"], "hybrid")
    report = evaluate_retrieval_cases(cases, retrieve, top_k=5)
    print(f" recall@5={report['summary']['recall_at_k']} mrr={report['summary']['mrr']} prec={report['summary']['precision_at_k']}")
    failed = [f["case_id"] for f in report["failed_cases"][:15]]
    print(f" failed {len(report['failed_cases'])}: {failed}")
    import json
    out = ROOT / f"evaluation/results/retrieval_external_graph_{'on' if ge else 'off'}.json"
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f" saved {out}")
# external subset
ext = [c for c in cases if c["query_type"]=="external_policy"]
print(f"\n--- external subset {len(ext)} ---")
for ge in [False,True]:
    pipeline = create_mindgraph_retrieval_pipeline(index_root, graph_store, final_top_k=5, graph_enabled=ge)
    report = evaluate_retrieval_cases(ext, lambda case: pipeline.retrieve(case["question"],"hybrid"), top_k=5)
    print(f" ge={ge} recall={report['summary']['recall_at_k']} details {[d['metrics'] for d in report['details']]}")
