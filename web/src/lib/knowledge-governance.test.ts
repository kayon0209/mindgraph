import { describe, expect, it } from "vitest";

import { governanceCaseView, governanceFailureView } from "./knowledge-governance";

describe("governanceCaseView", () => {
  it("uses safe labels and capability-derived actions", () => {
    const view = governanceCaseView({
      case_id: "case-1",
      case_type: "version_conflict",
      policy_key: "travel-expense",
      status: "proposed",
      canonical_note_id: null,
      reason_code: "overlapping_effective_intervals",
      evidence_ids: ["note-1"],
      participants: [{
        note_id: "note-1", participant_role: "candidate", document_version: "1.0",
        effective_from: "2026-01-01", effective_to: null,
      }],
      created_at: "2026-08-25T00:00:00Z",
      updated_at: "2026-08-25T00:00:00Z",
      resolved_at: null,
      capabilities: { can_resolve: false, can_revoke: false },
    });
    expect(view.reasonLabel).toBe("有效期重叠");
    expect(view.actions).toEqual([]);
  });

  it("exposes only server-authorized actions", () => {
    const view = governanceCaseView({
      case_id: "case-2", case_type: "exact_duplicate", policy_key: null,
      status: "proposed", canonical_note_id: null, reason_code: "exact_duplicate_equivalent",
      evidence_ids: [], participants: [], created_at: "", updated_at: "", resolved_at: null,
      capabilities: { can_resolve: true, can_revoke: false },
    });
    expect(view.actions).toEqual(["confirm", "reject"]);
  });

  it("refreshes stale decisions but preserves the list for transient errors", () => {
    const conflict = governanceFailureView({ status: 409, message: "stale" });
    expect(conflict).toEqual({
      refresh: true,
      message: "事项状态已变化，列表已刷新；请基于最新状态重试。",
    });
    expect(conflict.message).not.toContain("已保留");
    const transient = governanceFailureView({ status: 503, message: "temporarily unavailable" });
    expect(transient).toEqual({
      refresh: false,
      message: "temporarily unavailable；当前列表已保留，可重试。",
    });
  });

  it("labels checksum review with the real backend reason code", () => {
    const view = governanceCaseView({
      case_id: "case-3", case_type: "version_conflict", policy_key: null,
      status: "proposed", canonical_note_id: null, reason_code: "checksum_match_requires_review",
      evidence_ids: [], participants: [], created_at: "", updated_at: "", resolved_at: null,
      capabilities: { can_resolve: false, can_revoke: false },
    });
    expect(view.reasonLabel).toBe("校验和一致，需人工核验");
  });
});
