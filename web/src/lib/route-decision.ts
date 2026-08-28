import type { RouteDecision } from "../types";

const ROUTE_LABELS: Record<string, string> = {
  manual: "手动策略",
  factual: "事实问答",
  exact_title: "明确标题检索",
  exception_or_conflict: "例外 / 冲突核对",
  cross_policy: "跨制度核对",
  structured_fallback: "结构化能力回退",
  clarification_required: "歧义 / 复合问题",
};

const STRATEGY_LABELS: Record<string, string> = {
  auto: "自动匹配",
  dense: "语义检索",
  bm25: "关键词检索",
  hybrid: "混合检索",
  hybrid_rerank: "混合检索 + 精排",
};

const REASON_LABELS: Record<string, string> = {
  user_selected_strategy: "按用户指定策略执行",
  default_factual_query: "普通事实问题，避免无必要的重排和关系扩展",
  explicit_document_title: "检测到明确制度标题，优先精确词项检索",
  exception_or_conflict_terms: "检测到例外或冲突核对需求",
  cross_policy_terms: "检测到跨制度组合判断需求",
  structured_clause_query_selected: "检测到结构化条款条件，选择 Hybrid 检索",
  router_fallback: "路由置信不足，回退到 Hybrid",
  version_constraint: "检测到版本或生效日期约束",
  graph_expansion_disabled_by_request: "本次请求已关闭关系扩展",
  compound_question_requires_decomposition: "复合问题，建议拆解后再检索",
};

const COST_TIER_LABELS: Record<string, string> = {
  low: "低成本",
  medium: "中等成本",
  high: "高成本",
  variable: "用户指定",
};

const LATENCY_TIER_LABELS: Record<string, string> = {
  low: "低延迟",
  medium: "中等延迟",
  high: "高延迟",
  variable: "用户指定",
};

export function routeDecisionView(decision: RouteDecision) {
  return {
    routeLabel: ROUTE_LABELS[decision.route] ?? decision.route,
    strategyLabel: STRATEGY_LABELS[decision.selected_strategy] ?? decision.selected_strategy,
    graphLabel: decision.graph_enabled ? "启用受控关系扩展" : "无需关系扩展",
    reasonLabels: decision.reasons.map((reason) => REASON_LABELS[reason] ?? reason),
    costTierLabel: COST_TIER_LABELS[decision.estimated_cost_tier ?? "medium"] ?? "中等成本",
    latencyTierLabel: LATENCY_TIER_LABELS[decision.estimated_latency_tier ?? "medium"] ?? "中等延迟",
    degraded: decision.degraded ?? false,
  };
}

