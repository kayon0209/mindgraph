"""AgentExecutionPolicy 测试（M2）：路由→步骤映射与 fail-closed 判定。

覆盖实施方案 §4.1 表格的每一行 + 预算上限 + 澄清特例 + 手动策略不变。
"""

from __future__ import annotations

from application.agent_execution_policy import STEP_LABELS, AgentExecutionPolicy
from domain.evidence import EvidenceResultState

POLICY = AgentExecutionPolicy()


def _names(plan):
    return [step.name for step in plan.steps]


def test_clarification_route_has_no_retrieval_and_no_budget():
    plan = POLICY.plan_for_route("clarification_required", [])
    assert _names(plan) == ["finalize"]
    assert plan.budget_steps() == ()  # 生成澄清问题不计工具预算


def test_factual_route_is_search_conflict_generate():
    plan = POLICY.plan_for_route("factual", ["default_factual_query"])
    assert _names(plan) == ["retrieve_evidence", "check_conflicts", "finalize"]
    assert plan.conflict_gate_after == "check_conflicts"
    assert len(plan.budget_steps()) == 2  # retrieve + conflict check


def test_structured_fallback_with_version_constraint_adds_version_resolution():
    plan = POLICY.plan_for_route("structured_fallback", ["structured_clause_query_selected", "version_constraint"])
    assert _names(plan) == ["retrieve_evidence", "resolve_version", "check_conflicts", "finalize"]
    assert len(plan.budget_steps()) == 3


def test_structured_fallback_without_version_constraint_stays_single_pass():
    plan = POLICY.plan_for_route("structured_fallback", ["structured_clause_query_selected"])
    assert "resolve_version" not in _names(plan)


def test_exception_route_expands_relations_after_conflict_gate():
    plan = POLICY.plan_for_route("exception_or_conflict", ["exception_or_conflict_terms"])
    assert _names(plan) == ["retrieve_evidence", "check_conflicts", "expand_relations", "finalize"]
    # 冲突门在扩图之前：第一次冲突检查后不得再扩图
    assert plan.steps.index(_named(plan, "check_conflicts")) < plan.steps.index(_named(plan, "expand_relations"))


def test_cross_policy_route_same_as_exception():
    plan = POLICY.plan_for_route("cross_policy", ["cross_policy_terms"])
    assert "expand_relations" in _names(plan)


def test_manual_strategy_keeps_single_pass():
    plan = POLICY.plan_for_route("manual", ["user_selected_strategy"])
    assert _names(plan) == ["retrieve_evidence", "finalize"]
    assert len(plan.budget_steps()) == 1


def test_exact_title_same_as_factual():
    plan = POLICY.plan_for_route("exact_title", ["explicit_document_title"])
    assert _names(plan) == ["retrieve_evidence", "check_conflicts", "finalize"]


def test_every_route_plan_stays_within_three_tool_calls():
    for route in ("factual", "exact_title", "structured_fallback", "exception_or_conflict", "cross_policy", "manual", "clarification_required"):
        for codes in ([], ["version_constraint"]):
            plan = POLICY.plan_for_route(route, codes)
            assert len(plan.budget_steps()) <= 3, f"{route}/{codes} exceeds budget"


def test_halt_rules_fail_closed():
    # 无证据/无权限：任意步直接停
    assert POLICY.should_halt(EvidenceResultState.insufficient_evidence, gate_reached=False) is True
    assert POLICY.should_halt(EvidenceResultState.permission_denied, gate_reached=True) is True
    # 冲突：过门后必停
    assert POLICY.should_halt(EvidenceResultState.conflicting_evidence, gate_reached=True) is True
    assert POLICY.should_halt(EvidenceResultState.conflicting_evidence, gate_reached=False) is False
    # 正常路径不停
    assert POLICY.should_halt(EvidenceResultState.evidence_found, gate_reached=True) is False


def test_step_labels_use_user_language():
    # 文案表（docs/ui/AGENT-UI-COPY-DECK.md §1）的唯一后端来源
    assert STEP_LABELS["retrieve_evidence"] == "查找制度证据"
    assert STEP_LABELS["resolve_version"] == "定位指定版本"
    assert STEP_LABELS["check_conflicts"] == "核对版本有效期"
    assert STEP_LABELS["expand_relations"] == "对比关联制度"
    assert STEP_LABELS["finalize"] == "生成并核对回答"


def _named(plan, name):
    return next(step for step in plan.steps if step.name == name)
