"""Deterministic, evidence-path based evaluation for the MindGraph golden set."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import json
from pathlib import Path
import re
from typing import Any

from src.retrieval.types import RetrievalTrace

DEFAULT_DATASET_PATH = Path(__file__).resolve().parent / "datasets" / "mindgraph_golden.jsonl"
_REQUIRED_FIELDS = ("case_id", "question", "expected_behavior", "gold_vault_paths", "dataset_version")
_STAGES = ("dense_results", "sparse_results", "fused_results", "reranked_results", "final_selected_chunks")
_STAGE_ORDER = {"not_retrieved": 0, "retrieved_not_ranked": 1, "ranked_not_final": 2, "final": 3}


def validate_golden_cases(cases: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate and return golden cases, with identifiers in every error."""
    if not isinstance(cases, list):
        cases = list(cases)
    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    dataset_version: str | None = None
    for index, case in enumerate(cases, 1):
        location = f"case at index {index}"
        if not isinstance(case, dict):
            raise ValueError(f"{location} must be a JSON object")
        case_id = case.get("case_id", f"index {index}")
        location = f"case_id {case_id!r} (index {index})"
        missing = [field for field in _REQUIRED_FIELDS if field not in case]
        if missing:
            raise ValueError(f"{location} missing required field(s): {', '.join(missing)}")
        if not isinstance(case["case_id"], str) or not case["case_id"].strip():
            raise ValueError(f"{location} case_id must be a non-empty string")
        if case["case_id"] in seen:
            raise ValueError(f"{location} duplicate case_id")
        seen.add(case["case_id"])
        if not isinstance(case["question"], str):
            raise ValueError(f"{location} question must be a string")
        version = case["dataset_version"]
        if not isinstance(version, str) or not version.strip():
            raise ValueError(f"{location} dataset_version must be a non-empty string")
        if dataset_version is None:
            dataset_version = version
        elif version != dataset_version:
            raise ValueError(f"{location} dataset_version must be consistent with all cases (expected {dataset_version!r})")
        behavior = case["expected_behavior"]
        if behavior not in {"answer", "abstain"}:
            raise ValueError(f"{location} expected_behavior must be 'answer' or 'abstain'")
        paths = case["gold_vault_paths"]
        if not isinstance(paths, list) or any(not isinstance(path, str) or not path for path in paths):
            raise ValueError(f"{location} gold_vault_paths must be a list of non-empty strings")
        if len(set(paths)) != len(paths):
            raise ValueError(f"{location} gold_vault_paths must not contain duplicates")
        if behavior == "answer" and not paths:
            raise ValueError(f"{location} answer case requires at least one gold_vault_paths entry")
        if behavior == "abstain" and paths:
            raise ValueError(f"{location} abstain case must not contain gold_vault_paths")
        validated.append(case)
    return validated


def load_golden_dataset(path: str | Path = DEFAULT_DATASET_PATH) -> list[dict[str, Any]]:
    """Load JSONL golden cases and report malformed lines with line numbers."""
    source = Path(path)
    cases: list[dict[str, Any]] = []
    with source.open(encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            if not raw_line.strip():
                continue
            try:
                value = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"line {line_number}: invalid JSON ({exc.msg})") from exc
            if not isinstance(value, dict):
                raise ValueError(f"line {line_number}: expected a JSON object")
            value["_source_line"] = line_number
            cases.append(value)
    try:
        validated = validate_golden_cases(cases)
    except ValueError as exc:
        message = str(exc)
        match = re.search(r"index (\d+)", message)
        if match:
            index = int(match.group(1))
            line = next((c.get("_source_line") for c in cases[index - 1:index]), None)
            message = f"line {line}: {message}"
        raise ValueError(message) from exc
    for case in validated:
        case.pop("_source_line", None)
    return validated


def _paths(items: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for item in items:
        path = getattr(getattr(item, "chunk", None), "metadata", {}).get("vault_path")
        if isinstance(path, str) and path not in result:
            result.append(path)
    return result


def _stage_for(gold: set[str], stage_paths: dict[str, list[str]]) -> str:
    if any(gold.intersection(stage_paths[name]) for name in ("final_selected_chunks",)):
        return "final"
    if any(gold.intersection(stage_paths[name]) for name in ("fused_results", "reranked_results")):
        return "ranked_not_final"
    if any(gold.intersection(stage_paths[name]) for name in ("dense_results", "sparse_results")):
        return "retrieved_not_ranked"
    return "not_retrieved"


def _metrics(gold: set[str], final_paths: list[str], top_k: int) -> dict[str, float]:
    selected = final_paths[:top_k]
    hits = len(gold.intersection(selected))
    first = next((position for position, path in enumerate(selected, 1) if path in gold), None)
    return {
        "recall_at_k": round(hits / len(gold), 4),
        "precision_at_k": round(hits / len(selected), 4) if selected else 0.0,
        "mrr": round(1.0 / first, 4) if first else 0.0,
    }


def evaluate_retrieval_cases(
    cases: list[dict[str, Any]], retrieve: Callable[[dict[str, Any]], RetrievalTrace], *,
    top_k: int = 5, include_questions: bool = False,
) -> dict[str, Any]:
    """Evaluate traces without invoking retrieval, models, network, or persistence."""
    if top_k < 1:
        raise ValueError("top_k must be >= 1")
    cases = validate_golden_cases(cases)
    details: list[dict[str, Any]] = []
    scored: list[dict[str, Any]] = []
    counts = {"answer": 0, "abstain": 0}
    for case in cases:
        behavior = case["expected_behavior"]
        counts[behavior] += 1
        detail: dict[str, Any] = {"case_id": case["case_id"], "expected_behavior": behavior, "scored": behavior == "answer"}
        if include_questions:
            detail["question"] = case["question"]
        if behavior == "abstain":
            detail["reason"] = "abstain cases are excluded from retrieval metrics"
            details.append(detail)
            continue
        try:
            trace_value = retrieve(case)
        except Exception as exc:
            raise RuntimeError(f"case_id {case['case_id']!r}: retrieval failed") from exc
        if not isinstance(trace_value, RetrievalTrace):
            raise TypeError(f"case_id {case['case_id']!r}: retrieve must return RetrievalTrace")
        stage_paths = {name: _paths(getattr(trace_value, name)) for name in _STAGES}
        metrics = _metrics(set(case["gold_vault_paths"]), stage_paths["final_selected_chunks"], top_k)
        detail.update({"gold_vault_paths": case["gold_vault_paths"], "evidence_stage": _stage_for(set(case["gold_vault_paths"]), stage_paths), "metrics": metrics})
        scored.append(detail)
        details.append(detail)
    summary = {name: round(sum(row["metrics"][name] for row in scored) / len(scored), 4) if scored else None for name in ("recall_at_k", "precision_at_k", "mrr")}
    failures = [row for row in scored if row["metrics"]["recall_at_k"] < 1.0]
    return {"evaluator_version": "mindgraph-retrieval-v1", "dataset_version": cases[0].get("dataset_version") if cases else None, "top_k": top_k, "counts": counts, "summary": summary, "details": details, "failed_cases": failures}
