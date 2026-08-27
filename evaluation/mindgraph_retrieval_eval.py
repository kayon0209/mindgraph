"""Deterministic, evidence-path based evaluation for the MindGraph golden set."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Any

try:
    from src.retrieval.types import RetrievalTrace
except ModuleNotFoundError:
    from retrieval.types import RetrievalTrace

DEFAULT_DATASET_PATH = Path(__file__).resolve().parent / "datasets" / "mindgraph_golden_v2.jsonl"
DEFAULT_CANDIDATE_DATASET_PATH = Path(__file__).resolve().parent / "datasets" / "mindgraph_candidates_v2.jsonl"
_REQUIRED_FIELDS = (
    "case_id",
    "question",
    "category",
    "split",
    "expected_behavior",
    "gold_vault_paths",
    "required_facts",
    "forbidden_facts",
    "dataset_version",
    "label_source",
)
_STAGES = ("dense_results", "sparse_results", "fused_results", "reranked_results", "final_selected_chunks")
_STAGE_ORDER = {"not_retrieved": 0, "retrieved_not_ranked": 1, "ranked_not_final": 2, "final": 3}
_APPROVED_STATUS = "approved"
_PENDING_STATUS = "pending"
_CANDIDATE_SOURCE = "generated_candidate"


def _jsonl_records(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    records: list[dict[str, Any]] = []
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
            records.append(value)
    return records


def _canonical_jsonl_bytes(cases: Iterable[dict[str, Any]]) -> bytes:
    normalized = [json.dumps(case, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for case in cases]
    payload = "\n".join(normalized)
    if payload:
        payload += "\n"
    return payload.encode("utf-8")


def dataset_sha256(path: str | Path) -> str:
    return sha256(_canonical_jsonl_bytes(_jsonl_records(path))).hexdigest()


def _validate_case_contract(cases: Iterable[dict[str, Any]], *, kind: str) -> list[dict[str, Any]]:
    if not isinstance(cases, list):
        cases = list(cases)
    if not cases:
        raise ValueError("dataset must contain at least one case")
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
        for field in ("question", "category", "dataset_version", "label_source"):
            if not isinstance(case[field], str) or not case[field].strip():
                raise ValueError(f"{location} {field} must be a non-empty string")
        version = case["dataset_version"]
        if dataset_version is None:
            dataset_version = version
        elif version != dataset_version:
            raise ValueError(f"{location} dataset_version must be consistent with all cases (expected {dataset_version!r})")
        split = case["split"]
        if not isinstance(split, str) or split not in {"development", "regression"}:
            raise ValueError(f"{location} split must be 'development' or 'regression'")
        behavior = case["expected_behavior"]
        if not isinstance(behavior, str) or behavior not in {"answer", "abstain"}:
            raise ValueError(f"{location} expected_behavior must be 'answer' or 'abstain'")
        paths = case["gold_vault_paths"]
        if not isinstance(paths, list) or any(not isinstance(path, str) or not path.strip() for path in paths):
            raise ValueError(f"{location} gold_vault_paths must be a list of non-empty strings")
        if len(set(paths)) != len(paths):
            raise ValueError(f"{location} gold_vault_paths must not contain duplicates")
        for field in ("required_facts", "forbidden_facts"):
            facts = case[field]
            if not isinstance(facts, list) or any(not isinstance(fact, str) or not fact.strip() for fact in facts):
                raise ValueError(f"{location} {field} must be a list of non-empty strings")
        if behavior == "answer" and not paths:
            raise ValueError(f"{location} answer case requires at least one gold_vault_paths entry")
        if behavior == "answer" and not case["required_facts"]:
            raise ValueError(f"{location} answer case requires at least one required_facts entry")
        if behavior == "abstain" and paths:
            raise ValueError(f"{location} abstain case must not contain gold_vault_paths")

        validation_status = case.get("validation_status")
        if validation_status is not None:
            if not isinstance(validation_status, str) or not validation_status.strip():
                raise ValueError(f"{location} validation_status must be a non-empty string when present")
            if kind == "golden" and validation_status != _APPROVED_STATUS:
                raise ValueError(f"{location} golden cases must be approved")
            if kind == "candidate" and validation_status != _PENDING_STATUS:
                raise ValueError(f"{location} candidate cases must remain pending until human review")
        elif kind == "candidate":
            raise ValueError(f"{location} candidate cases require validation_status='pending'")

        source = case.get("source")
        if source is not None:
            if not isinstance(source, str) or not source.strip():
                raise ValueError(f"{location} source must be a non-empty string when present")
            if kind == "candidate" and source != _CANDIDATE_SOURCE:
                raise ValueError(f"{location} candidate cases must use source='{_CANDIDATE_SOURCE}'")
        elif kind == "candidate":
            raise ValueError(f"{location} candidate cases require source='{_CANDIDATE_SOURCE}'")

        query_type = case.get("query_type")
        if kind == "candidate":
            if not isinstance(query_type, str) or not query_type.strip():
                raise ValueError(f"{location} candidate cases require a non-empty query_type")

        graph_needed = case.get("graph_needed")
        expected_relations = case.get("expected_relations")
        if kind == "golden" and case.get("validation_status") == "approved" and graph_needed:
            if not isinstance(expected_relations, list) or not expected_relations:
                raise ValueError(f"{location} approved graph_needed cases require expected_relations")
        if kind == "candidate" and graph_needed:
            if not isinstance(expected_relations, list) or not expected_relations:
                raise ValueError(f"{location} graph_needed cases require expected_relations")
            for rel_index, relation in enumerate(expected_relations, 1):
                if not isinstance(relation, dict):
                    raise ValueError(f"{location} expected_relations[{rel_index}] must be an object")
                for rel_field in ("source_path", "target_path", "relation_type"):
                    value = relation.get(rel_field)
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError(f"{location} expected_relations[{rel_index}].{rel_field} must be a non-empty string")
        elif expected_relations is not None and not isinstance(expected_relations, list):
            raise ValueError(f"{location} expected_relations must be a list when present")

        acl_context = case.get("acl_context")
        if acl_context is not None and not isinstance(acl_context, dict):
            raise ValueError(f"{location} acl_context must be an object when present")

        if kind == "candidate" and case.get("expected_route") is not None:
            if not isinstance(case["expected_route"], str) or not case["expected_route"].strip():
                raise ValueError(f"{location} expected_route must be a non-empty string when present")

        validated.append(case)
    return validated


def validate_golden_cases(cases: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return _validate_case_contract(cases, kind="golden")


def validate_candidate_cases(cases: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return _validate_case_contract(cases, kind="candidate")


def load_golden_dataset(path: str | Path = DEFAULT_DATASET_PATH) -> list[dict[str, Any]]:
    """Load JSONL golden cases and report malformed lines with line numbers."""
    cases = _jsonl_records(path)
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


def load_candidate_dataset(path: str | Path = DEFAULT_CANDIDATE_DATASET_PATH) -> list[dict[str, Any]]:
    cases = _jsonl_records(path)
    try:
        validated = validate_candidate_cases(cases)
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


def _stage_for_path(gold_path: str, stage_paths: dict[str, list[str]]) -> str:
    if gold_path in stage_paths["final_selected_chunks"]:
        return "final"
    if any(gold_path in stage_paths[name] for name in ("fused_results", "reranked_results")):
        return "ranked_not_final"
    if any(gold_path in stage_paths[name] for name in ("dense_results", "sparse_results")):
        return "retrieved_not_ranked"
    return "not_retrieved"


def _metrics(gold: set[str], final_paths: list[str], top_k: int) -> dict[str, float]:
    selected = final_paths[:top_k]
    hits = len(gold.intersection(selected))
    first = next((position for position, path in enumerate(final_paths, 1) if path in gold), None)
    relevance = [1 if path in gold else 0 for path in selected]
    dcg = sum(value / math.log2(index + 2) for index, value in enumerate(relevance))
    ideal = sum(1 / math.log2(index + 2) for index in range(min(len(gold), top_k)))
    return {
        "recall_at_k": round(hits / len(gold), 4),
        "precision_at_k": round(hits / top_k, 4),
        "mrr": round(1.0 / first, 4) if first else 0.0,
        "ndcg_at_k": round(dcg / ideal, 4) if ideal else 0.0,
    }


def evaluate_retrieval_cases(
    cases: list[dict[str, Any]], retrieve: Callable[[dict[str, Any]], RetrievalTrace], *,
    top_k: int = 5, include_questions: bool = False,
) -> dict[str, Any]:
    """Evaluate traces without invoking retrieval, models, network, or persistence."""
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise ValueError("top_k must be a positive integer")
    cases = validate_golden_cases(cases)
    details: list[dict[str, Any]] = []
    scored: list[dict[str, Any]] = []
    counts = {"answer": 0, "abstain": 0}
    graph_enabled_cases = 0
    graph_activated_cases = 0
    graph_expanded_candidates = 0
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
        graph_enabled = bool(trace_value.graph_enabled)
        expanded_candidates = int(trace_value.candidate_counts.get("graph_expanded", 0) or 0)
        relation_ids = sorted({
            relation_id
            for link in trace_value.graph_links
            if isinstance(link, dict) and isinstance((relation_id := link.get("relation_id")), str)
        })
        detail["graph"] = {
            "enabled": graph_enabled,
            "hops": trace_value.graph_hops,
            "expanded_candidates": expanded_candidates,
            "relation_ids": relation_ids,
        }
        if graph_enabled:
            graph_enabled_cases += 1
            graph_expanded_candidates += expanded_candidates
            if expanded_candidates > 0 or relation_ids:
                graph_activated_cases += 1
        stage_paths = {name: _paths(getattr(trace_value, name)) for name in _STAGES}
        gold_paths = case["gold_vault_paths"]
        gold = set(gold_paths)
        final_paths = stage_paths["final_selected_chunks"]
        metrics = _metrics(gold, final_paths, top_k)
        evidence_stages = {path: _stage_for_path(path, stage_paths) for path in gold_paths}
        detail.update({"gold_vault_paths": gold_paths, "evidence_stages": evidence_stages, "metrics": metrics})
        missing_paths = gold.difference(final_paths[:top_k])
        if missing_paths:
            loss_stages = [
                "ranked_not_final" if evidence_stages[path] == "final" else evidence_stages[path]
                for path in missing_paths
            ]
            detail["failure_stage"] = min(loss_stages, key=_STAGE_ORDER.__getitem__)
        scored.append(detail)
        details.append(detail)
    summary = {
        name: round(sum(row["metrics"][name] for row in scored) / len(scored), 4) if scored else None
        for name in ("recall_at_k", "precision_at_k", "mrr", "ndcg_at_k")
    }
    failures = [row for row in scored if row["metrics"]["recall_at_k"] < 1.0]
    graph_limitations: list[str] = []
    if graph_enabled_cases == 0:
        graph_limitations.append("graph_not_enabled")
    elif graph_activated_cases == 0:
        graph_limitations.append("graph_enabled_but_no_expansion_observed")
    return {
        "evaluator_version": "mindgraph-retrieval-v2",
        "dataset_version": cases[0].get("dataset_version") if cases else None,
        "top_k": top_k,
        "counts": counts,
        "summary": summary,
        "graph_diagnostics": {
            "enabled_cases": graph_enabled_cases,
            "activated_cases": graph_activated_cases,
            "expanded_candidates": graph_expanded_candidates,
            "activation_rate": round(graph_activated_cases / graph_enabled_cases, 4) if graph_enabled_cases else 0.0,
            "comparable_for_graph_gain": graph_enabled_cases > 0 and graph_activated_cases > 0,
            "limitations": graph_limitations,
        },
        "details": details,
        "failed_cases": failures,
    }
