import { afterEach, describe, expect, it, vi } from "vitest";

import { api, assistAgentEnabled, parseSseFrames, streamAssistAgent } from "./api";

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

  it("skips a malformed complete frame and continues with later events", () => {
    const result = parseSseFrames([
      'event: answer_delta\ndata: {"event":"answer_delta","data":{"text":"前"}}',
      'event: answer_delta\ndata: {not-json}',
      'event: answer_delta\ndata: {"event":"answer_delta","data":{"text":"后"}}',
      "",
    ].join("\n\n"));

    expect(result.events.map((event) => event.data.text)).toEqual(["前", "后"]);
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

describe("assist availability probe hygiene (P0-1 follow-up)", () => {
  it("reads assist_agent_enabled from /config/public and never POSTs the agent stream endpoint", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ assist_agent_enabled: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const enabled = await assistAgentEnabled();

    expect(enabled).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/config/public");
    // 探测只读配置：不得对 agent stream 端点发起 POST（会触发真实执行与审计噪声）
    expect(url).not.toContain("/assist/agent/stream");
    expect(init.method).toBeUndefined(); // GET
  });

  it("returns false when the flag is off or the request fails", async () => {
    const off = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ assist_agent_enabled: false }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    expect(await assistAgentEnabled()).toBe(false);
    off.mockRestore();

    const failing = vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("offline"));
    expect(await assistAgentEnabled()).toBe(false);
    failing.mockRestore();
  });
});

describe("streamAssistAgent payload hygiene (P0-1)", () => {
  it("never sends resume_from — clarification submit is a fresh question request", async () => {
    const encoder = new TextEncoder();
    const frames = [
      'event: completed\ndata: {"event":"completed","data":{"result_state":"answered"}}',
      "",
    ].join("\n");
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(frames));
        controller.close();
      },
    });
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );

    await streamAssistAgent(
      // 后端 AssistRequest 没有 resume_from 字段：即使调用方误传，也不得进入请求体
      {
        question: "差旅餐补（补充：2026-07 之后）",
        retrieval_strategy: "hybrid",
        final_top_k: 5,
        include_retrieval_trace: true,
        include_historical: false,
        graph_enabled: false,
      },
      () => {},
    );

    const request = fetchMock.mock.calls[0][1] as RequestInit;
    const sent = JSON.parse(String(request.body)) as Record<string, unknown>;
    expect(sent.question).toContain("补充：");
    expect("resume_from" in sent).toBe(false);
    expect("clarification_answers" in sent).toBe(false);
  });
});
