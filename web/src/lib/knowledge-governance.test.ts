import { describe, expect, it } from "vitest";

import { governanceCaseView } from "./knowledge-governance";

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
      status: "proposed", canonical_note_id: null, reason_code: "exact_duplicate",
      evidence_ids: [], participants: [], created_at: "", updated_at: "", resolved_at: null,
      capabilities: { can_resolve: true, can_revoke: false },
    });
    expect(view.actions).toEqual(["confirm", "reject"]);
  });
});
