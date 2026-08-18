import { describe, expect, it } from "vitest";

import { routeDecisionView } from "./route-decision";
import type { RouteDecision } from "../types";


describe("adaptive route decision presentation", () => {
  it("labels a structured fallback without pretending structured retrieval exists", () => {
    const decision: RouteDecision = {
      mode: "adaptive",
      route: "structured_fallback",
      requested_strategy: "auto",
      selected_strategy: "hybrid",
      graph_enabled: false,
      reasons: ["structured_clause_store_unavailable"],
    };

    expect(routeDecisionView(decision)).toEqual({
      routeLabel: "结构化能力回退",
      strategyLabel: "Hybrid",
      graphLabel: "无需关系扩展",
      reasonLabels: ["条款级结构化存储尚未完成，使用 Hybrid 回退"],
      costTierLabel: "中等成本",
      latencyTierLabel: "中等延迟",
      degraded: false,
    });
  });

  it("keeps manual selection visibly distinct from adaptive routing", () => {
    expect(routeDecisionView({
      mode: "manual",
      route: "manual",
      requested_strategy: "bm25",
      selected_strategy: "bm25",
      graph_enabled: false,
      reasons: ["user_selected_strategy"],
    })).toMatchObject({
      routeLabel: "手动策略",
      strategyLabel: "BM25",
      reasonLabels: ["按用户指定策略执行"],
    });
  });
});
