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

describe("材料上传走版本化生命周期（P4）", () => {
  const versionRecord = (status: string, version = "v1") => ({
    document_id: "doc-abc",
    logical_document_id: "doc-1a2b3c4d",
    version,
    title: "差旅费管理办法",
    status,
  });

  it("上传 → 两次状态流转 → 返回 active 记录（缺任何一跳都进不了索引）", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify(versionRecord("draft")), { status: 201, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(versionRecord("pending_index")), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(versionRecord("active")), { status: 200, headers: { "Content-Type": "application/json" } }));

    const file = new File(["# 差旅费"], "差旅费管理办法.md", { type: "text/markdown" });
    const record = await api.uploadDocumentVersion(file, "upload");

    expect(record.status).toBe("active");
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls[0]).toContain("/knowledge/versions");
    expect(urls[1]).toContain("target=pending_index");
    expect(urls[2]).toContain("target=active");
    // 中文文件名必须被 slug 化：后端 _SAFE_SEGMENT 只接受 ASCII
    const firstBody = fetchMock.mock.calls[0][1]?.body as FormData;
    expect(String(firstBody.get("logical_document_id"))).toMatch(/^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/);
    expect(firstBody.get("authority_level")).toBe("user_uploaded_reference");
  });

  it("解析未通过（parse_failed）不做流转：把真实原因交回调用方", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ ...versionRecord("parse_failed"), parsing_diagnostics: { ocr_required_pages: [2] } }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const record = await api.uploadDocumentVersion(new File(["x"], "scan.pdf"), "upload");

    expect(record.status).toBe("parse_failed");
    expect(fetchMock).toHaveBeenCalledTimes(1); // 没有去撞 409
  });

  it("同名同版本冲突时用内容派生版本重试一次，不把死路留给用户", async () => {
    const conflict = new Response(
      JSON.stringify({ error: { code: "conflict", message: "Document version already exists" } }),
      { status: 409, headers: { "Content-Type": "application/json" } },
    );
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(conflict)
      .mockResolvedValueOnce(new Response(JSON.stringify(versionRecord("draft", "u20260101T000000s1")), { status: 201, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(versionRecord("pending_index")), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(versionRecord("active")), { status: 200, headers: { "Content-Type": "application/json" } }));

    const file = new File(["updated"], "policy.md", { type: "text/markdown" });
    const record = await api.uploadDocumentVersion(file, "upload");

    expect(fetchMock).toHaveBeenCalledTimes(4);
    const retryBody = fetchMock.mock.calls[1][1]?.body as FormData;
    expect(String(retryBody.get("version"))).not.toBe("v1");
    expect(record.status).toBe("active");
  });

  it("业务错误体（error.code/message）不再是 HTTP 状态文本", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ error: { code: "index_consistency_blocked", message: "index activation blocked: chunking_changed；确认要切换请带 force=true 重试" } }),
        { status: 409, headers: { "Content-Type": "application/json" } },
      ),
    );

    const failure = await api.incrementalRebuild().catch((caught: unknown) => caught);

    expect(failure).toMatchObject({ status: 409, code: "index_consistency_blocked" });
    expect((failure as Error).message).toContain("force=true");
  });

  it("force 走 query string（空 body POST 也要能过）", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ index_version: "m4-x" }), { status: 200, headers: { "Content-Type": "application/json" } }),
    );

    await api.incrementalRebuild(true);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("force=true");
    expect(init.method).toBe("POST");
  });
});
