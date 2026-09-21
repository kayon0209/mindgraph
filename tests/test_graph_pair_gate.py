"""图扩展 on/off 产物对的门控判定（计划 5 消融闸门）。

## 为什么需要这一层

图扩展的实验产物由 ``scripts/run_external_eval2.py`` 用
``mindgraph_retrieval_eval`` 产出（dataset 2.2.0 / MG-ENT-* 黄金集），而
``ablation_runner`` 的门控只认 ``retrieval_eval`` 的策略行（dataset 1.0.0，
``STRATEGIES`` 里**根本没有 graph 行**）。两条链路不相交的后果是：图扩展的
原始数据一直在，门控却永远输出 ``no_comparable_graph_and_baseline_rows``——
"是否把图扩展设为默认"这个决策实际上是人工看 JSON 拍的，不是门控算的。

本模块锁的是把两份产物接进同一套门控判据时的**正确性边界**：
- 增益不足 → 不晋升（keep_graph_disabled）；
- 增益达标且延迟在预算内 → 给 conditional_only 建议；
- **缺延迟数据时必须说"缺"，不得默认判延迟回归**（旧实现 baseline 延迟取不到
  时 ``latency_ratio=inf`` → 恒定命中 ``latency_regression``，是假结论）；
- 产物对必须同数据集、且 off 侧真的没开图，否则控制变量不成立。
"""

from __future__ import annotations

import pytest

from evaluation.ablation_runner import evaluate_graph_pair_gate


def _report(
    *,
    recall: float,
    mrr: float = 0.77,
    latency: float | None = 20.0,
    graph_enabled: bool,
    dataset_version: str = "2.2.0",
    digest: str = "abc123",
    top_k: int = 5,
    extra_summary: dict | None = None,
) -> dict:
    summary: dict[str, object] = {
        "recall_at_k": recall,
        "precision_at_k": 0.22,
        "mrr": mrr,
        "ndcg_at_k": 0.78,
    }
    if latency is not None:
        summary["mean_retrieval_latency_ms"] = latency
    summary.update(extra_summary or {})
    return {
        "evaluator_version": "mindgraph-retrieval-v2",
        "dataset_version": dataset_version,
        "dataset_sha256": digest,
        "top_k": top_k,
        "summary": summary,
        "graph_diagnostics": {
            "enabled_cases": 46 if graph_enabled else 0,
            "activated_cases": 35 if graph_enabled else 0,
            "expanded_candidates": 73 if graph_enabled else 0,
            "activation_rate": 0.7609 if graph_enabled else 0.0,
            "comparable_for_graph_gain": graph_enabled,
            "limitations": [] if graph_enabled else ["graph_not_enabled"],
        },
        "details": [],
        "failed_cases": [],
    }


def test_gain_below_threshold_keeps_graph_disabled():
    """增益 0.0025（实测值）远低于 0.05 门槛 → 不晋升。"""
    decision = evaluate_graph_pair_gate(
        _report(recall=0.8576, graph_enabled=True),
        _report(recall=0.8551, graph_enabled=False),
    )

    assert decision["eligible"] is False
    assert "recall_gain_below_threshold" in decision["reasons"]
    assert decision["default_route_recommendation"] == "keep_graph_disabled"
    assert decision["recall_gain"] == pytest.approx(0.0025, abs=1e-6)


def test_sufficient_gain_within_latency_budget_is_eligible():
    decision = evaluate_graph_pair_gate(
        _report(recall=0.92, latency=40.0, graph_enabled=True),
        _report(recall=0.85, latency=20.0, graph_enabled=False),
    )

    assert decision["eligible"] is True
    assert decision["reasons"] == []
    assert decision["default_route_recommendation"] == "conditional_only"
    assert decision["latency_ratio"] == pytest.approx(2.0)


def test_missing_latency_is_reported_as_unknown_not_as_regression():
    """缺延迟数据 → ``latency_ratio=None`` + 明确 limitation，绝不默认判回归。"""
    decision = evaluate_graph_pair_gate(
        _report(recall=0.92, latency=None, graph_enabled=True),
        _report(recall=0.85, latency=None, graph_enabled=False),
    )

    assert decision["latency_ratio"] is None
    assert "latency_regression" not in decision["reasons"]
    assert "latency_unavailable" in decision["limitations"]
    assert decision["eligible"] is True


def test_latency_regression_still_blocks_promotion():
    decision = evaluate_graph_pair_gate(
        _report(recall=0.92, latency=100.0, graph_enabled=True),
        _report(recall=0.85, latency=20.0, graph_enabled=False),
    )

    assert decision["eligible"] is False
    assert "latency_regression" in decision["reasons"]


def test_different_dataset_versions_are_not_comparable():
    with pytest.raises(ValueError, match="dataset_version"):
        evaluate_graph_pair_gate(
            _report(recall=0.92, graph_enabled=True),
            _report(recall=0.85, graph_enabled=False, dataset_version="2.1.0"),
        )


def test_different_dataset_digest_is_not_comparable():
    with pytest.raises(ValueError, match="dataset_sha256"):
        evaluate_graph_pair_gate(
            _report(recall=0.92, graph_enabled=True),
            _report(recall=0.85, graph_enabled=False, digest="other"),
        )


def test_baseline_report_with_graph_enabled_is_rejected():
    """off 侧若已经开了图，控制变量不成立，必须拒绝而不是硬算。"""
    with pytest.raises(ValueError, match="baseline"):
        evaluate_graph_pair_gate(
            _report(recall=0.92, graph_enabled=True),
            _report(recall=0.85, graph_enabled=True),
        )


def test_non_top_k_five_report_is_rejected():
    with pytest.raises(ValueError, match="top_k"):
        evaluate_graph_pair_gate(
            _report(recall=0.92, graph_enabled=True, top_k=10),
            _report(recall=0.85, graph_enabled=False),
        )


def test_zero_baseline_recall_is_rejected_as_mismatched_data():
    """基线 Recall=0 时两侧必然是 0 增益——那是 golden 与索引错配，不是"没效果"。

    真实踩过：把 vault 路径的 golden（2.4.0）接到只含 5 篇中文制度的旧索引上，
    两侧 Recall 全 0，门控照常输出 eligible=false + recall_gain_below_threshold，
    结论方向恰好与真实情况一致，于是数据错配被完全掩盖。
    """
    with pytest.raises(ValueError, match="baseline 的 Recall 为 0"):
        evaluate_graph_pair_gate(
            _report(recall=0.0, graph_enabled=True),
            _report(recall=0.0, graph_enabled=False),
        )


def test_gate_presents_evidence_completeness_metrics():
    """门控必须同时呈现「Top-5 之外」的口径。

    图扩展的机制是在 Top-5 **之外**追加旁证，用截断到 5 条的 Recall@5 去评判它
    等于用错的尺子量。实测（8 条 confirmed、76 例）：R@5 增益 0.0000，但
    full_set_recall +1.75pp、平均证据条数 4.91 → 6.41。这两个口径必须出现在判定
    输出里，否则只剩一个会误导人的数字。
    """
    decision = evaluate_graph_pair_gate(
        _report(recall=0.886, graph_enabled=True,
                extra_summary={"full_set_recall": 0.9035, "mean_evidence_size": 6.4079}),
        _report(recall=0.886, graph_enabled=False,
                extra_summary={"full_set_recall": 0.886, "mean_evidence_size": 4.9079}),
    )

    assert decision["graph_metrics"]["full_set_recall"] == 0.9035
    assert decision["baseline_metrics"]["mean_evidence_size"] == 4.9079


def test_missing_completeness_metrics_stay_none_not_zero():
    """缺这两个口径时记 None，不臆造 0（否则会渲染成「证据零增长」的假象）。"""
    decision = evaluate_graph_pair_gate(
        _report(recall=0.92, graph_enabled=True),
        _report(recall=0.85, graph_enabled=False),
    )

    assert decision["graph_metrics"]["full_set_recall"] is None
    assert decision["graph_metrics"]["mean_evidence_size"] is None
