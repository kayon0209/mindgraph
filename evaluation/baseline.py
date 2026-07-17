"""Milestone 1 reproducible baseline for the frozen current implementation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config import (  # noqa: E402
    CHAT_MODEL,
    COLLECTION_NAME,
    DEFAULT_TOP_K,
    DOCS_DIR,
    EMBED_MODEL,
    MAX_CONTEXT_CHARS,
    SIMILARITY_THRESHOLD,
    UPLOAD_DIR,
    ZHIPU_API_KEY,
)
from document_loader import (  # noqa: E402
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    load_all_kb_chunks,
)
from embedder import embed_query, get_backend_type  # noqa: E402
from rag_engine import (  # noqa: E402
    ATTACK_PATTERNS,
    OUT_OF_SCOPE_KEYWORDS,
    REJECT_ANSWER,
    SourceChunk,
    _client,
    _distance_threshold,
    _get_collection,
    _lexical_score,
    _query_raw,
    build_context,
    build_index,
    fallback_answer,
    generate_answer,
)
from special_cases import try_prd_short_circuit  # noqa: E402

DATASET_PATH = ROOT / "evaluation" / "datasets" / "expense_qa_v1.jsonl"
RESULTS_DIR = ROOT / "evaluation" / "results" / "baseline_current"
REPORT_PATH = ROOT / "docs" / "evaluation" / "baseline-current.md"
STRATEGY = "baseline_current"
DATASET_VERSION = "1.0.0"


def chunk_id(source: str, index: int) -> str:
    return f"{source}::{index}"


def load_dataset(path: Path = DATASET_PATH) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    validate_dataset(cases)
    return cases


def current_chunk_catalog() -> dict[str, dict[str, Any]]:
    chunks = load_all_kb_chunks([(DOCS_DIR, "official"), (UPLOAD_DIR, "upload")])
    return {
        chunk_id(item["metadata"]["doc_name"], item["metadata"]["chunk_index"]): item
        for item in chunks
    }


def validate_dataset(cases: list[dict[str, Any]]) -> None:
    required = {
        "case_id", "question", "category", "split", "expected_behavior",
        "reference_answer", "required_facts", "forbidden_facts",
        "gold_document_ids", "gold_chunk_ids", "acceptable_chunk_ids",
        "should_answer", "should_refuse", "should_abstain", "notes",
        "dataset_version",
    }
    ids = set()
    catalog = current_chunk_catalog()
    known_documents = {item["metadata"]["doc_name"] for item in catalog.values()}
    errors: list[str] = []
    for case in cases:
        missing = required - case.keys()
        if missing:
            errors.append(f"case {case.get('case_id')}: missing {sorted(missing)}")
            continue
        case_id = case["case_id"]
        if case_id in ids:
            errors.append(f"case {case_id}: duplicate id")
        ids.add(case_id)
        if case["split"] not in {"development", "regression"}:
            errors.append(f"case {case_id}: invalid split {case['split']}")
        if case["expected_behavior"] not in {"answer", "refuse", "abstain"}:
            errors.append(f"case {case_id}: invalid behavior")
        flags = sum(bool(case[name]) for name in ("should_answer", "should_refuse", "should_abstain"))
        if flags != 1:
            errors.append(f"case {case_id}: exactly one behavior flag must be true")
        for document in case["gold_document_ids"]:
            if document not in known_documents:
                errors.append(f"case {case_id}: missing document {document}")
        for evidence_id in case["gold_chunk_ids"] + case["acceptable_chunk_ids"]:
            if evidence_id not in catalog:
                errors.append(f"case {case_id}: missing chunk {evidence_id}")
    if len(cases) != 34 or ids != set(range(1, 35)):
        errors.append("dataset must contain case ids 1..34 exactly once")
    if errors:
        raise ValueError("Dataset validation failed:\n" + "\n".join(errors))


def _nullable_metric(value: float | int | None, source: str = "unavailable", **extra: Any) -> dict[str, Any]:
    return {"value": value, "source": source, **extra}


def _source_payload(source: SourceChunk, rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "chunk_id": chunk_id(source.source, source.chunk_index),
        "document_id": source.source,
        "section_path": source.section_path,
        "distance": source.distance,
        "text": source.text,
    }


def _retrieve_with_timing(question: str, api_key: str, k: int = 5) -> tuple[list[SourceChunk], dict[str, float]]:
    timings = {"query_embedding_ms": 0.0, "vector_search_ms": 0.0}
    collection = _get_collection(create=False)
    start = time.perf_counter()
    vector = embed_query(_client(api_key), question)
    timings["query_embedding_ms"] = round((time.perf_counter() - start) * 1000, 3)
    query_k = collection.count() if get_backend_type() == "hash" else k
    start = time.perf_counter()
    raw = _query_raw(collection, vector, query_k)
    timings["vector_search_ms"] = round((time.perf_counter() - start) * 1000, 3)
    sources: list[SourceChunk] = []
    documents = raw.get("documents", [[]])[0]
    metadata = raw.get("metadatas", [[]])[0]
    distances = raw.get("distances", [[]])[0]
    for document, meta, distance in zip(documents, metadata, distances):
        sources.append(SourceChunk(
            text=document,
            source=str(meta.get("doc_name") or meta.get("source", "")),
            chunk_index=int(meta.get("chunk_index", 0)),
            section_path=meta.get("section_path"),
            distance=float(distance) if distance is not None else None,
        ))
    if get_backend_type() == "hash":
        sources.sort(key=lambda source: (
            -_lexical_score(question, f"{source.section_path or ''}\n{source.text}"),
            source.distance if source.distance is not None else 1.0,
        ))
        sources = sources[:k]
    return sources, timings


def run_case(case: dict[str, Any], api_key: str, generate: bool) -> dict[str, Any]:
    question = case["question"].strip()
    total_start = time.perf_counter()
    stage = {
        "document_load_ms": 0.0,
        "rule_handling_ms": 0.0,
        "query_embedding_ms": 0.0,
        "vector_search_ms": 0.0,
        "context_build_ms": 0.0,
        "generation_ms": 0.0,
        "ttft_ms": None,
    }
    degradation_reason = None
    path = "rag"
    answer = ""
    sources: list[SourceChunk] = []

    start = time.perf_counter()
    lowered = question.lower()
    if any(pattern in lowered for pattern in ATTACK_PATTERNS):
        path, answer = "attack_rule_short_circuit", REJECT_ANSWER
    elif any(keyword in lowered for keyword in OUT_OF_SCOPE_KEYWORDS):
        path, answer = "scope_rule_short_circuit", REJECT_ANSWER
    else:
        fixed = try_prd_short_circuit(question)
        if fixed is not None:
            path, answer = "special_case_short_circuit", fixed
    stage["rule_handling_ms"] = round((time.perf_counter() - start) * 1000, 3)

    if not answer:
        try:
            sources, retrieval_timing = _retrieve_with_timing(question, api_key, 5)
            stage.update(retrieval_timing)
        except Exception as exc:
            path = "system_error"
            degradation_reason = f"retrieval_error: {type(exc).__name__}: {exc}"
        threshold = _distance_threshold()
        if sources and sources[0].distance is not None and sources[0].distance > threshold:
            sources = []
            degradation_reason = f"distance_threshold_exceeded: {threshold}"
        if not sources:
            answer = "未在制度文件中找到足够依据。建议联系 HR/财务确认。"
            path = path if path == "system_error" else "no_evidence_fallback"
        elif generate:
            start = time.perf_counter()
            context = build_context(sources[:DEFAULT_TOP_K])
            stage["context_build_ms"] = round((time.perf_counter() - start) * 1000, 3)
            start = time.perf_counter()
            try:
                answer = generate_answer(api_key, question, context)
            except Exception as exc:
                answer = fallback_answer(question, sources[:DEFAULT_TOP_K])
                degradation_reason = f"generation_error: {type(exc).__name__}: {exc}"
                path = "generation_fallback"
            stage["generation_ms"] = round((time.perf_counter() - start) * 1000, 3)
        else:
            answer = fallback_answer(question, sources[:DEFAULT_TOP_K])
            path = "retrieval_only"
            degradation_reason = "generation_disabled"

    retrieved = [_source_payload(source, rank) for rank, source in enumerate(sources, 1)]
    retrieved_ids = [item["chunk_id"] for item in retrieved]
    retrieved_documents = [item["document_id"] for item in retrieved]
    gold_chunks = set(case["gold_chunk_ids"])
    acceptable = gold_chunks | set(case["acceptable_chunk_ids"])
    gold_documents = set(case["gold_document_ids"])
    first_rank = next((index + 1 for index, item in enumerate(retrieved_ids) if item in gold_chunks), None)

    required_hits = [fact for fact in case["required_facts"] if fact.lower() in answer.lower()]
    forbidden_hits = [fact for fact in case["forbidden_facts"] if fact.lower() in answer.lower()]
    completeness = len(required_hits) / len(case["required_facts"]) if case["required_facts"] else None
    is_refusal = REJECT_ANSWER.rstrip("。") in answer
    no_answer = "未在制度文件中找到足够依据" in answer or "建议联系 HR/财务" in answer
    citation_ids = set(retrieved_ids[:DEFAULT_TOP_K]) if sources and path not in {"special_case_short_circuit"} else set()
    citation_accuracy = (
        len(citation_ids & acceptable) / len(citation_ids)
        if citation_ids and acceptable else (0.0 if case["should_answer"] else None)
    )
    answer_correctness = None
    if case["should_refuse"]:
        answer_correctness = 1.0 if is_refusal else 0.0
    elif case["should_abstain"]:
        answer_correctness = 1.0 if no_answer or "补充" in answer else 0.0
    elif completeness is not None:
        answer_correctness = 1.0 if completeness == 1.0 and not forbidden_hits else 0.0

    stage["total_latency_ms"] = round((time.perf_counter() - total_start) * 1000, 3)
    return {
        "case_id": case["case_id"],
        "question": question,
        "category": case["category"],
        "split": case["split"],
        "execution_path": path,
        "degradation_reason": degradation_reason,
        "answer": answer,
        "retrieved": retrieved,
        "metrics": {
            "retrieval": {
                "recall_at_1": len(gold_chunks & set(retrieved_ids[:1])) / len(gold_chunks) if gold_chunks else None,
                "recall_at_3": len(gold_chunks & set(retrieved_ids[:3])) / len(gold_chunks) if gold_chunks else None,
                "recall_at_5": len(gold_chunks & set(retrieved_ids[:5])) / len(gold_chunks) if gold_chunks else None,
                "reciprocal_rank": 1.0 / first_rank if first_rank else (0.0 if gold_chunks else None),
                "document_hit": 1.0 if gold_documents and gold_documents & set(retrieved_documents) else (0.0 if gold_documents else None),
                "chunk_hit": 1.0 if gold_chunks and gold_chunks.issubset(set(retrieved_ids)) else (0.0 if gold_chunks else None),
            },
            "generation": {
                "answer_correctness": answer_correctness,
                "answer_correctness_method": "deterministic_fact_check",
                "citation_accuracy": citation_accuracy,
                "evidence_consistency": None,
                "evidence_consistency_method": "manual_review_required",
                "completeness": completeness,
                "no_answer_accuracy": (1.0 if no_answer else 0.0) if case["should_abstain"] else None,
                "refusal_accuracy": (1.0 if is_refusal else 0.0) if case["should_refuse"] else None,
                "required_fact_hits": required_hits,
                "forbidden_fact_hits": forbidden_hits,
            },
            "system": {
                "stages": stage,
                "token_usage": _nullable_metric(None),
                "estimated_cost": _nullable_metric(None, currency=None, pricing_version=None),
            },
        },
    }


def _mean(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return round(sum(present) / len(present), 4) if present else None


def summarize(details: list[dict[str, Any]]) -> dict[str, Any]:
    def metrics_for(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "count": len(rows),
            "retrieval": {name: _mean(row["metrics"]["retrieval"][name] for row in rows) for name in (
                "recall_at_1", "recall_at_3", "recall_at_5", "reciprocal_rank", "document_hit", "chunk_hit"
            )},
            "generation": {name: _mean(row["metrics"]["generation"][name] for row in rows) for name in (
                "answer_correctness", "citation_accuracy", "evidence_consistency", "completeness", "no_answer_accuracy", "refusal_accuracy"
            )},
            "system": {
                name: _mean(row["metrics"]["system"]["stages"][name] for row in rows)
                for name in ("query_embedding_ms", "vector_search_ms", "context_build_ms", "generation_ms", "ttft_ms", "total_latency_ms")
            },
        }

    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for detail in details:
        by_category[detail["category"]].append(detail)
    return {
        "overall": metrics_for(details),
        "by_split": {split: metrics_for([row for row in details if row["split"] == split]) for split in ("development", "regression")},
        "by_category": {category: metrics_for(rows) for category, rows in sorted(by_category.items())},
    }


def environment_snapshot() -> dict[str, Any]:
    documents = []
    for path in sorted(DOCS_DIR.glob("*.md")):
        data = path.read_bytes()
        documents.append({"name": path.name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    try:
        git_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        git_head = None
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "git_head": git_head,
        "python": sys.version,
        "platform": platform.platform(),
        "chat_model": CHAT_MODEL,
        "embedding_backend": get_backend_type(),
        "embedding_model": EMBED_MODEL if get_backend_type() == "zhipu" else get_backend_type(),
        "collection_name": COLLECTION_NAME,
        "chunking": {"chunk_size": DEFAULT_CHUNK_SIZE, "chunk_overlap": DEFAULT_CHUNK_OVERLAP},
        "retrieval": {"default_top_k": DEFAULT_TOP_K, "similarity_threshold": SIMILARITY_THRESHOLD, "max_context_chars": MAX_CONTEXT_CHARS},
        "knowledge_documents": documents,
    }


def classify_bad_cases(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bad_cases = []
    for row in details:
        reasons = []
        retrieval = row["metrics"]["retrieval"]
        generation = row["metrics"]["generation"]
        if retrieval["recall_at_5"] == 0.0:
            reasons.append("retrieval_miss")
        if generation["answer_correctness"] == 0.0:
            reasons.append("generation_or_rule_error")
        if generation["citation_accuracy"] == 0.0:
            reasons.append("citation_error")
        if row["execution_path"].endswith("short_circuit") and row["retrieved"] == [] and row["metrics"]["generation"]["answer_correctness"] is not None:
            reasons.append("rule_short_circuit_no_evidence")
        if row["execution_path"] == "system_error":
            reasons.append("system_error")
        if reasons:
            bad_cases.append({"case_id": row["case_id"], "reasons": reasons, "execution_path": row["execution_path"]})
    return bad_cases


def render_report(result: dict[str, Any]) -> str:
    summary = result["summary"]
    overall = summary["overall"]
    lines = [
        "# 当前系统基线报告",
        "",
        f"- 策略：`{STRATEGY}`",
        f"- 数据集：`expense_qa_v1` / `{DATASET_VERSION}`（34 题）",
        f"- 生成模式：`{'enabled' if result['generation_enabled'] else 'disabled'}`",
        f"- Embedding 后端：`{result['environment']['embedding_backend']}`",
        f"- Chat 模型：`{result['environment']['chat_model']}`",
        "",
        "> 限制：34 题来自既有项目并已参与历史开发，仅分为 development 与 regression；当前没有独立 holdout，因此本报告不能证明生产效果。",
        "",
        "## Overall metrics",
        "",
        "### Retrieval",
        "",
        "| Recall@1 | Recall@3 | Recall@5 | MRR | Document hit | Chunk hit |",
        "|---:|---:|---:|---:|---:|---:|",
        "| {recall_at_1} | {recall_at_3} | {recall_at_5} | {reciprocal_rank} | {document_hit} | {chunk_hit} |".format(**overall["retrieval"]),
        "",
        "### Generation",
        "",
        "| Correctness* | Citation accuracy | Evidence consistency | Completeness | No-answer | Refusal |",
        "|---:|---:|---:|---:|---:|---:|",
        "| {answer_correctness} | {citation_accuracy} | {evidence_consistency} | {completeness} | {no_answer_accuracy} | {refusal_accuracy} |".format(**overall["generation"]),
        "",
        "*Correctness 与 completeness 是确定性事实字符串检查，不等价于人工语义评审；evidence consistency 保持 `null`，等待人工复核。*",
        "",
        "### System latency (mean ms)",
        "",
        "| Query embedding | Vector search | Context build | Generation | TTFT | Total |",
        "|---:|---:|---:|---:|---:|---:|",
        "| {query_embedding_ms} | {vector_search_ms} | {context_build_ms} | {generation_ms} | {ttft_ms} | {total_latency_ms} |".format(**overall["system"]),
        "",
        "TTFT 为 `null`：当前系统不是流式接口。Token 与成本为 `null/unavailable`：当前封装未暴露可靠 usage，且未配置经过确认的价格版本。",
        "",
        "## Split comparison",
        "",
        "| Split | Cases | Recall@3 | MRR | Correctness | Citation accuracy |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for split, values in summary["by_split"].items():
        lines.append(f"| {split} | {values['count']} | {values['retrieval']['recall_at_3']} | {values['retrieval']['reciprocal_rank']} | {values['generation']['answer_correctness']} | {values['generation']['citation_accuracy']} |")
    lines.extend(["", "## Initial bad cases", ""])
    for bad_case in result["bad_cases"]:
        lines.append(f"- Case {bad_case['case_id']}: `{', '.join(bad_case['reasons'])}`，路径 `{bad_case['execution_path']}`")
    lines.extend([
        "",
        "## Current-system boundaries",
        "",
        "- 实际向量存储是 SQLite + NumPy 全量余弦扫描，不是 ChromaDB、FAISS 或正式 Hybrid Search。",
        "- 特殊场景和域外问题在检索前短路，因此可以答对但没有证据引用；这会直接降低检索与引用指标。",
        "- 原 34 题的“命中任意关键词即满分”会高估正确性；本基线改为必需事实完整度，但仍需人工语义复核。",
        "- 当前非流式生成无法测量 TTFT，模型封装也未保留 token usage。",
        "- Milestone 2 应以本报告为对照实验基准，不得沿用历史宣传数字。",
        "",
        f"机器可读原始结果：`{result['result_file']}`",
        "",
    ])
    return "\n".join(lines)


def run_baseline(generate: bool, rebuild_index: bool) -> dict[str, Any]:
    cases = load_dataset()
    if not ZHIPU_API_KEY:
        raise RuntimeError("ZHIPU_API_KEY is required by the frozen current embedding implementation")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if rebuild_index:
        build = build_index(ZHIPU_API_KEY, force=True)
        if not build.get("ok"):
            raise RuntimeError(build.get("message", "index build failed"))
    else:
        try:
            if _get_collection(create=False).count() == 0:
                raise RuntimeError("index is empty; rerun with --rebuild-index")
        except Exception as exc:
            raise RuntimeError("current index is unavailable; rerun with --rebuild-index") from exc

    details = [run_case(case, ZHIPU_API_KEY, generate) for case in cases]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result_path = RESULTS_DIR / f"baseline_{timestamp}.json"
    result = {
        "strategy": STRATEGY,
        "dataset_version": DATASET_VERSION,
        "generation_enabled": generate,
        "environment": environment_snapshot(),
        "summary": summarize(details),
        "bad_cases": classify_bad_cases(details),
        "details": details,
        "result_file": str(result_path.relative_to(ROOT)).replace("\\", "/"),
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(render_report(result), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the frozen current-system baseline")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument("--rebuild-index", action="store_true")
    args = parser.parse_args()
    if args.validate_only:
        cases = load_dataset()
        print(f"validated {len(cases)} cases against {len(current_chunk_catalog())} chunks")
        return
    result = run_baseline(generate=not args.retrieval_only, rebuild_index=args.rebuild_index)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(f"report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
