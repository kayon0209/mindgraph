import { describe, expect, it } from "vitest";

import type { EvaluationRun } from "../types";
import * as metricHelpers from "./metrics";

const {
  evaluationEfficiencyView,
  latestRunPerStrategy,
  latestRunsForMetric,
  latestRunWithMetric,
  metricValue,
  numericMetricValue,
} = metricHelpers;

type GovernanceInput = {
  owner: string | null;
  version: string | null;
  effective_from: string | null;
  effective_to: string | null;
  policy_status: string;
  metadata_complete: boolean;
  issues: string[];
};

const policyGovernanceView = (
  metricHelpers as unknown as {
    policyGovernanceView?: (value: GovernanceInput) => unknown;
  }
).policyGovernanceView;

const run = (strategy: string, value: number): EvaluationRun => ({
  run_id: `${strategy}-${value}`,
  status: "completed",
  dataset: "enterprise-v2",
  strategy,
  metrics: { recall_at_5: value },
});

describe("evaluation metric helpers", () => {
  it("keeps the latest run for each strategy", () => {
    expect(latestRunPerStrategy([run("hybrid", 0.9), run("hybrid", 0.8), run("bm25", 0.7)])).toHaveLength(2);
  });

  it("clamps invalid metric values for visual bars", () => {
    expect(metricValue(run("hybrid", 1.2), "recall_at_5")).toBe(1);
    expect(metricValue(run("hybrid", -0.2), "recall_at_5")).toBe(0);
    expect(metricValue(run("hybrid", 0.8), "missing")).toBeNull();
  });

  it("finds the latest completed ledger entry that contains an answer metric", () => {
    const retrieval = run("hybrid", 0.9);
    const failedAnswerRun: EvaluationRun = {
      ...run("answer-eval", 0),
      status: "failed",
      metrics: { citation_correctness: 0.2 },
    };
    const answerRun: EvaluationRun = {
      ...run("answer-eval", 0),
      metrics: { citation_correctness: 0.8 },
    };

    expect(latestRunWithMetric([retrieval, failedAnswerRun, answerRun], "citation_correctness"))?.toBe(answerRun);
    expect(latestRunWithMetric([retrieval], "citation_correctness")).toBeNull();
  });

  it("does not let a newer run without recall hide an older retrieval run", () => {
    const answerOnly: EvaluationRun = {
      ...run("hybrid", 0),
      run_id: "hybrid-answer-only",
      metrics: { citation_correctness: 0.8 },
    };
    const retrieval = run("hybrid", 0.9);

    expect(latestRunsForMetric([answerOnly, retrieval], "recall_at_5")).toEqual([retrieval]);
  });

  it("keeps operational metrics unbounded and formats cost with coverage", () => {
    const efficiencyRun: EvaluationRun = {
      ...run("answer_eval_hybrid", 0),
      metrics: {
        p95_total_latency_ms: 1250.4,
        mean_total_tokens: 321.2,
        mean_estimated_cost: 0.012345,
        cost_coverage: 0.5,
        cost_currency: "USD",
      },
    };

    expect(numericMetricValue(efficiencyRun, "p95_total_latency_ms")).toBe(1250.4);
    expect(evaluationEfficiencyView(efficiencyRun)).toEqual({
      p95Latency: "1250 ms",
      meanTokens: "321",
      meanCost: "USD 0.0123",
      costCoverage: "50.0%",
    });
    expect(evaluationEfficiencyView(null)).toEqual({
      p95Latency: "—",
      meanTokens: "—",
      meanCost: "—",
      costCoverage: "—",
    });
  });
});

describe("policy governance view", () => {
  it("distinguishes an active governed policy from an incomplete record", () => {
    expect(policyGovernanceView?.({
      owner: "财务部",
      version: "3.0",
      effective_from: "2026-08-01",
      effective_to: null,
      policy_status: "active",
      metadata_complete: true,
      issues: [],
    })).toEqual({
      statusLabel: "在行",
      statusTone: "positive",
      completenessLabel: "元数据完整",
      period: "2026-08-01 → 长期有效",
      issueLabels: [],
    });

    expect(policyGovernanceView?.({
      owner: null,
      version: null,
      effective_from: "2027-12-31",
      effective_to: "2027-01-01",
      policy_status: "unspecified",
      metadata_complete: false,
      issues: ["missing_owner", "missing_version", "invalid_effective_range"],
    })).toEqual({
      statusLabel: "状态未治理",
      statusTone: "negative",
      completenessLabel: "3 项待治理",
      period: "2027-12-31 → 2027-01-01",
      issueLabels: ["缺少责任部门", "缺少版本号", "生效区间无效"],
    });
  });
});
