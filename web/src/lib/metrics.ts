import type { EvaluationRun, PolicyGovernance } from "../types";

export const METRIC_DEFINITIONS = [
  { key: "recall_at_5", label: "Recall@5" },
  { key: "mrr", label: "MRR" },
  { key: "document_hit_rate", label: "文档命中率" },
  { key: "chunk_hit_rate", label: "分块命中率" },
  { key: "route_accuracy", label: "路由准确率" },
  { key: "graph_policy_accuracy", label: "图路径策略准确率" },
  { key: "mean_total_latency_ms", label: "平均总延迟" },
  { key: "p95_total_latency_ms", label: "P95 总延迟" },
] as const;

export function numericMetricValue(run: EvaluationRun, key: string): number | null {
  const raw = run.metrics[key];
  if (typeof raw !== "number" || !Number.isFinite(raw)) return null;
  return raw;
}

export function metricValue(run: EvaluationRun, key: string): number | null {
  const raw = numericMetricValue(run, key);
  return raw === null ? null : Math.max(0, Math.min(1, raw));
}

export function latestRunPerStrategy(runs: EvaluationRun[]): EvaluationRun[] {
  const seen = new Set<string>();
  return runs.filter((run) => {
    if (seen.has(run.strategy)) return false;
    seen.add(run.strategy);
    return true;
  });
}

export function latestRunWithMetric(runs: EvaluationRun[], key: string): EvaluationRun | null {
  return runs.find((run) => run.status === "completed" && numericMetricValue(run, key) !== null) ?? null;
}

export function latestRunsForMetric(runs: EvaluationRun[], key: string): EvaluationRun[] {
  return latestRunPerStrategy(runs.filter((run) => metricValue(run, key) !== null));
}

export function evaluationEfficiencyView(run: EvaluationRun | null) {
  if (!run) {
    return { p95Latency: "—", meanTokens: "—", meanCost: "—", costCoverage: "—" };
  }
  const latency = numericMetricValue(run, "p95_total_latency_ms");
  const tokens = numericMetricValue(run, "mean_total_tokens");
  const cost = numericMetricValue(run, "mean_estimated_cost");
  const coverage = metricValue(run, "cost_coverage");
  const currency = typeof run.metrics.cost_currency === "string" ? run.metrics.cost_currency : null;
  return {
    p95Latency: latency === null ? "—" : `${latency.toFixed(0)} ms`,
    meanTokens: tokens === null ? "—" : tokens.toFixed(0),
    meanCost: cost === null || !currency ? "—" : `${currency} ${cost.toFixed(4)}`,
    costCoverage: coverage === null ? "—" : `${(coverage * 100).toFixed(1)}%`,
  };
}

const POLICY_STATUS_LABELS: Record<string, { label: string; tone: "positive" | "negative" | "neutral" }> = {
  active: { label: "生效中", tone: "positive" },
  draft: { label: "草案", tone: "neutral" },
  archived: { label: "已归档", tone: "neutral" },
  expired: { label: "已失效", tone: "negative" },
  superseded: { label: "已被替代", tone: "negative" },
  unspecified: { label: "状态未治理", tone: "negative" },
};

const POLICY_ISSUE_LABELS: Record<string, string> = {
  missing_policy_key: "缺少制度族标识",
  missing_owner: "缺少责任部门",
  missing_version: "缺少版本号",
  missing_effective_from: "缺少生效日期",
  missing_policy_status: "缺少制度状态",
  invalid_policy_status: "制度状态无效",
  invalid_effective_range: "生效区间无效",
  invalid_metadata_issues: "治理数据损坏",
};

export function policyGovernanceView(value: PolicyGovernance) {
  const status = POLICY_STATUS_LABELS[value.policy_status] ?? POLICY_STATUS_LABELS.unspecified;
  return {
    statusLabel: status.label,
    statusTone: status.tone,
    completenessLabel: value.metadata_complete ? "元数据完整" : `${value.issues.length} 项待治理`,
    period: `${value.effective_from ?? "未设置"} → ${value.effective_to ?? "长期有效"}`,
    issueLabels: value.issues.map((issue) => POLICY_ISSUE_LABELS[issue] ?? issue),
  };
}
