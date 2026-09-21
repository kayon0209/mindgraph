from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METRICS = ("recall_at_1", "recall_at_3", "recall_at_5", "mrr", "document_hit_rate", "chunk_hit_rate", "mean_retrieval_latency_ms")


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _as_float(value: object) -> float | None:
    """``bool`` 是 ``int`` 的子类，需显式排除；非数值一律视作"缺数据"。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def evaluate_graph_gate(
    graph_metrics: dict[str, float],
    baseline_metrics: dict[str, float],
    *,
    min_recall_gain: float = 0.05,
    max_latency_multiplier: float = 3.0,
) -> dict[str, object]:
    graph_recall = float(graph_metrics.get("recall_at_5", 0.0))
    baseline_recall = float(baseline_metrics.get("recall_at_5", 0.0))
    graph_latency = _as_float(graph_metrics.get("mean_retrieval_latency_ms"))
    baseline_latency = _as_float(baseline_metrics.get("mean_retrieval_latency_ms"))
    recall_gain = graph_recall - baseline_recall
    # 取不到基线延迟时必须记 None（"未知"），不能写成 inf —— 那会让
    # latency_regression 恒成立，把"没测"当成"测出来超标"，产出假结论。
    latency_ratio = graph_latency / baseline_latency if graph_latency is not None and baseline_latency else None
    reasons: list[str] = []
    limitations: list[str] = []
    if recall_gain < min_recall_gain:
        reasons.append("recall_gain_below_threshold")
    if latency_ratio is None:
        limitations.append("latency_unavailable")
    elif latency_ratio > max_latency_multiplier:
        reasons.append("latency_regression")
    eligible = not reasons
    return {
        "eligible": eligible,
        "reasons": reasons,
        "limitations": limitations,
        "recall_gain": recall_gain,
        "latency_ratio": latency_ratio,
        "default_route_recommendation": "conditional_only" if eligible else "keep_graph_disabled",
    }


def evaluate_ablation(
    rows: list[dict[str, object]],
    *,
    graph_strategy: str,
    baseline_strategy: str,
) -> dict[str, object]:
    by_name = {str(row["retrieval_strategy"]): row for row in rows}
    if graph_strategy not in by_name or baseline_strategy not in by_name:
        raise ValueError("strategy rows must include both baseline and graph strategies")
    graph_row = by_name[graph_strategy]
    baseline_row = by_name[baseline_strategy]
    deltas = {
        metric: float(graph_row.get(metric, 0.0)) - float(baseline_row.get(metric, 0.0))
        for metric in METRICS
    }
    gate = evaluate_graph_gate(graph_row, baseline_row)
    return {
        "graph_strategy": graph_strategy,
        "baseline_strategy": baseline_strategy,
        "graph_metrics": {metric: graph_row.get(metric) for metric in METRICS},
        "baseline_metrics": {metric: baseline_row.get(metric) for metric in METRICS},
        "deltas": deltas,
        "decision": {
            "eligible": gate["eligible"],
            "reasons": gate["reasons"],
            "statistical_significance": False,
            "default_route_recommendation": gate["default_route_recommendation"],
        },
    }


GRAPH_MIN_RECALL_GAIN = 0.05
GRAPH_MAX_LATENCY_MULTIPLIER = 3.0

# mindgraph_retrieval_eval 的产物口径：``recall_at_k`` 就是 Top-K 截断召回，
# 只有 K=5 才与闸门里的 Recall@5 同义。
_GRAPH_REPORT_TOP_K = 5


def _graph_report_metrics(report: dict, *, side: str) -> dict[str, float | None]:
    """从 mindgraph 图评测产物里取出闸门所需的三个量（missing → None）。"""
    if report.get("top_k") != _GRAPH_REPORT_TOP_K:
        raise ValueError(
            f"{side} report top_k must be {_GRAPH_REPORT_TOP_K} to be comparable with Recall@5; "
            f"got {report.get('top_k')!r}"
        )
    summary = report.get("summary") or {}
    recall = _as_float(summary.get("recall_at_k"))
    if recall is None:
        raise ValueError(f"{side} report is missing summary.recall_at_k")
    # mean 优先；旧产物只有 p50 时退回 p50 并记 limitation（见下方 latency_source）。
    latency = _as_float(summary.get("mean_retrieval_latency_ms"))
    latency_source = "mean" if latency is not None else None
    if latency is None:
        latency = _as_float(summary.get("p50_retrieval_ms"))
        latency_source = "p50_fallback" if latency is not None else None
    return {
        "recall_at_5": recall,
        "mrr": _as_float(summary.get("mrr")),
        "mean_retrieval_latency_ms": latency,
        "latency_source": latency_source,
        # 图扩展的机制是"在 Top-5 **之外**追加证据"，所以 recall_at_5 天然测不出它的
        # 价值（实测：图开启 R@5 增益 0.0000，而 full_set_recall +0.0175、
        # mean_evidence_size +1.50 条）。这两个口径必须一起呈现，否则只看 R@5 会
        # 得出"图扩展无用"的片面结论。注意：呈现 ≠ 放宽门槛，晋升判据仍是
        # evaluate_graph_gate 里的 Recall@5 ≥ +5pp。
        "full_set_recall": _as_float(summary.get("full_set_recall")),
        "mean_evidence_size": _as_float(summary.get("mean_evidence_size")),
    }


def evaluate_graph_pair_gate(
    on_report: dict,
    off_report: dict,
    *,
    min_recall_gain: float = GRAPH_MIN_RECALL_GAIN,
    max_latency_multiplier: float = GRAPH_MAX_LATENCY_MULTIPLIER,
) -> dict[str, object]:
    """用一对 graph on/off 产物做闸门判定，产出可写进治理文档的结论。

    与 ``evaluate_ablation``（策略行口径）的区别：这里比的是**同一个策略开着/
    关着图扩展**，因此必须先证明两份产物真的可比 —— 同数据集、同数据集指纹、
    且 off 侧确实没开图。任一不成立就直接拒绝，不给"看起来算过了"的结论。
    """
    for label, report in (("graph-on", on_report), ("baseline", off_report)):
        if not isinstance(report, dict):
            raise ValueError(f"{label} report must be a dict")
    if on_report.get("dataset_version") != off_report.get("dataset_version"):
        raise ValueError(
            "graph-on 与 baseline 产物的 dataset_version 不一致，不可比："
            f"{on_report.get('dataset_version')!r} vs {off_report.get('dataset_version')!r}"
        )
    if on_report.get("dataset_sha256") != off_report.get("dataset_sha256"):
        raise ValueError(
            "graph-on 与 baseline 产物的 dataset_sha256 不一致，不可比："
            f"{on_report.get('dataset_sha256')!r} vs {off_report.get('dataset_sha256')!r}"
        )
    off_diagnostics = off_report.get("graph_diagnostics") or {}
    if int(off_diagnostics.get("enabled_cases") or 0) > 0:
        raise ValueError(
            "baseline 产物里 graph 已被启用（enabled_cases="
            f"{off_diagnostics.get('enabled_cases')}），控制变量不成立，拒绝判定"
        )

    on_metrics = _graph_report_metrics(on_report, side="graph-on")
    off_metrics = _graph_report_metrics(off_report, side="baseline")
    # 基线失效守卫：当 golden 与索引不匹配（例如把 vault 路径的数据集接到只含
    # 5 篇中文制度的旧索引上）两侧 Recall 都会是 0，此时"增益 0"不是实验结论
    # 而是数据错配。不拦的话，产出的 JSON 看起来完全正常（eligible=false /
    # recall_gain_below_threshold），结论恰好与真实情况同向，于是错误被掩盖。
    if float(off_metrics["recall_at_5"] or 0.0) <= 0.0:
        raise ValueError(
            "baseline 的 Recall 为 0：golden 与索引不匹配导致基线失效，增益比较无意义。"
            "请先确认索引根与数据集版本对应（MindGraph 数据集需 mg-* 索引）。"
        )
    gate = evaluate_graph_gate(
        on_metrics, off_metrics,
        min_recall_gain=min_recall_gain,
        max_latency_multiplier=max_latency_multiplier,
    )
    limitations = list(gate["limitations"])
    if on_report.get("dataset_sha256") is None:
        limitations.append("dataset_digest_missing")
    if on_metrics["latency_source"] == "p50_fallback":
        limitations.append("latency_p50_fallback")
    counts = on_report.get("counts") or {}
    sample_size = on_report.get("sample_size") or sum(
        int(counts.get(name) or 0) for name in ("answer", "abstain")
    )
    return {
        # 先展开 gate，再用下面的显式键覆盖 —— limitations 必须是"gate 项 + 本层新增项"
        # 的合集，放在 **gate 之前会被它整体覆盖掉。
        **gate,
        "variable": "graph_expansion",
        "dataset_version": on_report.get("dataset_version"),
        "dataset_sha256": on_report.get("dataset_sha256"),
        "sample_size": sample_size,
        "graph_metrics": on_metrics,
        "baseline_metrics": off_metrics,
        "graph_diagnostics": on_report.get("graph_diagnostics") or {},
        "limitations": limitations,
    }


def _stratified_deltas(
    data: dict,
    graph_strategy: str | None,
    baseline_strategy: str | None,
    *,
    metric: str = "recall_at_5",
    dimension: str = "category",
) -> dict[str, dict[str, float]]:
    """计划 5：按子集（category）统计 Graph 增益，而非只看整体均值。

    details 结构：{strategy: [{case_id, category, metrics: {...}}, ...]}。
    缺少可比数据时返回空 dict（调用方保留 limitations 说明）。
    """
    if not graph_strategy or not baseline_strategy:
        return {}
    details = data.get("details")
    if not isinstance(details, dict):
        return {}
    graph_rows = details.get(graph_strategy) or []
    baseline_rows = details.get(baseline_strategy) or []
    if not graph_rows or not baseline_rows:

        return {}
    baseline_by_case = {str(row.get("case_id")): row for row in baseline_rows if isinstance(row, dict)}
    groups: dict[str, dict[str, list[float]]] = {}
    for row in graph_rows:
        if not isinstance(row, dict):
            continue
        baseline_row = baseline_by_case.get(str(row.get("case_id")))
        if baseline_row is None:
            continue
        graph_metrics = row.get("metrics") or {}
        baseline_metrics = baseline_row.get("metrics") or {}
        graph_value = graph_metrics.get(metric)
        baseline_value = baseline_metrics.get(metric)
        if graph_value is None or baseline_value is None:
            continue
        key = str(row.get(dimension) or "uncategorized")
        bucket = groups.setdefault(key, {"graph": [], "baseline": []})
        bucket["graph"].append(float(graph_value))
        bucket["baseline"].append(float(baseline_value))
    stratified: dict[str, dict[str, float]] = {}
    for key, values in sorted(groups.items()):
        graph_mean = sum(values["graph"]) / len(values["graph"])
        baseline_mean = sum(values["baseline"]) / len(values["baseline"])
        stratified[key] = {
            "sample_size": float(len(values["graph"])),
            f"graph_{metric}": round(graph_mean, 4),
            f"baseline_{metric}": round(baseline_mean, 4),
            "delta": round(graph_mean - baseline_mean, 4),
        }
    return stratified


def run(source: Path) -> dict:
    data = json.loads(source.read_text(encoding="utf-8"))
    rows = [{"retrieval_strategy": strategy, **{metric: values.get(metric) for metric in METRICS}}
            for strategy, values in data["summary"].items()]
    graph_strategy = next((row["retrieval_strategy"] for row in rows if row["retrieval_strategy"].endswith("_graph")), None)
    baseline_strategy = next((row["retrieval_strategy"] for row in rows if row["retrieval_strategy"] in {"bm25_vector", "hybrid"}), None)
    if graph_strategy is None or baseline_strategy is None:
        return {
            "source": str(source),
            "dataset_version": data.get("dataset_version"),
            "sample_size": data.get("retrieval_eligible_cases", 0),
            "ablation_variable": "retrieval_strategy",
            "frozen_controls": data.get("controls", {}),
            "results": rows,
            "decision": {"eligible": False, "reasons": ["no_comparable_graph_and_baseline_rows"], "statistical_significance": False, "default_route_recommendation": "keep_graph_disabled"},
            "deltas": {},
            "stratified": {},
            "limitations": ["The source contains no explicitly comparable graph and baseline rows; no gate decision was inferred."],
        }
    decision = evaluate_ablation(rows, graph_strategy=graph_strategy, baseline_strategy=baseline_strategy)
    stratified = _stratified_deltas(data, graph_strategy, baseline_strategy)
    return {
        "source": str(source),
        "dataset_version": data["dataset_version"],
        "sample_size": data["retrieval_eligible_cases"],
        "ablation_variable": "retrieval_strategy",
        "frozen_controls": data.get("controls", {}),
        "results": rows,
        "decision": decision["decision"],
        "deltas": decision["deltas"],
        "stratified": stratified,
        "limitations": [
            "Uses the existing development/regression-derived 23 retrieval-eligible cases, not an independent holdout.",
            "Only retrieval strategy changes; no generation-model conclusion is supported.",
            "No statistical significance test is claimed.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    result = run(Path(args.source))
    output = ROOT / "evaluation" / "results" / "governance"
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = output / f"ablation_{stamp}.json"
    csv_path = output / f"ablation_{stamp}.csv"
    markdown_path = ROOT / "docs" / "evaluation" / "retrieval-ablation.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(result["results"][0]) if result["results"] else METRICS)
        writer.writeheader()
        writer.writerows(result["results"])
    lines = [
        "# Retrieval strategy ablation", "",
        f"Source: `{result['source']}`. Dataset version: `{result['dataset_version']}`. Sample size: {result['sample_size']} retrieval-eligible cases.", "",
        "| Strategy | R@1 | R@3 | R@5 | MRR | Doc hit | Chunk hit | Mean latency (ms) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in result["results"]:
        lines.append("| {retrieval_strategy} | {recall_at_1:.4f} | {recall_at_3:.4f} | {recall_at_5:.4f} | {mrr:.4f} | {document_hit_rate:.4f} | {chunk_hit_rate:.4f} | {mean_retrieval_latency_ms:.4f} |".format(**row))
    lines.extend([
        "",
        "## Decision",
        "",
        f"- Eligible for default graph path: `{result['decision']['eligible']}`",
        f"- Default recommendation: `{result['decision']['default_route_recommendation']}`",
        f"- Reasons: {', '.join(result['decision']['reasons']) if result['decision']['reasons'] else 'none'}",
        "",
        "## Limitations",
        "",
        *[f"- {item}" for item in result["limitations"]],
    ])
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    print(json_path)
    print(csv_path)
    print(markdown_path)


if __name__ == "__main__":
    main()
