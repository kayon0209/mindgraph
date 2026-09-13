import type {
  AgentArtifactContent,
  AgentArtifactMeta,
  AgentTask,
  AnswerResult,
  ChatRequest,
  ConceptGapsResponse,
  ConfirmedRelationsResponse,
  DocumentVersionModel,
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

import { contentVersion, slugifyLogicalId } from "./document-upload";

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "/api/v1").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    /** 后端错误体的 error.code（如 index_consistency_blocked）——调用方据此分支。 */
    public readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * 从后端错误体里取出人能读的消息与机器可判的 code。
 *
 * 后端 ProductError 的响应体是 ``{"error": {"code", "message", "detail"}}``
 * （见 api/exception_handlers._build_error_response），而 422 校验错误是
 * ``{"detail": [...]}``。以前只读顶层 ``detail/message``，于是 404/409 这类
 * 业务错误在界面上只剩 "Conflict" 这种 HTTP 状态文本——索引门禁那条带着
 * "force=true 重试"指引的 409 也会被吞掉。
 */
export function describeApiError(body: unknown, fallback: string): { message: string; code?: string } {
  if (!body || typeof body !== "object") return { message: fallback };
  const record = body as Record<string, unknown>;
  const nested = (record.error && typeof record.error === "object" ? record.error : {}) as Record<string, unknown>;
  const code = typeof nested.code === "string" ? nested.code : undefined;
  for (const candidate of [nested.message, record.message, nested.detail, record.detail]) {
    if (typeof candidate === "string" && candidate.trim()) return { message: candidate, code };
  }
  const detail = nested.detail ?? record.detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => (item && typeof item === "object" ? String((item as Record<string, unknown>).msg ?? "") : String(item)))
      .filter(Boolean);
    if (parts.length) return { message: parts.join("; "), code };
  }
  return { message: fallback, code };
}

async function send<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    let parsed: unknown;
    try {
      parsed = await response.json();
    } catch {
      parsed = undefined; // 非 JSON 错误体：保留 HTTP 状态文本
    }
    const { message, code } = describeApiError(parsed, response.statusText);
    throw new ApiError(message || `HTTP ${response.status}`, response.status, code);
  }
  return response.json() as Promise<T>;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return send<T>(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
}

/** multipart 上传：**不要**设置 Content-Type，boundary 必须由浏览器生成。 */
async function requestForm<T>(path: string, body: FormData): Promise<T> {
  return send<T>(path, { method: "POST", body });
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

/** M2：确定性 Assist Agent 流（AGENT_ASSIST_ENABLED 开启时可用；404 = 服务端未开）。
 * P0-1：澄清补充作为新的问题提交；AssistRequest 契约没有 resume_from。 */
export async function streamAssistAgent(
  payload: ChatRequest,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE}/assist/agent/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok || !response.body) {
    throw new ApiError(response.statusText || "Assist stream unavailable", response.status);
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

/** Assist 深度核对可用性探测：读 /config/public 的 assist_agent_enabled 布尔。
 * P0-1 后续：旧探测 POST /assist/agent/stream 有副作用（写 assist_stream 审计、
 * flag 开启时触发一次真实 agent 执行）——读配置零副作用。
 * 请求失败也判 false：探测失败宁可提示"服务端未开启"也不让用户踩空。 */
export async function assistAgentEnabled(): Promise<boolean> {
  try {
    const config = await api.publicConfig();
    return config.assist_agent_enabled === true;
  } catch {
    return false;
  }
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
  /**
   * 材料上传：走**版本化生命周期**（`POST /knowledge/versions`），而不是只把文件
   * 丢进 uploads/ 的旧入口。
   *
   * 旧入口（`POST /knowledge/documents`）不写 document_versions、没有页级账本、
   * 不接 OCR——用它上传的材料拿不到任何新能力（P4 现场核对实测：页级账本 0 条）。
   *
   * 三步串联，缺一步这份材料对检索就是不可见的：
   * ① 上传（draft）→ ② 状态流转 draft→pending_index→active（TRANSITIONS 不允许
   * 跳级）→ ③ 增量重建索引（chunks 只在 active 之后才进得了索引）。
   *
   * 解析未通过（含需 OCR 的扫描页）时后端直接给 parse_failed，此时**不做**流转：
   * 硬转会 409，而"解析失败"本身是需要告诉用户的信息，不该被一次伪造的流转盖掉。
   */
  uploadDocumentVersion: async (file: File, category = "upload"): Promise<DocumentVersionModel> => {
    const buildForm = (version: string) => {
      const form = new FormData();
      form.append("file", file);
      form.append("logical_document_id", slugifyLogicalId(file.name));
      form.append("version", version);
      form.append("category", category);
      form.append("authority_level", "user_uploaded_reference");
      return form;
    };
    let record: DocumentVersionModel;
    try {
      record = await requestForm<DocumentVersionModel>("/knowledge/versions", buildForm("v1"));
    } catch (error) {
      // 同名同版本已存在（`create_version` 以 logical_id+version 定位，不自动递增）。
      // 用内容派生版本再试一次：内容真变了就是一次正常的版本更新；没变则第二次同样
      // 冲突，错误照实抛给用户（"这份文件已在库中"）。不静默吞掉，调用方会展示
      // record.version，用户能看到落到哪个版本上。
      if (!(error instanceof ApiError) || error.status !== 409) throw error;
      record = await requestForm<DocumentVersionModel>(
        "/knowledge/versions",
        buildForm(contentVersion(file.lastModified, file.size)),
      );
    }
    if (record.status !== "draft") return record;
    for (const target of ["pending_index", "active"]) {
      record = await request<DocumentVersionModel>(
        `/knowledge/versions/${encodeURIComponent(record.document_id)}/transition?target=${target}`,
        { method: "POST" },
      );
    }
    return record;
  },
  /**
   * 上传后增量重建索引，使新材料进入检索。
   *
   * ``force`` 是切分口径门禁的逃生口：候选索引与活跃索引的切分口径/文档覆盖不一致
   * 时后端 409（防 2026-09-11 那类静默换口径），确认后带 force=true 再试一次。
   */
  incrementalRebuild: (force = false) =>
    request<Record<string, unknown>>(
      `/knowledge/index/incremental-rebuild${force ? "?force=true" : ""}`,
      { method: "POST" },
    ),
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
  /** M3：服务端会话（CONVERSATION_PERSISTENCE_ENABLED 开启时可用） */
  createConversation: (payload: { title: string; workspace?: string; department?: string }) =>
    request<{ conversation_id: string; title: string; status: string; created_at: string; updated_at: string }>(
      "/mindgraph/conversations", { method: "POST", body: JSON.stringify(payload) },
    ),
  listConversations: (cursor?: string, limit = 50) =>
    request<{ items: Array<{ conversation_id: string; title: string; status: string; created_at: string; updated_at: string }>; next_cursor: string | null }>(
      `/mindgraph/conversations${cursor ? `?cursor=${encodeURIComponent(cursor)}&limit=${limit}` : `?limit=${limit}`}`,
    ),
  getConversationMessages: (conversationId: string) =>
    request<Array<{ message_id: string; sequence_no: number; role: string; content: string; created_at: string; request_id?: string | null }>>(
      `/mindgraph/conversations/${encodeURIComponent(conversationId)}/messages`,
    ),
  importConversationTurns: (conversationId: string, turns: Array<Record<string, unknown>>) =>
    request<{ imported: number; skipped_existing: number; mapping: unknown[]; total_messages: number }>(
      `/mindgraph/conversations/${encodeURIComponent(conversationId)}/import-turns`,
      { method: "POST", body: JSON.stringify({ turns }) },
    ),
  /** M4-A：后台任务（AGENT_TASKS_ENABLED 开启时可用；Idempotency-Key 幂等提交） */
  submitAgentTask: (constraints: Record<string, unknown>, idempotencyKey: string) =>
    request<AgentTask>("/mindgraph/agent/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ task_type: "batch_policy_check", constraints }),
    }),
  listAgentTasks: (cursor?: string, limit = 50) =>
    request<{ items: AgentTask[]; next_cursor: string | null }>(
      `/mindgraph/agent/tasks${cursor ? `?cursor=${encodeURIComponent(cursor)}&limit=${limit}` : `?limit=${limit}`}`,
    ),
  getAgentTask: (taskId: string) =>
    request<AgentTask & { artifacts: AgentArtifactMeta[] }>(`/mindgraph/agent/tasks/${encodeURIComponent(taskId)}`),
  cancelAgentTask: (taskId: string) =>
    request<AgentTask>(`/mindgraph/agent/tasks/${encodeURIComponent(taskId)}/cancel`, { method: "POST" }),
  getAgentArtifact: (taskId: string, artifactId: string) =>
    request<AgentArtifactContent>(
      `/mindgraph/agent/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(artifactId)}`,
    ),
};
