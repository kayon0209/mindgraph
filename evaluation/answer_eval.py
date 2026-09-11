"""Deterministic answer-level trust evaluation for MindGraph Golden cases.

口径版本 **deterministic-answer-v2**（2026-09-11）相对 v1 的两处修复，都是为了
让指标名与其真实含义一致，而不是让分数好看——v1 的数值由 ``citation_offered_f1``
完整保留，可与历史结果直接对照：

1. **引用正确性改为以「正文实际引用的证据」为基准**（v1 用候选证据集合）。
   v1 的口径下，系统固定返回检索 top-k（实测均值 5.11 条）而 Gold 通常只有 1 条，
   precision 被结构性稀释到 ≈0.35；而同一批数据里 Gold 文档命中率其实是 92%。
   新增 ``citation_precision`` / ``citation_recall`` 让丢掉的维度可见。
2. **事实匹配全链路归一化**（NFKC + 去 markdown 标记 + 去空白）。v1 用裸子串
   ``fact in answer``，答案写 ``**800 元**`` 就匹配不上 Gold 的 ``800元``。
3. **「检索到但未被引用」不再判为引用标注缺陷**。运行时契约里 ``citations`` 是
   提供给模型的候选证据，提示词只要求"使用 [citation-N] 标注引用来源"。该现象
   改由 ``citation_usage_ratio`` 量化记录。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import date
from evaluation.conflict_attribution import conflict_expectation
import math
from statistics import fmean
from typing import Any
import unicodedata


REFUSAL_STATES = {"insufficient_evidence", "out_of_scope", "conflicting_evidence"}
ANSWER_METRICS = (
    "citation_correctness",
    "citation_precision",
    "citation_recall",
    "citation_offered_f1",
    "citation_usage_ratio",
    "refusal_correctness",
    "version_validity",
    "citation_fidelity",
    "citation_marker_validity",
    "required_fact_coverage",
    "forbidden_fact_avoidance",
    "acl_leakage",
    "conflict_accuracy",
)
INACTIVE_POLICY_STATUSES = {"archived", "expired", "superseded", "replaced"}
# markdown 强调/代码标记：真实模型输出里普遍包在 **加粗** 或 `反引号` 内，
# 裸子串比对会因为这两个字符判"缺失"（详见 _normalize_for_match）。
_MARKDOWN_MARKS = str.maketrans("", "", "*_`~")


def _is_conflict_case(case: dict[str, Any]) -> bool:
    return (case.get("category") or case.get("query_type")) == "conflict"


def _normalize_for_match(text: str) -> str:
    """把文本归一化到"只比较语义字符"的形态，供确定性事实匹配使用。

    P1 口径修复：答案由真实模型生成，常写成 ``**30 个自然日**``（加粗 + 空格），
    而 Gold 里的事实是 ``30个自然日``；裸 ``fact in answer`` 会把它判成缺失，
    这是评测口径缺陷而不是模型答错。这里做三件事：
    1. NFKC 统一全/半角（``８００元`` → ``800元``）；
    2. 去掉 markdown 强调与代码标记（``*`` ``_`` ``` ``~``）；
    3. 去掉全部空白（含全角空格，交给 :meth:`str.split` 判定）。
    """
    normalized = unicodedata.normalize("NFKC", text or "")
    return "".join(normalized.translate(_MARKDOWN_MARKS).split())


def _citation_f1(expected_paths: set[str], actual_paths: set[str]) -> float:
    if not expected_paths or not actual_paths:
        return 0.0
    overlap = len(expected_paths & actual_paths)
    precision = overlap / len(actual_paths)
    recall = overlap / len(expected_paths)
    return 2 * precision * recall / (precision + recall) if overlap else 0.0


def _citation_ranks(citations: list[dict[str, Any]]) -> set[int]:
    """从评测预测的 citation 列表还原 final_rank 序号集合。

    MindGraph 的 citation_id 形如 citation-N（N=final_rank）；两者都缺失时
    视为序号 0（永远不在 [citation-N] 的合法取值内，即引用不可命中）。
    """
    ranks: set[int] = set()
    for item in citations:
        rank = item.get("final_rank")
        if isinstance(rank, int) and rank and rank > 0:
            ranks.add(rank)
            continue
        citation_id = item.get("citation_id")
        if isinstance(citation_id, str) and citation_id.startswith("citation-"):
            suffix = citation_id[len("citation-"):]
            if suffix.isdigit():
                ranks.add(int(suffix))
    return ranks


def _cited_ranks(prediction: dict[str, Any]) -> set[int]:
    """答案正文**实际引用**的证据序号集合（P0 契约澄清的落点）。

    优先使用运行时写入的 ``cited_citation_ids``（由 chat_service 在落库前统一
    派生）；历史预测文件没有该字段时，退化为「答案里的 [citation-N] 标注 ∩
    引用列表序号」——两者在语义上等价，因此 v1 的预测文件也能按 v2 口径重算，
    不需要重新调用模型。

    注意：``prediction["citations"]`` 是**候选证据**（检索 top-k），不是引用。
    """
    from application.evidence_fidelity import extract_citation_marks

    citations = prediction.get("citations") or []
    available = _citation_ranks(citations)
    declared = prediction.get("cited_citation_ids")
    if isinstance(declared, list) and declared:
        declared_ids = {item for item in declared if isinstance(item, str)}
        declared_ranks = {rank for rank in available if f"citation-{rank}" in declared_ids}
        declared_ranks |= {
            item.get("final_rank")
            for item in citations
            if isinstance(item.get("citation_id"), str)
            and item.get("citation_id") in declared_ids
            and isinstance(item.get("final_rank"), int)
        }
        if declared_ranks:
            return declared_ranks & available if available else declared_ranks
    return set(extract_citation_marks(str(prediction.get("answer") or ""))) & available


def _citation_fidelity(answer: str, citations: list[dict[str, Any]]) -> float | None:
    """确定性引用一致性：答案中的 [citation-N] 必须全部命中实际引用集。

    与运行时 evidence_fidelity.check_citation_fidelity 语义一致（M0，warning-first）。
    无引用且无标注 → None（不可判定，不计入聚合分母）。
    """
    from application.evidence_fidelity import check_citation_fidelity

    report = check_citation_fidelity(answer, _citation_ranks(citations))
    if not report.applicable:
        return None
    return float(report.ok)


def _citation_marker_report(answer: str, citations: list[dict[str, Any]]):
    """构造引用标注完整性报告（P0：关闭「必须用尽候选证据」这道门）。

    见 :func:`_citation_marker_validity` 的说明。单独暴露报告是为了让失败归因
    能落到具体原因（畸形/越界/重复），而不是一律报 ``citation_marker_integrity``。
    """
    from application.citation_integrity import CitationIntegrityValidator

    ranks = _citation_ranks(citations)
    citation_ids = {item.get("citation_id") for item in citations if isinstance(item.get("citation_id"), str)}
    validator = CitationIntegrityValidator(
        citation_ids=citation_ids,
        citation_ranks=ranks,
        require_all_citations_used=False,
    )
    return validator.validate(answer)


def _citation_marker_failure_code(report: Any) -> str:
    """把标注完整性的失败原因映射成可读的失败码。"""
    if report.malformed_markers:
        return "citation_marker_malformed"
    if report.unknown_markers:
        return "citation_marker_unknown"
    if report.duplicate_markers:
        return "citation_marker_duplicate"
    return "citation_marker_integrity"


def _citation_marker_validity(answer: str, citations: list[dict[str, Any]]) -> float | None:
    """引用标注完整性：格式合法、无越界、无重复。

    与 ``citation_fidelity`` 互补：后者只检查「标注→引用集合」单向缺失；
    此函数检查更严格的完整视图（含畸形、重复）。

    P0 口径修复：**不再把「检索到但未被引用」判为失败**。运行时契约里
    ``citations`` 是提供给模型的候选证据，系统提示词只要求「使用 [citation-N]
    标注引用来源」，从未要求每条候选都被引用（实测 90 条里 79 条存在这种候选
    未被引用的情况，属正常现象而非模型缺陷）。未使用数量本身仍可审计——
    由 ``citation_usage_ratio`` 指标单独承载。

    无引用且无标注 → None（不可判定，不计入聚合分母）。
    """
    report = _citation_marker_report(answer, citations)
    if not report.applicable:
        return None
    return float(report.passed)


def _is_version_valid(citation: dict[str, Any], case: dict[str, Any]) -> bool:
    vault_path = citation.get("vault_path")
    status = str(citation.get("policy_status") or "").lower()
    effective_from = citation.get("effective_from")
    evaluation_date = case.get("evaluation_date")
    if not status or not effective_from or not evaluation_date:
        return False

    try:
        target = date.fromisoformat(evaluation_date)
        starts = date.fromisoformat(effective_from)
        ends = date.fromisoformat(citation["effective_to"]) if citation.get("effective_to") else None
    except (TypeError, ValueError):
        return False

    if vault_path in set(case.get("historical_vault_paths", [])):
        return status in INACTIVE_POLICY_STATUSES and ends is not None and starts <= ends < target
    if status in INACTIVE_POLICY_STATUSES:
        return False
    return status == "active" and starts <= target and (ends is None or target <= ends)


def _fact_coverage(answer: str, facts: list[str]) -> float:
    if not facts:
        return 1.0
    haystack = _normalize_for_match(answer)
    return sum(_normalize_for_match(fact) in haystack for fact in facts) / len(facts)


def _fact_avoidance(answer: str, facts: list[str]) -> float:
    """被禁事实规避率：出现任一被禁事实即 0。

    与 :func:`_fact_coverage` 共用同一套归一化。对安全类判定宁可更敏感：
    归一化只会让"本该被拦下"的表达更容易命中，不会放过它们。
    """
    if not facts:
        return 1.0
    haystack = _normalize_for_match(answer)
    return 0.0 if any(_normalize_for_match(fact) in haystack for fact in facts) else 1.0


def evaluate_answer_case(case: dict[str, Any], prediction: dict[str, Any]) -> dict[str, Any]:
    """Score one answer without using an LLM judge or runtime relation labels."""
    failures: list[str] = []
    expected_behavior = case["expected_behavior"]
    result_state = prediction.get("result_state")
    if expected_behavior == "abstain":
        refusal_correctness = float(result_state in REFUSAL_STATES)
    else:
        refusal_correctness = float(result_state == "answered")
    if not refusal_correctness:
        failures.append("expected_abstention" if expected_behavior == "abstain" else "unexpected_refusal")

    acl_context = case.get("acl_context") or {}
    acl_restricted = bool(acl_context.get("denied_resources") or acl_context.get("denied_actions"))
    # acl_leakage 的确定性子集：受限案例中，引用路径命中被拒资源特征
    # （denied_resources 是能力/资源名，如 finance_approvals；与 vault 路径
    # 的 snake_case 特征串匹配）。"拒答但附带可见的普通制度引用"不是泄漏
    # ——引用的内容调用主体有权查看；核心失败由 refusal_correctness 承载
    # （应 abstain 却 answered 在那里计分）。
    acl_leakage = 0.0
    if acl_restricted:
        denied_tokens = [
            str(resource).strip().lower().replace("-", "_")
            for resource in (acl_context.get("denied_resources") or [])
            if str(resource).strip()
        ]
        cited_paths = " ".join(str(item.get("vault_path") or "") for item in (prediction.get("citations") or []))
        normalized_cited_paths = cited_paths.lower().replace("-", "_")
        acl_leakage = float(any(token in normalized_cited_paths for token in denied_tokens))
    # 冲突判分契约（PR-05 拍板 A+B）：类别由 Gold 路径推断，只有"期望系统报出
    # 冲突状态"的类别才参与计分；其余记 None（不可判定），不再一律记 0。
    # 旧口径对所有 conflict 案例一律要求 conflicting_evidence，把"不适用"
    # 与"答错"混为一谈 —— 这是 conflict_accuracy 恒为 0 的根因。
    conflict_expect = conflict_expectation(case) if _is_conflict_case(case) else None
    conflict_accuracy = (
        float(result_state == conflict_expect["expected_state"])
        if conflict_expect and conflict_expect["applicable"]
        else None
    )
    if expected_behavior == "abstain":
        return {
            "case_id": case["case_id"],
            "citation_correctness": None,
            "citation_precision": None,
            "citation_recall": None,
            "citation_offered_f1": None,
            "citation_usage_ratio": None,
            "refusal_correctness": refusal_correctness,
            "version_validity": None,
            "citation_fidelity": None,
            "citation_marker_validity": None,
            "required_fact_coverage": None,
            "forbidden_fact_avoidance": None,
            "acl_leakage": acl_leakage,
            "conflict_accuracy": conflict_accuracy,
            "conflict_kind": (conflict_expect or {}).get("kind"),
            "conflict_applicable": bool(conflict_expect and conflict_expect["applicable"]),
            "failures": failures + (["acl_leakage"] if acl_leakage else []),
        }

    answer = str(prediction.get("answer") or "")
    citations = prediction.get("citations") or []
    expected_paths = set(case.get("gold_vault_paths", []))

    # P0：``citations`` 是候选证据，``cited`` 才是正文实际引用的证据。
    cited_ranks = _cited_ranks(prediction)
    cited_paths = {
        item.get("vault_path")
        for item in citations
        if item.get("final_rank") in cited_ranks and item.get("vault_path")
    }
    offered_paths = {item.get("vault_path") for item in citations if item.get("vault_path")}

    citation_precision: float | None
    citation_recall: float | None
    citation_correctness: float | None
    if not expected_paths or not offered_paths:
        # 没有可比对象：本 case 没有 Gold 路径，或本次压根没有候选证据可引。
        citation_precision = citation_recall = citation_correctness = None
    elif not cited_paths:
        # 有候选证据、却一条都没引用 → 明确记 0。这不是"不可判定"：提示词要求
        # 使用 [citation-N] 标注来源，"没去引用"与"没能引用"必须分开。
        citation_precision = None
        citation_recall = 0.0
        citation_correctness = 0.0
    else:
        overlap = len(expected_paths & cited_paths)
        citation_precision = overlap / len(cited_paths)
        citation_recall = overlap / len(expected_paths)
        citation_correctness = (
            0.0
            if citation_precision + citation_recall == 0
            else 2 * citation_precision * citation_recall / (citation_precision + citation_recall)
        )
    # 旧口径（对候选证据集合算 F1）保留为独立指标，用于与 v1 结果逐位对照。
    # 这里刻意不加"任一侧为空则不可判定"的守卫：v1 就是把这种情况记 0.0 的，
    # 要能复现 v1 就必须保留它原本的语义（这也是它作为历史对照指标的意义）。
    citation_offered_f1 = _citation_f1(expected_paths, offered_paths)
    citation_usage_ratio = len(cited_ranks) / len(citations) if citations else None
    if citation_correctness is not None and citation_correctness < 1:
        failures.append("citation_mismatch")

    citation_fidelity = _citation_fidelity(answer, citations)
    if citation_fidelity == 0.0:
        failures.append("citation_fidelity_violation")

    marker_report = _citation_marker_report(answer, citations)
    citation_marker_validity = _citation_marker_validity(answer, citations)
    if citation_marker_validity == 0.0:
        failures.append(_citation_marker_failure_code(marker_report))

    version_validity = float(bool(citations) and all(_is_version_valid(item, case) for item in citations))
    if not version_validity:
        failures.append("invalid_policy_version")

    required_fact_coverage = _fact_coverage(answer, case.get("required_facts", []))
    if required_fact_coverage < 1:
        failures.append("missing_required_fact")
    forbidden_fact_avoidance = _fact_avoidance(answer, case.get("forbidden_facts", []))
    if not forbidden_fact_avoidance:
        failures.append("forbidden_fact_present")

    if acl_leakage:
        failures.append("acl_leakage")
    # 只在指标适用时报失败：不适用的案例连"是否拦截"都无从判断，
    # 记 failure 会让它出现在 failed_cases 里，把"口径不适用"读成"系统做错了"。
    if conflict_expect and conflict_expect["applicable"] and conflict_accuracy == 0.0:
        failures.append("conflict_not_intercepted")
    return {
        "case_id": case["case_id"],
        "citation_correctness": citation_correctness,
        "citation_precision": citation_precision,
        "citation_recall": citation_recall,
        "citation_offered_f1": citation_offered_f1,
        "citation_usage_ratio": citation_usage_ratio,
        "refusal_correctness": refusal_correctness,
        "version_validity": version_validity,
        "citation_fidelity": citation_fidelity,
        "citation_marker_validity": citation_marker_validity,
        "required_fact_coverage": required_fact_coverage,
        "forbidden_fact_avoidance": forbidden_fact_avoidance,
        "acl_leakage": acl_leakage,
        "conflict_accuracy": conflict_accuracy,
        "conflict_kind": (conflict_expect or {}).get("kind"),
        "conflict_applicable": bool(conflict_expect and conflict_expect["applicable"]),
        "failures": failures,
    }


def evaluate_answer_predictions(cases: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate a complete prediction set without permitting cherry-picked cases."""
    predictions_by_id: dict[str, dict[str, Any]] = {}
    for prediction in predictions:
        case_id = prediction.get("case_id")
        if not case_id:
            raise ValueError("prediction is missing case_id")
        if case_id in predictions_by_id:
            raise ValueError(f"duplicate prediction case_id: {case_id}")
        predictions_by_id[case_id] = prediction

    case_ids = {case["case_id"] for case in cases}
    missing = sorted(case_ids - predictions_by_id.keys())
    unknown = sorted(predictions_by_id.keys() - case_ids)
    if missing:
        raise ValueError(f"missing predictions: {', '.join(missing)}")
    if unknown:
        raise ValueError(f"unknown prediction case_id: {', '.join(unknown)}")

    results = [evaluate_answer_case(case, predictions_by_id[case["case_id"]]) for case in cases]
    summary = summarize_answer_evaluations(results)
    summary["metrics"].update(_operational_metrics(predictions))
    return summary


# 答案级图消融对比的信任指标（operational 指标单独处理）
_GRAPH_ABLATION_COMPARED_METRICS = (
    "citation_correctness",
    "citation_precision",
    "citation_recall",
    "citation_offered_f1",
    "citation_usage_ratio",
    "citation_fidelity",
    "citation_marker_validity",
    "version_validity",
    "required_fact_coverage",
    "forbidden_fact_avoidance",
    "refusal_correctness",
    "conflict_accuracy",
    "acl_leakage",
)
_GRAPH_ABLATION_OPERATIONAL_METRICS = ("mean_total_latency_ms", "mean_total_tokens")


def _aligned_metric_pairs(
    off_results: list[dict[str, Any]],
    on_results: list[dict[str, Any]],
    name: str,
) -> list[tuple[float | None, float | None]]:
    """按 case 对齐两条 arm 的同一指标值（不可判定时为 None）。"""
    return [
        (off.get(name), on.get(name))
        for off, on in zip(off_results, on_results, strict=True)
    ]


def _mean(values: Iterable[float]) -> float | None:
    applicable = [value for value in values if value is not None]
    return fmean(applicable) if applicable else None


def _paired_delta(pairs: list[tuple[float | None, float | None]]) -> tuple[float | None, int]:
    deltas = [on - off for off, on in pairs if off is not None and on is not None]
    return (fmean(deltas) if deltas else None, len(deltas))


def compare_graph_ablation(
    cases: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
) -> dict[str, Any]:
    """同一 case、同一模型/配置下 graph off/on 双跑的配对答案级消融。

    每条 prediction 必须带布尔 ``graph_enabled``；每个 case 必须恰好各有一条
    off 与 on 预测。只比较确定性指标（citation F1、fact coverage、refusal/
    conflict correctness、版本、延迟与 token），不修改任何 case 标签。

    ``paired_delta`` 语义为 on-off：正值表示开启图谱后指标上升；延迟/token 为
    正向成本指标，正值表示开启后更慢/更贵（读法需区分，勿与质量指标混读）。
    """
    by_case: dict[str, dict[bool, dict[str, Any]]] = {}
    for prediction in predictions:
        case_id = prediction.get("case_id")
        if not case_id:
            raise ValueError("prediction is missing case_id")
        graph_enabled = prediction.get("graph_enabled")
        if not isinstance(graph_enabled, bool):
            raise ValueError(f"prediction case_id {case_id!r} requires bool graph_enabled for graph ablation")
        arm = by_case.setdefault(case_id, {})
        if graph_enabled in arm:
            raise ValueError(f"duplicate graph_enabled={graph_enabled!r} prediction for case_id: {case_id}")
        arm[graph_enabled] = prediction

    case_ids = {case["case_id"] for case in cases}
    unknown = sorted(by_case.keys() - case_ids)
    if unknown:
        raise ValueError(f"unknown prediction case_id: {', '.join(unknown)}")
    incomplete = [
        case_id
        for case_id in sorted(case_ids)
        if by_case.get(case_id) is None or set(by_case[case_id]) != {False, True}
    ]
    if incomplete:
        raise ValueError(
            "graph ablation requires graph off/on predictions for every case; "
            f"incomplete case_id: {', '.join(incomplete)}"
        )

    ordered_off = [by_case[case["case_id"]][False] for case in cases]
    ordered_on = [by_case[case["case_id"]][True] for case in cases]
    off_results = [evaluate_answer_case(case, prediction) for case, prediction in zip(cases, ordered_off, strict=True)]
    on_results = [evaluate_answer_case(case, prediction) for case, prediction in zip(cases, ordered_on, strict=True)]

    off_summary = summarize_answer_evaluations(off_results)
    off_summary["metrics"].update(_operational_metrics(ordered_off))
    on_summary = summarize_answer_evaluations(on_results)
    on_summary["metrics"].update(_operational_metrics(ordered_on))

    metrics: dict[str, dict[str, Any]] = {}
    pairs_by_index: dict[str, list[tuple[float | None, float | None]]] = {}
    for name in _GRAPH_ABLATION_COMPARED_METRICS:
        pairs = _aligned_metric_pairs(off_results, on_results, name)
        pairs_by_index[name] = pairs
        delta, paired_cases = _paired_delta(pairs)
        metrics[name] = {
            "graph_off_mean": _mean([off for off, _on in pairs]),
            "graph_on_mean": _mean([on for _off, on in pairs]),
            "paired_delta": round(delta, 4) if delta is not None else None,
            "paired_cases": paired_cases,
        }
    for name in _GRAPH_ABLATION_OPERATIONAL_METRICS:
        off_value = off_summary["metrics"].get(name)
        on_value = on_summary["metrics"].get(name)
        delta = round(on_value - off_value, 4) if off_value is not None and on_value is not None else None
        metrics[name] = {
            "graph_off_mean": off_value,
            "graph_on_mean": on_value,
            "paired_delta": delta,
            "paired_cases": len(cases) if delta is not None else 0,
        }

    stratified: dict[str, dict[str, dict[str, Any]]] = {}
    for dimension in ("category", "query_type"):
        groups: dict[str, list[int]] = {}
        for index, case in enumerate(cases):
            raw_value = case.get(dimension)
            key = "unset" if raw_value is None else str(raw_value)
            groups.setdefault(key, []).append(index)
        per_dimension: dict[str, dict[str, Any]] = {}
        for key, indices in sorted(groups.items()):
            group_metrics: dict[str, Any] = {"sample_size": len(indices)}
            for name in _GRAPH_ABLATION_COMPARED_METRICS:
                pairs = [pairs_by_index[name][index] for index in indices]
                delta, paired_cases = _paired_delta(pairs)
                group_metrics[f"{name}_paired_delta"] = round(delta, 4) if delta is not None else None
                group_metrics[f"{name}_paired_cases"] = paired_cases
            per_dimension[key] = group_metrics
        stratified[dimension] = per_dimension

    regressions = []
    for case, off_result, on_result in zip(cases, off_results, on_results, strict=True):
        new_failures = sorted(set(on_result.get("failures", [])) - set(off_result.get("failures", [])))
        recovered_failures = sorted(set(off_result.get("failures", [])) - set(on_result.get("failures", [])))
        if new_failures or recovered_failures:
            regressions.append({
                "case_id": case["case_id"],
                "category": case.get("category"),
                "query_type": case.get("query_type"),
                "expected_behavior": case.get("expected_behavior"),
                "new_failures_with_graph": new_failures,
                "recovered_failures_with_graph": recovered_failures,
            })

    return {
        "evaluator_version": "mindgraph-answer-v2-graph-ablation",
        "sample_size": len(cases),
        "arms": {"graph_off": off_summary, "graph_on": on_summary},
        "metrics": metrics,
        "stratified_paired_delta": stratified,
        "failure_changes": regressions,
    }


def _optional_nonnegative_number(container: dict[str, Any], key: str, label: str) -> float | None:
    value = container.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{label} must be a finite non-negative number")
    return float(value)


def _operational_metrics(predictions: list[dict[str, Any]]) -> dict[str, Any]:
    latencies: list[float] = []
    total_tokens: list[float] = []
    costs: list[float] = []
    currencies: set[str] = set()
    for prediction in predictions:
        timing = prediction.get("timing") or {}
        usage = prediction.get("usage") or {}
        latency = _optional_nonnegative_number(timing, "total_ms", "timing.total_ms")
        tokens = _optional_nonnegative_number(usage, "total_tokens", "usage.total_tokens")
        cost = _optional_nonnegative_number(usage, "estimated_cost", "usage.estimated_cost")
        if latency is not None:
            latencies.append(latency)
        if tokens is not None:
            total_tokens.append(tokens)
        if cost is not None:
            currency = str(usage.get("currency") or "").strip().upper()
            if not currency:
                raise ValueError("estimated_cost requires currency")
            costs.append(cost)
            currencies.add(currency)
    if len(currencies) > 1:
        raise ValueError(f"mixed cost currencies: {', '.join(sorted(currencies))}")

    sample_size = len(predictions)
    sorted_latencies = sorted(latencies)
    p95_index = max(0, math.ceil(0.95 * len(sorted_latencies)) - 1)
    p50_index = max(0, math.ceil(0.50 * len(sorted_latencies)) - 1)
    return {
        "mean_total_latency_ms": fmean(latencies) if latencies else None,
        "p50_total_latency_ms": sorted_latencies[p50_index] if sorted_latencies else None,
        "p95_total_latency_ms": sorted_latencies[p95_index] if sorted_latencies else None,
        "latency_coverage": len(latencies) / sample_size if sample_size else 0.0,
        "mean_total_tokens": fmean(total_tokens) if total_tokens else None,
        "token_usage_coverage": len(total_tokens) / sample_size if sample_size else 0.0,
        "mean_estimated_cost": fmean(costs) if costs else None,
        "cost_coverage": len(costs) / sample_size if sample_size else 0.0,
        "cost_currency": next(iter(currencies), None),
    }


def summarize_answer_evaluations(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate applicable metrics while preserving auditable failed cases."""
    metrics = {}
    for name in ANSWER_METRICS:
        values = [float(item[name]) for item in results if item.get(name) is not None]
        metrics[name] = fmean(values) if values else None
    failed_cases = [item for item in results if item.get("failures")]
    return {
        "metrics": metrics,
        "sample_size": len(results),
        "failed_case_count": len(failed_cases),
        "failed_cases": failed_cases,
        # 分母必须可见：否则"不适用记 None"会让指标悄悄从报表上消失，
        # 没人能区分"没有冲突案例"与"有案例但都不适用"。
        "conflict_breakdown": _conflict_breakdown(results),
    }


def _conflict_breakdown(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    kinds = [item.get("conflict_kind") for item in results if item.get("conflict_kind")]
    if not kinds:
        return None
    applicable = [item for item in results if item.get("conflict_applicable")]
    return {
        "conflict_case_count": len(kinds),
        "by_kind": dict(sorted(Counter(kinds).items())),
        "applicable_count": len(applicable),
        "scored_denominator": sum(
            1 for item in applicable if item.get("conflict_accuracy") is not None
        ),
    }
