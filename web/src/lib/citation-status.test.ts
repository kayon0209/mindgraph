import { describe, expect, it } from "vitest";

import { fidelityMissingMarks } from "./citation-status";

describe("fidelityMissingMarks", () => {
  it("extracts missing marks from backend warning format", () => {
    expect(fidelityMissingMarks(["citation_fidelity:missing_marks=9,12"])).toBe("[citation-9]、[citation-12]");
  });

  it("returns empty when no fidelity warning present", () => {
    expect(fidelityMissingMarks(["degraded:embedding_unavailable"])).toBe("");
    expect(fidelityMissingMarks(undefined)).toBe("");
    expect(fidelityMissingMarks([])).toBe("");
  });

  it("ignores unrelated prefix lookalikes", () => {
    expect(fidelityMissingMarks(["citation_fidelity_passed:9"])).toBe("");
  });

  it("handles single and empty mark lists", () => {
    expect(fidelityMissingMarks(["citation_fidelity:missing_marks=3"])).toBe("[citation-3]");
    expect(fidelityMissingMarks(["citation_fidelity:missing_marks="])).toBe("");
  });
});
