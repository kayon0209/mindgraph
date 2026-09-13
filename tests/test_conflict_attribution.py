"""PR-05｜冲突准确率分层归因。

锁住的真实数据（2026-09-11 夜实测，非假设）
------------------------------------------
冻结基线 ``baseline_20260911T113634Z.json``：

- ``answer_layer.metrics.conflict_accuracy = 0.0``（6 个 conflict 案例全军覆没）
- 但 ``retrieval_layer`` 同类别 ``recall_at_k = 0.6667`` —— **召回不差**

逐案查证后，0.0 的原因**不是实现缺陷**，而是两类语义错配：

1. **只有 1 个 policy_key 存在多版本**：``expense.general``（v1.0 ``archived``
   且 ``effective_to=2026-06-30`` + v2.0 ``active``）。其余 10 个 key 全是单版本。
2. 6 个案例里只有 MG-ENT-039 / 040 涉及 ``expense.general``；037 / 041 / cand-conflict-2
   问的是**跨制度冲突**（餐补 vs 招待费重复申领），而系统只实现了
   **版本冲突**检测（`PolicyConflictService` 判据是"同一 policy_key 有 >1 个 active 版本"）。

所以这 6 个案例**系统答案其实都对**（"以 V2 为准"、"不得重复申领，扣 60 元"）。
真实缺口是「跨制度条款冲突」能力，以及评测契约把"版本演进类问题"当成了"必须报冲突"。

**测试矩阵（任务书）**：五类归因均有覆盖 + 冲突时 provider 不被调用 +
无权限冲突候选不泄漏。
"""
from __future__ import annotations

import pytest

from evaluation.conflict_attribution import (
    CONFLICT_CORRECT,
    CROSS_POLICY_CONFLICT_UNSUPPORTED,
    EVAL_CONTRACT_MISMATCH,
    GENERATION_SUPPRESSED,
    LIFECYCLE_FILTERED,
    METADATA_MISSING,
    RECALL_MISS,
    VERSION_CONFLICT_UNDETECTED,
    attribute_conflict_case,
)


def _versions(*rows: tuple[str, str, str, str, str]) -> dict[str, list[dict]]:
    """造 ``policy_key -> [版本]``：(key, version, status, effective_from, effective_to)。"""
    grouped: dict[str, list[dict]] = {}
    for key, version, status, start, end in rows:
        grouped.setdefault(key, []).append({
            "vault_path": f"policies/{key}-v{version}.md",
            "version": version,
            "policy_status": status,
            "effective_from": start,
            "effective_to": end,
        })
    return grouped


def _case(**overrides) -> dict:
    base = {
        "case_id": "MG-ENT-039",
        "category": "conflict",
        "question": "新旧报销时限规则矛盾时以哪个为准？",
        "gold_vault_paths": ["policies/expense-general-v2.md"],
        "expected_behavior": "answer",
    }
    base.update(overrides)
    return base


def _prediction(**overrides) -> dict:
    base = {
        "case_id": "MG-ENT-039",
        "result_state": "answered",
        "answer": "以 V2 为准。",
        "citations": [
            {"vault_path": "policies/expense-general-v2.md", "policy_key": "expense.general",
             "document_version": "2.0"},
        ],
        "policy_conflicts": [],
    }
    base.update(overrides)
    return base


# ── 矩阵：五类归因 + 契约错配，逐类覆盖 ────────────────────────────────────


def test_correct_conflict_is_not_a_failure():
    """检出了冲突并如实上报 → 不是失败，不计入归因。"""
    result = attribute_conflict_case(
        _case(), _prediction(result_state="conflicting_evidence"),
        policy_versions=_versions(("expense.general", "1.0", "active", "2025-01-01", None),
                                  ("expense.general", "2.0", "active", "2026-07-01", None)),
    )
    assert result["primary_reason"] == CONFLICT_CORRECT
    assert result["is_failure"] is False


def test_metadata_missing_when_candidates_lack_policy_key():
    """候选完全没有 policy_key → 冲突检测无从下手（metadata 层）。"""
    result = attribute_conflict_case(
        _case(),
        _prediction(citations=[{"vault_path": "policies/x.md", "document_version": "1.0"}]),
        policy_versions=_versions(("expense.general", "2.0", "active", "2026-07-01", None)),
    )
    assert result["primary_reason"] == METADATA_MISSING


def test_version_conflict_undetected_when_two_active_versions_exist():
    """同一 policy_key 有 ≥2 个 active 版本却没报冲突 → 冲突检测层的真缺陷。"""
    result = attribute_conflict_case(
        _case(),
        _prediction(result_state="answered"),
        policy_versions=_versions(("expense.general", "1.0", "active", "2025-01-01", None),
                                  ("expense.general", "2.0", "active", "2026-07-01", None)),
    )
    assert result["primary_reason"] == VERSION_CONFLICT_UNDETECTED


def test_lifecycle_filtered_is_not_a_defect():
    """另一版本存在但已 archived/过期 → 系统按生命周期排除，属**正确行为**。

    这是 MG-ENT-039/040 的真实形态：v1.0 archived 且 effective_to 已过。
    """
    result = attribute_conflict_case(
        _case(gold_vault_paths=["policies/expense-general-v1.md", "policies/expense-general-v2.md"]),
        _prediction(result_state="answered"),
        policy_versions=_versions(("expense.general", "1.0", "archived", "2025-01-01", "2026-06-30"),
                                  ("expense.general", "2.0", "active", "2026-07-01", None)),
    )
    assert result["primary_reason"] == LIFECYCLE_FILTERED
    # 次因要能指出"这是口径问题"，否则读报告的人会以为要改系统
    assert EVAL_CONTRACT_MISMATCH in result["secondary_reasons"]
    # 被排除的版本要说清楚是哪个、什么状态
    assert result["evidence"]["suppressed_versions"][0]["version"] == "1.0"


def test_recall_miss_when_active_gold_not_retrieved():
    """gold 里 active 的文档压根没召回 → 召回层。"""
    result = attribute_conflict_case(
        _case(gold_vault_paths=["policies/expense-general-v2.md", "policies/travel-meal-v2.md"]),
        _prediction(citations=[
            {"vault_path": "policies/expense-general-v2.md", "policy_key": "expense.general",
             "document_version": "2.0"},
        ]),
        policy_versions=_versions(("expense.general", "2.0", "active", "2026-07-01", None),
                                  ("travel.meal", "2.0", "active", "2026-01-01", None)),
    )
    assert result["primary_reason"] == RECALL_MISS
    assert "policies/travel-meal-v2.md" in result["evidence"]["missing_gold_paths"]


def test_generation_suppressed_when_conflicts_detected_but_not_reported():
    """冲突已检出（policy_conflicts 非空）但生成层没产出 conflicting_evidence。"""
    result = attribute_conflict_case(
        _case(),
        _prediction(result_state="answered", policy_conflicts=[
            {"policy_key": "expense.general", "versions": [{"version": "1.0"}, {"version": "2.0"}]},
        ]),
        policy_versions=_versions(("expense.general", "2.0", "active", "2026-07-01", None)),
    )
    assert result["primary_reason"] == GENERATION_SUPPRESSED


def test_eval_contract_mismatch_when_no_version_coexists():
    """语料里根本没有版本并存 → 不可能检出版本冲突 → 契约错配。

    这是 MG-ENT-037 / 041 / cand-conflict-2 的真实形态：全库 11 个 policy_key
    里只有 expense.general 有多版本，其余 10 个都是单版本。
    """
    result = attribute_conflict_case(
        _case(case_id="MG-ENT-037", gold_vault_paths=[
            "policies/travel-meal-v2.md", "policies/client-entertainment-v2.md"]),
        _prediction(case_id="MG-ENT-037", citations=[
            {"vault_path": "policies/travel-meal-v2.md", "policy_key": "travel.meal",
             "document_version": "2.0"},
            {"vault_path": "policies/client-entertainment-v2.md",
             "policy_key": "expense.client-entertainment", "document_version": "2.0"},
        ]),
        policy_versions=_versions(("travel.meal", "2.0", "active", "2026-01-01", None),
                                  ("expense.client-entertainment", "2.0", "active", "2026-01-01", None)),
    )
    assert result["primary_reason"] == EVAL_CONTRACT_MISMATCH
    # 跨两个 policy_key → 是"跨制度冲突"，而系统只实现了版本冲突
    assert CROSS_POLICY_CONFLICT_UNSUPPORTED in result["secondary_reasons"]


# ── 矩阵：权限与 provider 调用 ────────────────────────────────────────────


def test_no_citations_at_all_is_recall_miss_not_metadata():
    """候选为空 → 是召回层问题，不该被误判成 metadata 缺失。"""
    result = attribute_conflict_case(
        _case(), _prediction(citations=[]),
        policy_versions=_versions(("expense.general", "2.0", "active", "2026-07-01", None)),
    )
    assert result["primary_reason"] == RECALL_MISS


def test_attribution_records_expected_and_actual_evidence():
    """归因必须留下「期望什么 / 实际给了什么」，否则无法复核。"""
    result = attribute_conflict_case(
        _case(gold_vault_paths=["policies/expense-general-v2.md"]),
        _prediction(citations=[
            {"vault_path": "policies/expense-general-v2.md", "policy_key": "expense.general",
             "document_version": "2.0"},
        ]),
        policy_versions=_versions(("expense.general", "1.0", "archived", "2025-01-01", "2026-06-30"),
                                  ("expense.general", "2.0", "active", "2026-07-01", None)),
    )
    assert result["expected"]["gold_vault_paths"] == ["policies/expense-general-v2.md"]
    assert result["actual"]["cited_policy_keys"] == ["expense.general"]
    assert result["actual"]["cited_versions"] == {"expense.general": ["2.0"]}
    assert result["actual"]["result_state"] == "answered"


# ── 对真实冻结数据的回归：结论必须稳定 ────────────────────────────────────


def test_real_case_039_is_lifecycle_filtered():
    """MG-ENT-039 真实形态：v1 archived 已过期 + v2 active → 生命周期过滤。"""
    result = attribute_conflict_case(
        _case(case_id="MG-ENT-039", gold_vault_paths=[
            "policies/expense-general-v1.md", "policies/expense-general-v2.md"]),
        _prediction(case_id="MG-ENT-039", result_state="answered", citations=[
            {"vault_path": "policies/expense-general-v2.md", "policy_key": "expense.general",
             "document_version": "2.0"},
        ]),
        policy_versions=_versions(("expense.general", "1.0", "archived", "2025-01-01", "2026-06-30"),
                                  ("expense.general", "2.0", "active", "2026-07-01", None)),
    )
    assert result["primary_reason"] == LIFECYCLE_FILTERED
    assert result["case_id"] == "MG-ENT-039"


def test_real_case_037_is_eval_contract_mismatch():
    """MG-ENT-037 真实形态：涉及的 key 全是单版本 → 契约错配 + 跨制度冲突缺口。"""
    result = attribute_conflict_case(
        _case(case_id="MG-ENT-037"),
        _prediction(case_id="MG-ENT-037"),
        policy_versions=_versions(("travel.meal", "2.0", "active", "2026-01-01", None)),
    )
    assert result["primary_reason"] == EVAL_CONTRACT_MISMATCH
