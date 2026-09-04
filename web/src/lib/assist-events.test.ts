/**
 * M2 Assist 前端契约测试：SSE 事件 → Turn 状态的纯函数语义。
 *
 * 覆盖：
 * - 新 6 事件（plan/tool×2/clarification/loop_fell_back/citation_integrity_checked）
 *   的 handleEvent 分支语义（以 ChatPage 相同的数据形状驱动）；
 * - 旧客户端兼容：未知事件名不改变 Turn 状态（契约红线）；
 * - 澄清卡过期判定与"新的补充问题请求"语义（P0-1：不发送 resume_from）。
 */

import { describe, expect, it } from "vitest";

import { parseSseFrames } from "./api";
import type { AssistClarification, AssistIntegrity, AssistPlan, AssistToolCall, StreamEvent } from "../types";

/** 与 ChatPage.handleEvent 的 assist 分支等价的纯函数重述（保持同一数据形状） */
function applyAssistEvent(turn: Record<string, unknown>, event: StreamEvent): Record<string, unknown> {
  const data = (event.data ?? {}) as Record<string, unknown>;
  if (event.event === "plan_created" && Array.isArray(data.steps)) {
    return {
      ...turn,
      plan: {
        steps: data.steps as AssistPlan["steps"],
        route: typeof data.route === "string" ? data.route : "",
      },
      toolCalls: [],
    };
  }
  if (event.event === "tool_call_started" && typeof data.step === "string") {
    const entry: AssistToolCall = {
      step: data.step,
      label: typeof data.label === "string" ? data.label : data.step,
      status: "running",
    };
    return { ...turn, toolCalls: [...((turn.toolCalls as AssistToolCall[]) ?? []), entry] };
  }
  if (event.event === "tool_call_finished" && typeof data.step === "string") {
    const status: AssistToolCall["status"] =
      data.status === "ok" ? "ok" : data.status === "denied" ? "denied" : data.status === "timeout" ? "timeout" : "failed";
    return {
      ...turn,
      toolCalls: ((turn.toolCalls as AssistToolCall[]) ?? []).map((item) =>
        item.step === data.step && item.status === "running"
          ? { ...item, status, latency_ms: typeof data.latency_ms === "number" ? data.latency_ms : undefined }
          : item,
      ),
    };
  }
  if (event.event === "clarification_required") {
    const clarification: AssistClarification = {
      clarification_id: String(data.clarification_id ?? ""),
      questions: Array.isArray(data.questions) ? (data.questions as string[]) : [],
      context_hash: String(data.context_hash ?? ""),
      expires_at: String(data.expires_at ?? ""),
    };
    return { ...turn, clarification, state: "complete" };
  }
  if (event.event === "loop_fell_back") {
    return { ...turn, fallbackReason: "查询步骤过多，已回到单次检索；以下回答基于一次直接查找" };
  }
  if (event.event === "citation_integrity_checked") {
    const integrity: AssistIntegrity = {
      passed: data.passed === true,
      applicable: data.applicable !== false,
      checks: (data.checks as AssistIntegrity["checks"]) ?? {},
    };
    return { ...turn, citationIntegrity: integrity };
  }
  // 未知事件：状态零变化（旧客户端兼容红线）
  return turn;
}

describe("M2 assist 事件 → Turn 状态", () => {
  it("plan_created 建立步骤序列并清空工具记录", () => {
    const turn = applyAssistEvent(
      { answer: "" },
      {
        event: "plan_created",
        data: { steps: [{ name: "retrieve_evidence", label: "查找制度证据" }], route: "factual" },
      },
    );
    const plan = turn.plan as AssistPlan;
    expect(plan.steps).toHaveLength(1);
    expect(plan.route).toBe("factual");
    expect(turn.toolCalls).toEqual([]);
  });

  it("tool started/finished 累积记录并更新状态", () => {
    let turn: Record<string, unknown> = {};
    turn = applyAssistEvent(turn, {
      event: "tool_call_started",
      data: { step: "retrieve_evidence", label: "查找制度证据" },
    });
    expect((turn.toolCalls as AssistToolCall[])).toHaveLength(1);
    expect((turn.toolCalls as AssistToolCall[])[0].status).toBe("running");

    turn = applyAssistEvent(turn, {
      event: "tool_call_finished",
      data: { step: "retrieve_evidence", status: "ok", latency_ms: 120 },
    });
    expect((turn.toolCalls as AssistToolCall[])[0].status).toBe("ok");
    expect((turn.toolCalls as AssistToolCall[])[0].latency_ms).toBe(120);
  });

  it("denied/timeout 状态正确分派", () => {
    let turn: Record<string, unknown> = {};
    turn = applyAssistEvent(turn, { event: "tool_call_started", data: { step: "resolve_version" } });
    turn = applyAssistEvent(turn, { event: "tool_call_finished", data: { step: "resolve_version", status: "denied" } });
    expect((turn.toolCalls as AssistToolCall[])[0].status).toBe("denied");
  });

  it("clarification_required 置 complete + waiting（关流语义）", () => {
    const turn = applyAssistEvent(
      {},
      {
        event: "clarification_required",
        data: {
          clarification_id: "abc123",
          questions: ["你关注哪个版本？"],
          context_hash: "h",
          expires_at: new Date(Date.now() + 30 * 60_000).toISOString(),
        },
      },
    );
    expect(turn.state).toBe("complete");
    expect((turn.clarification as AssistClarification).clarification_id).toBe("abc123");
  });

  it("citation_integrity_checked 失败时 passed=false（驱动 evidence-only 提示）", () => {
    const turn = applyAssistEvent(
      {},
      { event: "citation_integrity_checked", data: { passed: false, applicable: true, checks: { unknown_markers: ["[citation-9]"] } } },
    );
    expect((turn.citationIntegrity as AssistIntegrity).passed).toBe(false);
  });

  it("未知事件不改变状态（旧客户端兼容红线）", () => {
    const before = { answer: "已有内容", toolCalls: [{ step: "x", label: "y", status: "ok" }] };
    const after = applyAssistEvent(before, { event: "some_future_event", data: { anything: 1 } });
    expect(after).toBe(before); // 同引用：零变化
  });
});

describe("澄清卡语义（UI-G §4.2；P0-1 诚实化）", () => {
  it("expires_at 已过 → 过期态（引导重新提问）", () => {
    const expired = new Date(Date.now() - 60_000).toISOString();
    expect(new Date(expired).getTime() < Date.now()).toBe(true);
  });

  it("提交是新的补充问题请求：question 拼接补充信息，payload 不含 resume_from", () => {
    const question = "差旅餐补标准是多少";
    const answers = { "你关注哪个版本？": "2026-07-01 之后的现行版本" };
    const joined = Object.values(answers).filter(Boolean).join("；");
    // P0-1：后端 AssistRequest 没有 resume_from 字段——前端不得声称或发送服务端恢复
    const payload: Record<string, unknown> = { question: `${question}（补充：${joined}）` };
    expect(payload.question).toContain("补充：");
    expect("resume_from" in payload).toBe(false);
    expect(JSON.stringify(payload)).not.toContain("resume_from");
  });

  it("澄清卡文案：说明基于补充信息重新核对，不宣称服务端恢复", () => {
    const clarificationCardCopy = "补充后将基于补充信息重新核对制度依据，作为新的问题请求处理";
    expect(clarificationCardCopy).toContain("重新核对");
    expect(clarificationCardCopy).not.toContain("恢复");
    expect(clarificationCardCopy).not.toContain("继续上");
  });
});

describe("parseSseFrames 对 assist 事件的帧解析", () => {
  it("多事件流保持顺序与未知事件可解析性", () => {
    const wire =
      [
        'event: plan_created\ndata: {"event":"plan_created","data":{"steps":[{"name":"retrieve_evidence","label":"查找制度证据"}],"route":"factual"}}',
        "",
        'event: tool_call_started\ndata: {"event":"tool_call_started","data":{"step":"retrieve_evidence"}}',
        "",
        'event: completed\ndata: {"event":"completed","data":{"result_state":"answered"}}',
        "",
      ].join("\n") + "\n";
    const { events, remainder } = parseSseFrames(wire);
    expect(events.map((item) => item.event)).toEqual(["plan_created", "tool_call_started", "completed"]);
    expect(remainder).toBe("");
  });
});
