import { afterEach, describe, expect, it, vi } from "vitest";

import { api, parseSseFrames } from "./api";

afterEach(() => vi.restoreAllMocks());

describe("parseSseFrames", () => {
  it("parses complete events and preserves an incomplete tail", () => {
    const input = [
      "event: request_started",
      'data: {"event":"request_started","data":{"strategy":"hybrid"}}',
      "",
      "event: answer_delta",
      'data: {"event":"answer_delta","data":{"text":"制度"}}',
      "",
      "event: citations",
      "data: {\"event\":\"citations\"",
    ].join("\n");

    const result = parseSseFrames(input);

    expect(result.events).toHaveLength(2);
    expect(result.events[1].data.text).toBe("制度");
    expect(result.remainder).toContain("event: citations");
  });

  it("supports CRLF frames", () => {
    const result = parseSseFrames(
      'event: completed\r\ndata: {"event":"completed","data":{"request_id":"r1"}}\r\n\r\n',
    );

    expect(result.events[0].event).toBe("completed");
    expect(result.events[0].data.request_id).toBe("r1");
  });
});

describe("governance mutations", () => {
  it("parses the API error envelope without losing conflict guidance", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({
        error: { code: "governance_conflict", message: "governance case status changed", request_id: "r1" },
      }), { status: 409 }),
    );
    await expect(api.governanceCases()).rejects.toMatchObject({
      message: "governance case status changed",
      status: 409,
      code: "governance_conflict",
      requestId: "r1",
    });
  });

  it("never sends actor identity when resolving", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "rejected" }), { status: 200 }),
    );
    await api.resolveGovernanceCase("case-1", "proposed", "reject");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({ expected_status: "proposed", decision: "reject" });
    expect(String(init.body)).not.toContain("actor");
    expect(String(init.body)).not.toContain("resolved_by");
  });

  it("sends only expected state when revoking", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "revoked" }), { status: 200 }),
    );
    await api.revokeGovernanceCase("case-1", "confirmed");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({ expected_status: "confirmed" });
  });
});
