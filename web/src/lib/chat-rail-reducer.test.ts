/**
 * UI-1 结构重构测试：SSE reducer 语义与 ChatPage.handleEvent 等价性。
 *
 * 每个 action 对应 ChatPage 原内联分支；序列驱动（request→…→completed）
 * 断言与既有行为一致——重构护栏。
 */

import { describe, expect, it } from "vitest";

import { INITIAL_RAIL, railReducer, type RailState } from "./chat-rail-reducer";

function apply(events: Parameters<typeof railReducer>[1][], start: RailState = INITIAL_RAIL): RailState {
  return events.reduce((state, action) => railReducer(state, action), start);
}

describe("chat rail reducer（UI-1）", () => {
  it("request_started 重置为 scope running", () => {
    const next = railReducer(INITIAL_RAIL, { type: "request_started" });
    expect(next.steps.scope).toBe("running");
    expect(next.steps.retrieval).toBe("waiting");
    expect(next.citations).toEqual([]);
  });

  it("完整正常流：scope→retrieval→generation→completed", () => {
    const final = apply([
      { type: "request_started" },
      { type: "scope_check_completed", outOfScope: false },
      { type: "retrieval_routed", route: { route: "factual", mode: "adaptive", strategy: "hybrid", reasons: [], graph_enabled: false } as never },
      { type: "retrieval_started" },
      { type: "retrieval_completed" },
      { type: "generation_started" },
      {
        type: "completed",
        result: {
          citations: [{ citation_id: "citation-1" }] as never,
          trace: { route_decision: { route: "factual" } } as never,
          resultState: "answered",
          citationFidelity: true,
          usage: { total_tokens: 10 },
          degraded: null,
        },
      },
    ]);
    expect(final.steps).toEqual({ scope: "done", retrieval: "done", generation: "done" });
    expect(final.resultState).toBe("answered");
    expect(final.citationFidelity).toBe(true);
    expect(final.routeDecision?.route).toBe("factual");
    expect(final.usage?.total_tokens).toBe(10);
  });

  it("degraded：原因记录 + generation 置 warning", () => {
    const next = apply([
      { type: "request_started" },
      { type: "degraded", reason: "provider_not_configured" },
    ]);
    expect(next.degradedReason).toBe("provider_not_configured");
    expect(next.steps.generation).toBe("warning");
  });

  it("error：resultState 记码 + retrieval 警告（重试入口语义不变）", () => {
    const next = apply([{ type: "request_started" }, { type: "error", code: "retrieval_unavailable" }]);
    expect(next.resultState).toBe("retrieval_unavailable");
    expect(next.steps.retrieval).toBe("warning");
  });

  it("completed 保留流中已到达的 usage（usage 事件先到）", () => {
    const next = apply([
      { type: "request_started" },
      { type: "usage", usage: { total_tokens: 42 } },
      { type: "completed", result: { citations: [], trace: null, resultState: "answered", citationFidelity: null, usage: null, degraded: null } },
    ]);
    expect(next.usage?.total_tokens).toBe(42);
  });

  it("reset 回到初始", () => {
    const next = apply([{ type: "request_started" }, { type: "reset" }]);
    expect(next).toEqual(INITIAL_RAIL);
  });
});
