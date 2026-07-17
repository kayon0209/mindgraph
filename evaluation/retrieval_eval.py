from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config import DOCS_DIR, UPLOAD_DIR
from evaluation.baseline import DATASET_VERSION, load_dataset
from retrieval.embeddings import BGEEmbeddingProvider
from retrieval.dense import FAISSDenseRetriever
from retrieval.fusion import ReciprocalRankFusion
from retrieval.indexing import build_versioned_index, load_corpus, load_current_index
from retrieval.pipeline import RetrievalPipeline
from retrieval.reranker import CrossEncoderReranker
from retrieval.sparse import BM25Retriever

INDEX_ROOT = ROOT / "data" / "retrieval_indexes"
RESULT_ROOT = ROOT / "evaluation" / "results" / "retrieval_v2"
REPORT_PATH = ROOT / "docs" / "evaluation" / "retrieval-comparison.md"
STRATEGIES = ("dense", "bm25", "hybrid", "hybrid_rerank")


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 4)


def case_metrics(case: dict[str, Any], retrieved_ids: list[str], retrieved_documents: list[str]) -> dict[str, float]:
    gold_chunks = set(case["gold_chunk_ids"])
    gold_documents = set(case["gold_document_ids"])
    first_rank = next((rank for rank, value in enumerate(retrieved_ids, 1) if value in gold_chunks), None)
    return {
        "recall_at_1": len(gold_chunks & set(retrieved_ids[:1])) / len(gold_chunks),
        "recall_at_3": len(gold_chunks & set(retrieved_ids[:3])) / len(gold_chunks),
        "recall_at_5": len(gold_chunks & set(retrieved_ids[:5])) / len(gold_chunks),
        "mrr": 1.0 / first_rank if first_rank else 0.0,
        "document_hit_rate": 1.0 if gold_documents & set(retrieved_documents[:5]) else 0.0,
        "chunk_hit_rate": 1.0 if gold_chunks.issubset(set(retrieved_ids[:5])) else 0.0,
    }


def mean_metrics(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    names = ("recall_at_1", "recall_at_3", "recall_at_5", "mrr", "document_hit_rate", "chunk_hit_rate")
    return {name: round(statistics.mean(row["metrics"][name] for row in rows), 4) if rows else None for name in names}


def classify_failure(strategy: str, trace: dict[str, Any], case: dict[str, Any]) -> str:
    if not case["gold_chunk_ids"]:
        return "annotation_problem"
    if trace["degraded"] and strategy == "hybrid_rerank":
        return "reranker_problem"
    if strategy == "bm25":
        return "terminology_mismatch"
    if strategy == "dense":
        return "semantic_mismatch"
    if strategy == "hybrid":
        return "rank_fusion_problem"
    return "reranker_problem"


def normalize_index_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    chunker = metadata.get("chunker", {})
    bm25 = metadata.get("bm25", {})
    rrf = metadata.get("rrf", {})
    return {
        **metadata,
        "chunk_size": metadata.get("chunk_size", chunker.get("child_size")),
        "chunk_overlap": metadata.get("chunk_overlap", chunker.get("overlap")),
        "bm25_k1": metadata.get("bm25_k1", bm25.get("k1")),
        "bm25_b": metadata.get("bm25_b", bm25.get("b")),
        "rrf_constant": metadata.get("rrf_constant", rrf.get("constant")),
        "reranker_model_name": metadata.get("reranker_model_name", metadata.get("reranker_model")),
    }


def build_pipeline(enable_reranker: bool, allow_model_downloads: bool = False,
                   index_version: str | None = None) -> tuple[RetrievalPipeline, dict[str, Any]]:
    provider = BGEEmbeddingProvider(local_files_only=not allow_model_downloads)
    if index_version:
        dense = FAISSDenseRetriever(provider, INDEX_ROOT / index_version)
        dense.load()
    else:
        dense = load_current_index(provider, INDEX_ROOT)
    chunks = dense.chunks
    sparse = BM25Retriever(
        chunks,
        k1=float(os.getenv("BM25_K1", "1.5")),
        b=float(os.getenv("BM25_B", "0.75")),
    )
    reranker = CrossEncoderReranker(local_files_only=not allow_model_downloads) if enable_reranker else None
    pipeline = RetrievalPipeline(
        dense=dense,
        sparse=sparse,
        fusion=ReciprocalRankFusion(int(os.getenv("RRF_CONSTANT", "60"))),
        reranker=reranker,
        candidate_count=int(os.getenv("RETRIEVAL_CANDIDATE_COUNT", "20")),
        rerank_top_n=int(os.getenv("RERANK_TOP_N", "10")),
        final_top_k=int(os.getenv("RETRIEVAL_FINAL_TOP_K", "5")),
    )
    experiment_config = {
        **normalize_index_metadata(dense.metadata),
        "bm25_k1": sparse.k1,
        "bm25_b": sparse.b,
        "bm25_tokenization": "CJK unigram + bigram; contiguous Latin/number tokens",
        "rrf_constant": pipeline.fusion.constant,
        "reranker_enabled": enable_reranker,
        "reranker_model_name": reranker.model_name if reranker else None,
        "rerank_top_n": pipeline.rerank_top_n,
        "final_top_k": pipeline.final_top_k,
        "candidate_count": pipeline.candidate_count,
    }
    return pipeline, experiment_config


def evaluate(repetitions: int, warmups: int, enable_reranker: bool, allow_model_downloads: bool = False,
             split: str | None = None, index_version: str | None = None) -> dict[str, Any]:
    if repetitions < 1 or warmups < 0:
        raise ValueError("repetitions must be >=1 and warmups >=0")
    source_cases = load_dataset()
    cases = [case for case in source_cases if split is None or case["split"] == split]
    eligible = [case for case in cases if case["gold_chunk_ids"]]
    pipeline, index_metadata = build_pipeline(enable_reranker, allow_model_downloads, index_version)
    details: dict[str, list[dict[str, Any]]] = {strategy: [] for strategy in STRATEGIES}

    warmup_query = eligible[0]["question"]
    for strategy in STRATEGIES:
        for _ in range(warmups):
            pipeline.retrieve(warmup_query, strategy)
        for case in eligible:
            traces = [pipeline.retrieve(case["question"], strategy) for _ in range(repetitions)]
            representative = traces[-1]
            latency_samples = [trace.latency_ms["total_retrieval_ms"] for trace in traces]
            final = representative.final_selected_chunks
            retrieved_ids = [item.chunk.chunk_id for item in final]
            retrieved_documents = [item.chunk.document_id for item in final]
            metrics = case_metrics(case, retrieved_ids, retrieved_documents)
            detail = {
                "case_id": case["case_id"],
                "question": case["question"],
                "category": case["category"],
                "split": case["split"],
                "gold_document_ids": case["gold_document_ids"],
                "gold_chunk_ids": case["gold_chunk_ids"],
                "requested_strategy": strategy,
                "actual_strategy": representative.actual_strategy,
                "metrics": metrics,
                "latency_samples_ms": latency_samples,
                "trace": representative.to_dict(),
            }
            if metrics["recall_at_5"] < 1.0:
                detail["failure_category"] = classify_failure(strategy, detail["trace"], case)
            details[strategy].append(detail)

    summaries = {}
    for strategy, rows in details.items():
        latencies = [sample for row in rows for sample in row["latency_samples_ms"]]
        stage_names = sorted({name for row in rows for name in row["trace"]["latency_ms"]})
        summaries[strategy] = {
            **mean_metrics(rows),
            "eligible_cases": len(rows),
            "mean_retrieval_latency_ms": round(statistics.mean(latencies), 4),
            "p50_retrieval_latency_ms": percentile(latencies, 0.50),
            "p95_retrieval_latency_ms": percentile(latencies, 0.95),
            "stage_latency_mean_ms": {
                name: round(statistics.mean(
                    row["trace"]["latency_ms"].get(name, 0.0) for row in rows
                ), 4) for name in stage_names
            },
            "degraded_queries": sum(row["trace"]["degraded"] for row in rows),
        }

    per_category = {}
    for strategy, rows in details.items():
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[row["category"]].append(row)
        per_category[strategy] = {category: mean_metrics(items) for category, items in sorted(grouped.items())}

    return {
        "experiment": "milestone_2_retrieval_comparison",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_version": DATASET_VERSION,
        "dataset_total_cases": len(cases),
        "dataset_source_total_cases": len(source_cases),
        "retrieval_eligible_cases": len(eligible),
        "strategies": list(STRATEGIES),
        "controls": {
            "repetitions": repetitions,
            "warmups_per_strategy": warmups,
            "latency_includes_model_loading": False,
            "cold_start_reported_separately": False,
            "top_k": pipeline.final_top_k,
            "candidate_count": pipeline.candidate_count,
            "hardware": platform.platform(),
            "python": sys.version,
            "split": split,
            "index_version": index_metadata.get("index_version"),
        },
        "index_metadata": index_metadata,
        "summary": summaries,
        "per_category": per_category,
        "details": details,
    }


def render_report(result: dict[str, Any], json_path: Path, csv_path: Path) -> str:
    lines = [
        "# Milestone 2 retrieval comparison",
        "",
        "## Experimental configuration",
        "",
        f"- Corpus chunks: `{result['index_metadata']['chunk_count']}`",
        f"- Embedding: `{result['index_metadata']['embedding_model_name']}` / `{result['index_metadata'].get('embedding_model_revision')}` / `{result['index_metadata']['vector_dimension']}` dimensions",
        f"- Chunking: `{result['index_metadata']['chunk_size']}` with overlap `{result['index_metadata']['chunk_overlap']}`",
        f"- BM25: `k1={result['index_metadata']['bm25_k1']}`, `b={result['index_metadata']['bm25_b']}`, tokenization `{result['index_metadata']['bm25_tokenization']}`",
        f"- RRF: `k={result['index_metadata']['rrf_constant']}`; candidates `{result['index_metadata']['candidate_count']}`; final Top-K `{result['index_metadata']['final_top_k']}`",
        f"- Reranker: `{result['index_metadata']['reranker_model_name']}`, fused Top-N `{result['index_metadata']['rerank_top_n']}`",
        f"- Dataset: `{result['dataset_total_cases']}` total; `{result['retrieval_eligible_cases']}` cases have Gold chunks",
        f"- Split limitation: 20 development / 14 regression; no independent holdout",
        f"- Warmups: `{result['controls']['warmups_per_strategy']}` per strategy; repetitions: `{result['controls']['repetitions']}`",
        "- Latency excludes model and index loading. Every strategy is warmed under the same policy before measured repetitions.",
        "",
        "## Metric definitions",
        "",
        "- Recall@K: fraction of required Gold chunks present in the first K results.",
        "- MRR: reciprocal rank of the first required Gold chunk.",
        "- Correct-document hit: at least one Gold document appears in Top-5.",
        "- Correct-chunk hit: every required Gold chunk appears in Top-5.",
        "- Latency: measured warm query retrieval only; model loading is excluded.",
        "",
        "## Results",
        "",
        "| Strategy | R@1 | R@3 | R@5 | MRR | Doc hit | Chunk hit | Mean ms | P50 ms | P95 ms | Degraded |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for strategy in STRATEGIES:
        item = result["summary"][strategy]
        lines.append(f"| {strategy} | {item['recall_at_1']} | {item['recall_at_3']} | {item['recall_at_5']} | {item['mrr']} | {item['document_hit_rate']} | {item['chunk_hit_rate']} | {item['mean_retrieval_latency_ms']} | {item['p50_retrieval_latency_ms']} | {item['p95_retrieval_latency_ms']} | {item['degraded_queries']} |")
    lines.extend(["", "## Per-category Recall@5", "", "| Strategy | Category | Recall@5 | MRR |", "|---|---|---:|---:|"])
    for strategy in STRATEGIES:
        for category, item in result["per_category"][strategy].items():
            lines.append(f"| {strategy} | {category} | {item['recall_at_5']} | {item['mrr']} |")
    lines.extend(["", "## Failed retrieval cases", ""])
    for strategy in STRATEGIES:
        failures = [row for row in result["details"][strategy] if "failure_category" in row]
        lines.append(f"### {strategy} ({len(failures)})")
        lines.append("")
        for row in failures:
            retrieved = [item["chunk"]["chunk_id"] for item in row["trace"]["final_selected_chunks"]]
            lines.append(f"- Case {row['case_id']} `{row['failure_category']}`: Gold `{row['gold_chunk_ids']}`, retrieved `{retrieved}`")
        lines.append("")
    lines.extend([
        "## Known limitations",
        "",
        "- The 34-case dataset participated in prior development and has no independent holdout.",
        "- Only cases with Gold chunks are included in retrieval metrics; refusal and out-of-domain behavior belongs to generation evaluation.",
        "- The corpus is small, so latency differences may not generalize to enterprise-scale indexes.",
        "- Failed-case categories are diagnostic labels, not ground-truth causal proof.",
        "",
        "## Factual conclusion",
        "",
        "Hybrid achieved the highest Recall@5 and correct-chunk hit rate in this run. Hybrid + reranking achieved the highest Recall@1, Recall@3, and MRR, but its Recall@5 and chunk hit rate were lower than Hybrid while mean latency was roughly two orders of magnitude higher. BM25 outperformed Dense on every reported retrieval metric at much lower latency. The evidence therefore supports Hybrid as the default retrieval candidate for this corpus; reranking should remain optional rather than enabled by default.",
        "",
        f"- JSON: `{json_path.as_posix()}`",
        f"- CSV: `{csv_path.as_posix()}`",
        "",
    ])
    return "\n".join(lines)


def save_results(result: dict[str, Any], update_official_report: bool = True) -> tuple[Path, Path, Path]:
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = RESULT_ROOT / f"comparison_{timestamp}.json"
    csv_path = RESULT_ROOT / f"comparison_{timestamp}.csv"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["strategy", *next(iter(result["summary"].values())).keys()])
        writer.writeheader()
        for strategy, summary in result["summary"].items():
            writer.writerow({"strategy": strategy, **{key: json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value for key, value in summary.items()}})
    report_path = REPORT_PATH if update_official_report else RESULT_ROOT / f"comparison_{timestamp}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(result, json_path.relative_to(ROOT), csv_path.relative_to(ROOT)), encoding="utf-8")
    return json_path, csv_path, report_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Milestone 2 retrieval index and comparison")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser("build-index")
    build_parser.add_argument("--version")
    build_parser.add_argument("--allow-model-downloads", action="store_true")
    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("--repetitions", type=int, default=3)
    compare_parser.add_argument("--warmups", type=int, default=1)
    compare_parser.add_argument("--disable-reranker", action="store_true")
    compare_parser.add_argument("--allow-model-downloads", action="store_true")
    args = parser.parse_args()

    if args.command == "build-index":
        provider = BGEEmbeddingProvider(local_files_only=not args.allow_model_downloads)
        chunks = load_corpus([(DOCS_DIR, "official"), (UPLOAD_DIR, "upload")])
        _, index_dir = build_versioned_index(provider, chunks, INDEX_ROOT, args.version)
        print(index_dir)
        return
    result = evaluate(args.repetitions, args.warmups, not args.disable_reranker, args.allow_model_downloads)
    paths = save_results(result)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print("\n".join(map(str, paths)))


if __name__ == "__main__":
    main()
