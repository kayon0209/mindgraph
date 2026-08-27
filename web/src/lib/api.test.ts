import { afterEach, describe, expect, it, vi } from "vitest";

import { api, parseSseFrames } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
});

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

describe("relation review API", () => {
  it("sends a review reason without a client-controlled reviewer identity", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ ok: true, status: "confirmed" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await api.resolveRelation("rel-1", "confirm", "证据与关系类型一致");

    const request = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(request.body))).toEqual({
      decision: "confirm",
      reason: "证据与关系类型一致",
    });
  });
});
