import type {
  AnswerResult,
  ChatRequest,
  ConceptGapsResponse,
  ConfirmedRelationsResponse,
  DocumentRecord,
  EvaluationResponse,
  ExtractRelationsResult,
  HealthStatus,
  MineQuestionsResult,
  NoteDetail,
  NoteItem,
  ProposedRelationsResponse,
  PublicConfig,
  StreamEvent,
} from "../types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "/api/v1").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: string; message?: string };
      detail = body.detail || body.message || detail;
    } catch {
      // Keep the HTTP status text when the body is not JSON.
    }
    throw new ApiError(detail || `HTTP ${response.status}`, response.status);
  }
  return response.json() as Promise<T>;
}

export function parseSseFrames(input: string): { events: StreamEvent[]; remainder: string } {
  const normalized = input.replace(/\r\n/g, "\n");
  const frames = normalized.split("\n\n");
  const remainder = frames.pop() ?? "";
  const events: StreamEvent[] = [];

  for (const frame of frames) {
    let eventName = "message";
    const dataLines: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    }
    if (!dataLines.length) continue;
    try {
      const parsed = JSON.parse(dataLines.join("\n")) as StreamEvent;
      events.push({ ...parsed, event: parsed.event || eventName });
    } catch {
      // A malformed complete frame must not discard later valid SSE events.
    }
  }
  return { events, remainder };
}

export async function streamChat(
  payload: ChatRequest,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE}/mindgraph/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok || !response.body) {
    throw new ApiError(response.statusText || "SSE stream unavailable", response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parsed = parseSseFrames(buffer);
    buffer = parsed.remainder;
    parsed.events.forEach(onEvent);
  }
  buffer += decoder.decode();
  const tail = parseSseFrames(`${buffer}\n\n`);
  tail.events.forEach(onEvent);
}

export const api = {
  health: () => request<HealthStatus>("/health"),
  publicConfig: () => request<PublicConfig>("/config/public"),
  answer: (payload: ChatRequest) =>
    request<AnswerResult>("/mindgraph/chat", { method: "POST", body: JSON.stringify(payload) }),
  submitFeedback: (payload: { request_id: string; rating: "helpful" | "not_helpful" }) =>
    request<{ feedback_id: string; request_id: string }>("/feedback", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  notes: (query = "", offset = 0, limit = 200) =>
    request<{ total: number; items: NoteItem[] }>(
      `/mindgraph/notes?limit=${limit}&offset=${offset}&q=${encodeURIComponent(query)}`,
    ),
  note: (id: string) => request<NoteDetail>(`/mindgraph/notes/${encodeURIComponent(id)}`),
  evaluations: () => request<EvaluationResponse>("/mindgraph/evaluation/ablation"),
  proposedRelations: () => request<ProposedRelationsResponse>("/mindgraph/relations/proposed"),
  confirmedRelations: () => request<ConfirmedRelationsResponse>("/mindgraph/relations/confirmed"),
  resolveRelation: (id: string, decision: "confirm" | "reject", reason: string) =>
    request<{ ok: boolean; status: string }>(`/mindgraph/relations/${encodeURIComponent(id)}/resolve`, {
      method: "POST",
      body: JSON.stringify({ decision, reason }),
    }),
  /** 材料上传（阶段A需求3）：multipart 走独立 fetch，不能复用 JSON request 助手 */
  uploadDocument: async (file: File, category = "upload"): Promise<DocumentRecord> => {
    const form = new FormData();
    form.append("file", file);
    form.append("category", category);
    const response = await fetch(`${API_BASE}/knowledge/documents`, { method: "POST", body: form });
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const body = (await response.json()) as { detail?: string; message?: string };
        detail = body.detail || body.message || detail;
      } catch {
        // 非 JSON 错误体保留状态文本
      }
      throw new ApiError(detail || `HTTP ${response.status}`, response.status);
    }
    return response.json() as Promise<DocumentRecord>;
  },
  /** 上传后增量重建索引，使新材料进入检索 */
  incrementalRebuild: () =>
    request<Record<string, unknown>>("/knowledge/index/incremental-rebuild", { method: "POST" }),
  /** 离线关系抽取（HITL：只产 proposed，需人工确认后入图） */
  extractRelations: (options?: { dry_run?: boolean; use_llm?: boolean }) =>
    request<ExtractRelationsResult>("/mindgraph/relations/extract", {
      method: "POST",
      body: JSON.stringify({ method: "embedding", use_llm: false, dry_run: false, ...options }),
    }),
  /** 问题概念挖掘（纯规则式，HITL：只产 proposed CO_ASKED，需人工确认后入图） */
  mineQuestions: () =>
    request<MineQuestionsResult>("/mindgraph/relations/mine-questions", { method: "POST" }),
  /** 覆盖缺口：用户问过但语料未覆盖的概念（指导补传材料） */
  conceptGaps: (limit = 50) =>
    request<ConceptGapsResponse>(`/mindgraph/concept-gaps?limit=${limit}`),
};
