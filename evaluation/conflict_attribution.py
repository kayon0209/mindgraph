"""PR-05｜冲突准确率的逐案分层归因。

## 它回答什么问题

冻结基线里 ``conflict_accuracy = 0.0``（6/6 全错），但同类别 ``recall_at_k = 0.6667``
——**召回不差，答案全错**。单一总分无法告诉开发者该改哪一层，本模块把每个失败案例
归到具体那一层，并保留「期望什么 / 实际给了什么」供复核。

## 归因结论（2026-09-11 夜实测）

0.0 **不是实现缺陷**，而是两类语义错配：

1. 全库 11 个 ``policy_key`` 里**只有 ``expense.general`` 有多版本**
   （v1.0 ``archived`` 且 ``effective_to=2026-06-30`` + v2.0 ``active``），其余 10 个单版本。
   而 ``PolicyConflictService`` 的判据是「同一 policy_key 有 >1 个 **active** 版本」→
   绝大多数 conflict 案例**在定义上就不可能**检出版本冲突。
2. 6 个案例中只有 MG-ENT-039/040 涉及 ``expense.general``；037/041/cand-conflict-2 问的是
   **跨制度冲突**（餐补 vs 招待费重复申领），系统只实现了版本冲突检测。

并且这 6 个案例**系统答案其实都对**（"以 V2 为准"、"不得重复申领，扣 60 元"）。

## 与系统侧判据保持一致

``is_system_defect`` 把「真缺陷」与「行为正确/口径问题」分开：
真缺陷才需要改代码；口径问题要产品拍板（改指标 or 改标签），
**不得为了让指标好看去改标签**（见执行包 guardrail）。
"""
from __future__ import annotations

from typing import Any

# ── 归因码 ────────────────────────────────────────────────────────────────
CONFLICT_CORRECT = "conflict_correct"                       # 正确报冲突（非失败）
METADATA_MISSING = "metadata_missing"                       # 候选缺 policy_key
VERSION_CONFLICT_UNDETECTED = "version_conflict_undetected"  # ≥2 active 版本却没报
LIFECYCLE_FILTERED = "lifecycle_filtered"                    # 其他版本已被生命周期排除
RECALL_MISS = "recall_miss"                                  # gold 文档没召回
GENERATION_SUPPRESSED = "generation_suppressed"              # 检出冲突但生成层没报
EVAL_CONTRACT_MISMATCH = "eval_contract_mismatch"            # 评测契约与系统语义错配

# 次因
CROSS_POLICY_CONFLICT_UNSUPPORTED = "cross_policy_conflict_unsupported"

# 与 PolicyConflictService 保持一致：默认只认 active
_ACTIVE_STATUSES = ("active",)

# 属于"真缺陷、需要改代码"的归因码
_SYSTEM_DEFECTS = frozenset({
    METADATA_MISSING, VERSION_CONFLICT_UNDETECTED, RECALL_MISS, GENERATION_SUPPRESSED,
})

REASON_LABELS = {
    CONFLICT_CORRECT: "正确检出并上报冲突",
    METADATA_MISSING: "候选证据缺 policy_key，冲突检测无从下手",
    VERSION_CONFLICT_UNDETECTED: "同一 policy_key 存在多个 active 版本却未报冲突",
    LIFECYCLE_FILTERED: "其他版本已被生命周期排除（archived/过期），系统按现行版本作答——行为正确",
    RECALL_MISS: "应召回的文档未进入候选",
    GENERATION_SUPPRESSED: "冲突已检出但生成层未产出 conflicting_evidence",
    EVAL_CONTRACT_MISMATCH: "评测契约与系统冲突语义错配（非实现缺陷）",
    CROSS_POLICY_CONFLICT_UNSUPPORTED: "跨制度条款冲突，系统仅实现版本冲突检测",
}


def _is_active(version: dict[str, Any]) -> bool:
    return str(version.get("policy_status") or "").strip().lower() in _ACTIVE_STATUSES


def attribute_conflict_case(
    case: dict[str, Any],
    prediction: dict[str, Any],
    *,
    policy_versions: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """把单个 conflict 案例归因到一层，并保留复核所需的全部证据。

    ``policy_versions`` 是 ``policy_key -> [{"version","policy_status",
    "effective_from","effective_to","vault_path"}]``，取自 ``notes`` 表；
    它必须与系统侧 ``PolicyConflictService`` 看到的是同一批数据，否则归因失真。
    """
    citations = [c for c in (prediction.get("citations") or []) if isinstance(c, dict)]
    result_state = prediction.get("result_state")
    detected_conflicts = prediction.get("policy_conflicts") or []

    cited_keys = sorted({str(c.get("policy_key")) for c in citations if c.get("policy_key")})
    cited_versions: dict[str, list[str]] = {}
    for citation in citations:
        key, version = citation.get("policy_key"), citation.get("document_version")
        if key:
            cited_versions.setdefault(str(key), [])
            if version and str(version) not in cited_versions[str(key)]:
                cited_versions[str(key)].append(str(version))

    gold_paths = [str(p) for p in (case.get("gold_vault_paths") or []) if p]
    cited_paths = {str(c.get("vault_path")) for c in citations if c.get("vault_path")}

    # 归因依据：每个被引用 key 的版本构成
    active_per_key: dict[str, list[str]] = {}
    suppressed: list[dict[str, Any]] = []
    for key in cited_keys:
        versions = policy_versions.get(key) or []
        active = [v for v in versions if _is_active(v)]
        active_per_key[key] = [str(v.get("version")) for v in active]
        if len(versions) > 1:
            for version in versions:
                if not _is_active(version):
                    suppressed.append({
                        "policy_key": key,
                        "version": str(version.get("version")),
                        "policy_status": version.get("policy_status"),
                        "effective_to": version.get("effective_to"),
                        "vault_path": version.get("vault_path"),
                    })

    missing_gold = [p for p in gold_paths if p not in cited_paths]

    # ── 有序判定：首个命中即主因 ──
    secondary: list[str] = []
    if result_state == "conflicting_evidence":
        primary = CONFLICT_CORRECT
    elif not citations:
        primary = RECALL_MISS
    elif not cited_keys:
        primary = METADATA_MISSING
    elif any(len(active) > 1 for active in active_per_key.values()):
        primary = VERSION_CONFLICT_UNDETECTED
    elif suppressed:
        # 有其他版本但都不是 active —— 系统按生命周期排除，属于正确行为
        primary = LIFECYCLE_FILTERED
        secondary.append(EVAL_CONTRACT_MISMATCH)
    elif detected_conflicts:
        primary = GENERATION_SUPPRESSED
    elif missing_gold:
        primary = RECALL_MISS
    else:
        primary = EVAL_CONTRACT_MISMATCH
        if len(cited_keys) > 1:
            secondary.append(CROSS_POLICY_CONFLICT_UNSUPPORTED)

    return {
        "case_id": case.get("case_id"),
        "primary_reason": primary,
        "primary_reason_label": REASON_LABELS[primary],
        "secondary_reasons": secondary,
        "is_failure": primary != CONFLICT_CORRECT,
        # 真缺陷才需要改代码；口径问题要产品拍板，不许偷偷改标签
        "is_system_defect": primary in _SYSTEM_DEFECTS,
        "expected": {
            "gold_vault_paths": gold_paths,
            "expected_behavior": case.get("expected_behavior"),
        },
        "actual": {
            "result_state": result_state,
            "cited_policy_keys": cited_keys,
            "cited_versions": cited_versions,
            "active_versions_per_key": active_per_key,
            "detected_conflicts": detected_conflicts,
        },
        "evidence": {
            "suppressed_versions": suppressed,
            "missing_gold_paths": missing_gold,
        },
    }


def attribute_all(cases, predictions, *, policy_versions) -> dict[str, Any]:
    """批量归因，按主因汇总——供 backlog 直接用。"""
    by_case = {str(p.get("case_id")): p for p in predictions}
    results = [
        attribute_conflict_case(case, by_case.get(str(case.get("case_id")), {}), policy_versions=policy_versions)
        for case in cases
        if (case.get("category") or case.get("query_type")) == "conflict"
    ]
    tally: dict[str, int] = {}
    for item in results:
        tally[item["primary_reason"]] = tally.get(item["primary_reason"], 0) + 1
    return {
        "case_count": len(results),
        "by_primary_reason": tally,
        "system_defect_count": sum(1 for r in results if r["is_system_defect"]),
        "results": results,
    }
