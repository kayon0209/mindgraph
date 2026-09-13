"""Deterministic, evidence-path based evaluation for the MindGraph golden set."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

# ── 模块身份归一：权威导入侧固定为生产侧 `retrieval.types` ──────────────────
# `src/` 下没有 `__init__.py`，而 pytest.ini / 各脚本会把项目根与 `src/` 同时
# 放进 sys.path，于是同一个物理文件 `src/retrieval/types.py` 会被注册成两个
# 互不相认的模块对象：
#   * `retrieval.types`     —— src/** 生产代码实际使用的名字（31 处）
#   * `src.retrieval.types` —— 命名空间包路径下的别名
# 两个类对象会让 `isinstance(trace_value, RetrievalTrace)` 恒为 False，评测静默
# 失效（历史上靠调用方 monkey-patch 绕过）。这里做两件事：
#   1) 把权威侧固定为生产侧，并把别名**确定性地**写回 sys.modules（不用
#      setdefault：别名必须覆盖，否则别的模块先导入 src.retrieval.types 时失效）；
#   2) 判定仍保留一道按「类名 + 定义文件」的身份兜底 —— sys.modules 覆盖无法回溯
#      已经绑定过的名字，兜底保证结构正确的 trace 永远不会被静默拒绝。
_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

import retrieval.types as _retrieval_types  # noqa: E402

sys.modules["src.retrieval"] = sys.modules["retrieval"]
sys.modules["src.retrieval.types"] = _retrieval_types

from retrieval.types import RetrievalTrace  # noqa: E402

DEFAULT_DATASET_PATH = Path(__file__).resolve().parent / "datasets" / "mindgraph_golden_v2.jsonl"
DEFAULT_CANDIDATE_DATASET_PATH = Path(__file__).resolve().parent / "datasets" / "mindgraph_candidates_v2.jsonl"
GOLDEN_SCHEMA_PATH = Path(__file__).resolve().parent / "datasets" / "mindgraph_golden_v2.schema.json"
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
    "query_type",
    "difficulty",
    "expected_route",
    "graph_needed",
    "acl_context",
    "source",
    "validation_status",
    "notes",
)
_STAGES = ("dense_results", "sparse_results", "fused_results", "reranked_results", "final_selected_chunks")
_STAGE_ORDER = {"not_retrieved": 0, "retrieved_not_ranked": 1, "ranked_not_final": 2, "final": 3}
_APPROVED_STATUS = "approved"
_PENDING_STATUS = "pending"
_CANDIDATE_SOURCE = "generated_candidate"


def _golden_schema_validator() -> Draft202012Validator:
    schema = json.loads(GOLDEN_SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


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
    validated = _validate_case_contract(cases, kind="golden")
    validator = _golden_schema_validator()
    for index, case in enumerate(validated, 1):
        public_case = {key: value for key, value in case.items() if not key.startswith("_")}
        error = next(iter(validator.iter_errors(public_case)), None)
        if error is not None:
            field = ".".join(str(part) for part in error.absolute_path) or "record"
            raise ValueError(
                f"case_id {case['case_id']!r} (index {index}) violates golden schema at {field}: {error.message}"
            )
    return validated


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


def _is_graph_candidate(item: Any) -> bool:
    """候选是否由图扩展追加（mindgraph_pipeline 在 chunk 元数据打 graph_evidence 标记）。"""
    return bool(getattr(getattr(item, "chunk", None), "metadata", {}).get("graph_evidence"))


def _dedupe_by_chunk_id(items: Iterable[Any]) -> list[Any]:
    """按稳定 chunk_id 去重并保持顺序（图扩展按 chunk_id 判重，防御重复追加）。"""
    seen: set[str] = set()
    result: list[Any] = []
    for item in items:
        chunk_id = getattr(getattr(item, "chunk", None), "chunk_id", None)
        if isinstance(chunk_id, str) and chunk_id in seen:
            continue
        if isinstance(chunk_id, str):
            seen.add(chunk_id)
        result.append(item)
    return result


def _split_base_graph(final_chunks: list[Any]) -> tuple[list[Any], list[Any]]:
    """把 final_selected_chunks 拆成「基础检索命中」与「图扩展追加」两组。"""
    base: list[Any] = []
    graph: list[Any] = []
    for candidate in final_chunks:
        (graph if _is_graph_candidate(candidate) else base).append(candidate)
    return base, graph


def _full_set_metrics(gold: set[str], paths: list[str]) -> dict[str, float]:
    """对「基础 top_k + 图追加」的完整证据集计算指标（不做 top_k 截断）。

    ``evidence_size`` 反映实际进入生成上下文的证据规模（可大于 top_k）；
    ``recall`` 是完整证据集上命中的 gold 比例，专门用于观察图扩展在 top_k
    之外追加的召回增益——旧口径只数 final[:top_k]，对追加式图证据天然失明。
    """
    hits = len(gold.intersection(paths))
    first = next((position for position, path in enumerate(paths, 1) if path in gold), None)
    relevance = [1 if path in gold else 0 for path in paths]
    dcg = sum(value / math.log2(index + 2) for index, value in enumerate(relevance))
    ideal = sum(1 / math.log2(index + 2) for index in range(min(len(gold), len(paths))))
    return {
        "recall": round(hits / len(gold), 4) if gold else 0.0,
        "precision": round(hits / len(paths), 4) if paths else 0.0,
        "mrr": round(1.0 / first, 4) if first else 0.0,
        "ndcg": round(dcg / ideal, 4) if ideal else 0.0,
        "evidence_size": len(paths),
    }


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


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


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 4)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 4)


def _stratified_metrics(
    cases: list[dict[str, Any]],
    scored: list[dict[str, Any]],
    dimensions: tuple[str, ...] = ("query_type", "difficulty", "graph_needed"),
) -> dict[str, dict[str, dict[str, Any]]]:
    """计划 2.2：按 query_type/difficulty/graph_needed 输出分层检索指标。

    只统计 answer 用例（与总体指标同分母口径）；每组给出样本量与均值，
    样本量过小（<3）的组原样输出样本量便于读者判断统计意义。
    """
    metrics_by_case = {row["case_id"]: row["metrics"] for row in scored}
    behavior_by_case = {row["case_id"]: row.get("expected_behavior") for row in scored}
    stratified: dict[str, dict[str, dict[str, Any]]] = {}
    for dimension in dimensions:
        groups: dict[str, list[dict[str, Any]]] = {}
        for case in cases:
            if behavior_by_case.get(case["case_id"]) != "answer":
                continue
            raw_value = case.get(dimension)
            key = "unset" if raw_value is None else str(raw_value)
            groups.setdefault(key, []).append(case)
        summary: dict[str, dict[str, Any]] = {}
        for key, rows in sorted(groups.items()):
            group_metrics = [metrics_by_case[row["case_id"]] for row in rows if row["case_id"] in metrics_by_case]
            if not group_metrics:
                continue
            summary[key] = {
                "sample_size": len(group_metrics),
                **{
                    name: round(
                        sum(float(row[name]) for row in group_metrics) / len(group_metrics), 4
                    )
                    for name in ("recall_at_k", "precision_at_k", "mrr", "ndcg_at_k")
                },
            }
        stratified[dimension] = summary
    return stratified


def _defining_file(obj: Any) -> str | None:
    """某个类/对象所在模块的源文件路径；模块不在 sys.modules 时返回 None。"""
    module = sys.modules.get(getattr(obj, "__module__", ""))
    file_path = getattr(module, "__file__", None)
    return str(Path(file_path).resolve()) if file_path else None


def is_retrieval_trace(value: Any) -> bool:
    """判断 value 是否为 RetrievalTrace，且不受导入路径别名影响。

    首选 ``isinstance``。``src/`` 无 ``__init__.py`` 时同一文件可能被注册成
    ``retrieval.types`` 与 ``src.retrieval.types`` 两个模块对象，此时 isinstance
    恒为 False；按「类名 + 定义文件」兜底判定，避免结构正确的 trace 被静默拒绝。
    """
    if isinstance(value, RetrievalTrace):
        return True
    cls = type(value)
    if cls.__name__ != RetrievalTrace.__name__:
        return False
    return _defining_file(cls) is not None and _defining_file(cls) == _defining_file(RetrievalTrace)


def evaluate_retrieval_cases(
    cases: list[dict[str, Any]], retrieve: Callable[[dict[str, Any]], RetrievalTrace], *,
    top_k: int = 5, include_questions: bool = False, dataset_digest: str | None = None,
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
    total_latencies_ms: list[float] = []
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
        if not is_retrieval_trace(trace_value):
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
        total_latency = trace_value.latency_ms.get("total_retrieval_ms")
        if isinstance(total_latency, int | float) and not isinstance(total_latency, bool):
            total_latencies_ms.append(float(total_latency))
            detail["total_retrieval_ms"] = round(float(total_latency), 4)
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
        # 图扩展观测：完整证据集指标（不做 top_k 截断，专门反映追加式图证据的
        # 召回增益）+ 基础/图追加拆分（先按 chunk_id 去重，防御重复追加）。
        deduped_final = _dedupe_by_chunk_id(trace_value.final_selected_chunks)
        base_candidates, graph_candidates = _split_base_graph(deduped_final)
        detail["evidence_graph_split"] = {"base": len(base_candidates), "graph": len(graph_candidates)}
        detail["evidence_full_set"] = _full_set_metrics(gold, final_paths)
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
    # 完整证据集均值：top_k 截断口径的补充观测（图扩展在 top_k 之外追加的
    # 证据可以提升 full_set_recall 而不改变 recall_at_k）。
    summary["full_set_recall"] = _mean([float(row["evidence_full_set"]["recall"]) for row in scored])
    summary["mean_evidence_size"] = _mean([float(row["evidence_full_set"]["evidence_size"]) for row in scored])
    summary["p50_retrieval_ms"] = _percentile(total_latencies_ms, 0.50)
    summary["p95_retrieval_ms"] = _percentile(total_latencies_ms, 0.95)
    summary["stratified"] = _stratified_metrics(cases, scored)
    failures = [row for row in scored if row["metrics"]["recall_at_k"] < 1.0]
    graph_limitations: list[str] = []
    if graph_enabled_cases == 0:
        graph_limitations.append("graph_not_enabled")
    elif graph_activated_cases == 0:
        graph_limitations.append("graph_enabled_but_no_expansion_observed")
    return {
        "evaluator_version": "mindgraph-retrieval-v2",
        "dataset_version": cases[0].get("dataset_version") if cases else None,
        "dataset_sha256": dataset_digest,
        "sample_size": len(cases),
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
