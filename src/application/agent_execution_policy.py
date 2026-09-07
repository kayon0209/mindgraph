"""AgentExecutionPolicy：route/reason_codes/result_state → 确定性步骤（M2，实施方案 §4.1）。

不使用 LLM 自主规划。策略把 Router 的路由结果映射为**静态步骤序列**；
AgentService 按序执行，实际工具调用数永远 ≤ AGENT_MAX_TOOL_CALLS。

步骤定义（用户语言名对应 docs/ui/AGENT-UI-COPY-DECK.md §1 步骤词典）：
- retrieve_evidence：经 EvidenceQueryService 检索（第 1 步恒有）
- resolve_version：精确取候选笔记并复核版本（structured_fallback+version_constraint）
- expand_relations：可选 confirmed 关系扩展（exception_or_conflict/cross_policy）
- finalize：进入生成 + 引用完整性校验（不计入工具步数）

终止条件（fail-closed，任一命中即停）：
- 无证据 / 权限不足（bundle.result_state ∈ {insufficient_evidence, permission_denied}）
- 冲突（conflicting_evidence）：第一次冲突检查后不得再扩图或生成
- 超出工具预算：发 loop_fell_back，回到单遍回答（不是继续执行）
"""

from __future__ import annotations

from dataclasses import dataclass, field

from domain.evidence import EvidenceResultState

# 与 docs/ui/AGENT-UI-COPY-DECK.md 步骤词典一致的用户语言标签
STEP_LABELS: dict[str, str] = {
    "retrieve_evidence": "查找制度证据",
    "resolve_version": "定位指定版本",
    "check_conflicts": "核对版本有效期",
    "expand_relations": "对比关联制度",
    "finalize": "生成并核对回答",
}


@dataclass(frozen=True)
class ExecutionStep:
    name: str
    label: str
    counts_toward_budget: bool


@dataclass(frozen=True)
class ExecutionPlan:
    steps: tuple[ExecutionStep, ...]
    # 冲突检测在步骤序列中的强制位置（该步之后不得扩图/生成）
    conflict_gate_after: str | None = None

    def budget_steps(self) -> tuple[ExecutionStep, ...]:
        return tuple(step for step in self.steps if step.counts_toward_budget)

    def labels(self) -> list[str]:
        return [step.label for step in self.steps]


def _step(name: str, *, budget: bool = True) -> ExecutionStep:
    return ExecutionStep(name=name, label=STEP_LABELS[name], counts_toward_budget=budget)


class AgentExecutionPolicy:
    """路由 → 静态步骤序列的纯映射。不做检索、不做生成。"""

    def plan_for_route(self, route_name: str, reason_codes: list[str] | tuple[str, ...]) -> ExecutionPlan:
        codes = set(reason_codes or ())
        if route_name == "clarification_required":
            # 澄清：不检索、不生成，只有结构化提问步骤（生成澄清问题不计工具预算）
            return ExecutionPlan(steps=(_step("finalize", budget=False),))
        if route_name == "manual":
            # 手动策略：保持现有单遍行为，不加工具步
            return ExecutionPlan(steps=(_step("retrieve_evidence"), _step("finalize", budget=False)))
        if route_name in {"factual", "exact_title"}:
            return ExecutionPlan(
                steps=(
                    _step("retrieve_evidence"),
                    _step("check_conflicts"),
                    _step("finalize", budget=False),
                ),
                conflict_gate_after="check_conflicts",
            )
        if route_name == "structured_fallback" and "version_constraint" in codes:
            return ExecutionPlan(
                steps=(
                    _step("retrieve_evidence"),
                    _step("resolve_version"),
                    _step("check_conflicts"),
                    _step("finalize", budget=False),
                ),
                conflict_gate_after="check_conflicts",
            )
        if route_name in {"exception_or_conflict", "cross_policy"}:
            # 方案 §4.1：base search→冲突检查→可选 confirmed relation expansion→
            # 去重/重排→再次冲突检查→generate。第一次冲突检查后不得再扩图。
            return ExecutionPlan(
                steps=(
                    _step("retrieve_evidence"),
                    _step("check_conflicts"),
                    _step("expand_relations"),
                    _step("finalize", budget=False),
                ),
                conflict_gate_after="check_conflicts",
            )
        # structured_fallback（无版本约束）等其余路由：单遍 + 冲突检查
        return ExecutionPlan(
            steps=(
                _step("retrieve_evidence"),
                _step("check_conflicts"),
                _step("finalize", budget=False),
            ),
            conflict_gate_after="check_conflicts",
        )

    @staticmethod
    def should_halt(result_state: EvidenceResultState, *, gate_reached: bool) -> bool:
        """步骤间的 fail-closed 判定。

        gate_reached：本轮是否已过冲突检查步。冲突后不再扩图/生成；
        无证据/无权限在任意步直接停。
        """
        if result_state in {EvidenceResultState.insufficient_evidence, EvidenceResultState.permission_denied}:
            return True
        if result_state is EvidenceResultState.conflicting_evidence and gate_reached:
            return True
        return False
