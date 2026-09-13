/**
 * Chat SSE 状态 reducer（UI-1 结构重构：SSE event → typed action → 单一状态）。
 *
 * 目的（UI 调研方案 §4.2）：把 ChatPage 中分散的 useState（citations/trace/
 * routeDecision/resultState/citationFidelity/steps/usage/degradedReason）
 * 收敛为单一 reducer；组件仍按属性消费同一形状，行为不变。
 * ChatPage 原有 handleEvent 内联逻辑等价迁入；assist 事件分支由
 * lib/assist-events 语义对齐（turn 内状态仍由 ChatPage patchTurn 处理——
 * 轨迹状态与轮次状态是两层，reducer 只管"实时轨道"）。
 */

import type { Citation, RetrievalTrace, RouteDecision, UsageInfo } from "../types";

export type StepState = "waiting" | "running" | "done" | "warning";

export type RailState = {
  steps: Record<string, StepState>;
  citations: Citation[];
  trace: RetrievalTrace | null;
  routeDecision: RouteDecision | null;
  resultState: string | null;
  citationFidelity: boolean | null;
  usage: UsageInfo | null;
  degradedReason: string | null;
  /** P1：模型思考流已开始（reasoning_delta 到达）——前端据此显示"思考中"，不再死寂 */
  reasoningActive: boolean;
};

export const INITIAL_RAIL: RailState = {
  steps: { scope: "waiting", retrieval: "waiting", generation: "waiting" },
  citations: [],
  trace: null,
  routeDecision: null,
  resultState: null,
  citationFidelity: null,
  usage: null,
  degradedReason: null,
  reasoningActive: false,
};

export type RailAction =
  | { type: "request_started" }
  | { type: "scope_check_completed"; outOfScope: boolean }
  | { type: "retrieval_started" }
  | { type: "retrieval_routed"; route: RouteDecision }
  | { type: "retrieval_completed" }
  | { type: "generation_started" }
  | { type: "reasoning_started" }
  | { type: "reasoning_ended" }
  | { type: "citations"; citations: Citation[] }
  | { type: "usage"; usage: UsageInfo }
  | { type: "degraded"; reason: string | null }
  | { type: "completed"; result: {
      citations: Citation[]; trace: RetrievalTrace | null; resultState: string | null;
      citationFidelity: boolean | null; usage: UsageInfo | null; degraded: string | null;
    } }
  | { type: "error"; code: string }
  | { type: "reset" };

function step(state: RailState, key: string, value: StepState): Record<string, StepState> {
  return { ...state.steps, [key]: value };
}

export function railReducer(state: RailState, action: RailAction): RailState {
  switch (action.type) {
    case "request_started":
      return { ...INITIAL_RAIL, steps: { scope: "running", retrieval: "waiting", generation: "waiting" } };
    case "scope_check_completed":
      return { ...state, steps: step(state, "scope", action.outOfScope ? "warning" : "done") };
    case "retrieval_started":
      return { ...state, steps: step(state, "retrieval", "running") };
    case "retrieval_routed":
      return { ...state, routeDecision: action.route };
    case "retrieval_completed":
      return { ...state, steps: step(state, "retrieval", "done") };
    case "generation_started":
      return { ...state, steps: step(state, "generation", "running"), reasoningActive: false };
    case "reasoning_started":
      // 思考模型（qwen3.8-flash）的 reasoning 流：可见的"思考中"，替代死寂
      return { ...state, reasoningActive: true };
    case "reasoning_ended":
      return { ...state, reasoningActive: false };
    case "citations":
      return { ...state, citations: action.citations, reasoningActive: false };
    case "usage":
      return { ...state, usage: action.usage };
    case "degraded":
      return { ...state, degradedReason: action.reason, steps: step(state, "generation", "warning") };
    case "completed":
      return {
        ...state,
        citations: action.result.citations,
        trace: action.result.trace,
        routeDecision: action.result.trace?.route_decision ?? state.routeDecision,
        resultState: action.result.resultState,
        citationFidelity: action.result.citationFidelity,
        usage: action.result.usage ?? state.usage,
        degradedReason: action.result.degraded ?? state.degradedReason,
        reasoningActive: false,
        steps: {
          scope: state.steps.scope === "running" ? "done" : state.steps.scope,
          retrieval: action.result.trace ? "done" : state.steps.retrieval,
          generation: action.result.resultState === "answered" || state.steps.generation === "done" ? "done" : state.steps.generation,
        },
      };
    case "error":
      return { ...state, resultState: action.code, steps: step(state, "retrieval", "warning") };
    case "reset":
      return INITIAL_RAIL;
  }
}
