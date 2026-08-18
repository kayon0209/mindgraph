import { describe, expect, it } from "vitest";

import { completionGenerationState, policyConflictItems } from "./policy-conflicts";
import type { AnswerResult, RetrievalTrace } from "../types";


const trace: RetrievalTrace = {
  requested_strategy: "hybrid",
  actual_strategy: "hybrid",
  candidate_counts: {},
  stage_latency_ms: {},
  degraded: false,
  graph_enabled: false,
  graph_links: [],
  policy_conflicts: [
    {
      policy_key: "expense.general",
      as_of: "2026-08-18",
      versions: [
        {
          note_id: "v2",
          title: "费用制度 V2",
          vault_path: "policies/expense-v2.md",
          version: "2.0",
          effective_from: "2026-07-01",
          effective_to: null,
          policy_status: "active",
          owner: "财务部",
        },
        {
          note_id: "v3",
          title: "费用制度 V3",
          vault_path: "policies/expense-v3.md",
          version: "3.0",
          effective_from: "2026-08-01",
          effective_to: null,
          policy_status: "active",
          owner: "财务部",
        },
      ],
    },
  ],
};

describe("policy conflict presentation", () => {
  it("keeps deterministic conflict refusal in a warning state", () => {
    expect(completionGenerationState({ result_state: "conflicting_evidence", degraded: false } as AnswerResult)).toBe("warning");
    expect(completionGenerationState({ result_state: "answered", degraded: false } as AnswerResult)).toBe("done");
  });

  it("flattens every conflicting version without hiding its effective period", () => {
    expect(policyConflictItems(trace)).toEqual([
      {
        key: "expense.general-v2",
        policyKey: "expense.general",
        asOf: "2026-08-18",
        title: "费用制度 V2",
        version: "2.0",
        period: "2026-07-01 → 长期有效",
        owner: "财务部",
        vaultPath: "policies/expense-v2.md",
      },
      {
        key: "expense.general-v3",
        policyKey: "expense.general",
        asOf: "2026-08-18",
        title: "费用制度 V3",
        version: "3.0",
        period: "2026-08-01 → 长期有效",
        owner: "财务部",
        vaultPath: "policies/expense-v3.md",
      },
    ]);
  });
});
