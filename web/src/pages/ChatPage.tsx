import { FormEvent, useEffect, useReducer, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowUp,
  BookOpenCheck,
  Check,
  Circle,
  FileDown,
  Gauge,
  GitBranch,
  History,
  LoaderCircle,
  PanelRightClose,
  Plus,
  RotateCcw,
  ShieldQuestion,
  Sparkles,
  Square,
  ThumbsDown,
  ThumbsUp,
  Trash2,
} from "lucide-react";

import { AnswerBody } from "../components/AnswerBody";
import { api, assistAgentEnabled, streamAssistAgent, streamChat } from "../lib/api";
import { citationValidity, fidelityMissingMarks as citationFidelityMarks, summarizeCitationValidity } from "../lib/citation-status";
import { buildEvidenceMarkdown, downloadTextFile, evidenceFilename } from "../lib/export-evidence";
import { completionGenerationState, completionViewState, policyConflictItems } from "../lib/policy-conflicts";
import { routeDecisionView } from "../lib/route-decision";
import { confirmDeleteLocalSession, fetchServerConversationsEnabled, migrateAllSessions, migratedSessionsWithLocalCopy, sessionsPendingMigration } from "../lib/session-migration";
import { buildGuidedTasks } from "../lib/onboarding-tasks";

/** 5 任务可用性脚本的空态引导（一次构建；内容见 onboarding-tasks.ts） */
const GUIDED_TASKS = buildGuidedTasks();
import { INITIAL_RAIL, railReducer } from "../lib/chat-rail-reducer";
import type {
  AnswerResult,
  AssistClarification,
  AssistIntegrity,
  AssistPlan,
  AssistToolCall,
  Citation,
  ChatRequest,
  RetrievalTrace,
  RouteDecision,
  StreamEvent,
  UsageInfo,
} from "../types";
import { PageHeader } from "../components/Primitives";

type Turn = {
  id: string;
  question: string;
  answer: string;
  state: "streaming" | "complete" | "error";
  errorDetail?: string;
  requestId?: string;
  feedback?: "helpful" | "not_helpful" | "failed";
  /** U6：每轮自带证据快照，证据链轨道可以定位到任意历史轮次 */
  citations?: Citation[];
  trace?: RetrievalTrace | null;
  route?: RouteDecision | null;
  resultState?: string | null;
  steps?: Record<string, StepState>;
  usage?: UsageInfo | null;
  degraded?: string | null;
  /** M0 契约基线：回答中 [citation-N] 是否全部命中本次引用集（true/false/null=不可判定） */
  citationFidelity?: boolean | null;
  elapsedMs?: number;
  /** 版本时效判定所需的查询日期（缺省按今天）与轮次创建时间 */
  queryDate?: string;
  createdAt?: string;
  /** 证据导出需要记录实际使用的模型与索引版本 */
  model?: string;
  indexVersion?: string | null;
  /** M2 Assist：执行计划（步骤名+用户语言标签）、工具记录、澄清卡、引用校验结果 */
  plan?: AssistPlan | null;
  toolCalls?: AssistToolCall[];
  clarification?: AssistClarification | null;
  clarificationAnswered?: boolean;
  citationIntegrity?: AssistIntegrity | null;
  /** M2：loop_fell_back 的可见原因（一句话，见 COPY-DECK §6） */
  fallbackReason?: string | null;
};

/** 会话历史（研究项⑥）：本地多会话，工作留痕定位，非审计级留存 */
type ChatSession = {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  turns: Turn[];
};

const ERROR_MESSAGES: Record<string, string> = {
  retrieval_unavailable: "检索服务暂不可用，请稍后重试。",
  provider_error: "生成模型暂时不可用，可先查看引用原文。",
  stream_error: "回答连接中断，请重试。",
  // F3/F6：后端归一化后的 provider 错误码，各自给出可行动的提示
  quota_exhausted: "模型配额已用尽。请检查服务商账户额度，或在 .env 中切换其他模型。",
  rate_limited: "请求过于频繁已被限流，请稍等片刻再重试。",
  authentication_failed: "模型认证失败，请检查服务端 API 密钥配置。",
  provider_unavailable: "模型服务暂不可用，请稍后重试。",
  model_not_found: "配置的模型不存在，请检查服务端模型名称设置。",
  invalid_request: "请求被模型服务拒绝，请简化问题后重试。",
  timeout: "模型响应超时，请稍后重试或减少引用数量。",
};

/** 上次会话若在流式回答中途关闭页面，恢复后不能永远停在"生成中"。 */
function normalizeRestoredTurns(turns: Turn[]): Turn[] {
  return turns.map((turn) =>
    turn.state === "streaming"
      ? { ...turn, state: "error" as const, answer: turn.answer || "上次回答被中断，请重新提问。" }
      : turn,
  );
}
function randomId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // 非安全上下文（如通过局域网 IP 访问）没有 crypto.randomUUID
  return `turn-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

const SESSIONS_KEY = "mindgraph.chat.sessions";
const ACTIVE_SESSION_KEY = "mindgraph.chat.activeSession";
/** 旧版单线程存储：首次加载时迁移进会话列表，之后不再写入 */
const LEGACY_TURNS_KEY = "mindgraph.chat.turns";

/** 会话标题 = 首个问题截断 20 字（研究项⑥的命名约定） */
function makeSessionTitle(question: string): string {
  const trimmed = question.trim();
  return trimmed.length > 20 ? `${trimmed.slice(0, 20)}…` : trimmed || "未命名会话";
}

function loadSessions(): { sessions: ChatSession[]; activeId: string | null } {
  let sessions: ChatSession[] = [];
  try {
    const raw = window.localStorage.getItem(SESSIONS_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as ChatSession[];
      if (Array.isArray(parsed)) {
        sessions = parsed
          .filter((item) => item && typeof item.id === "string" && Array.isArray(item.turns))
          .map((item) => ({ ...item, turns: normalizeRestoredTurns(item.turns) }));
      }
    }
  } catch {
    sessions = [];
  }
  // 从旧版单线程存储迁移（只迁移一次）
  if (!sessions.length) {
    try {
      const legacy = window.localStorage.getItem(LEGACY_TURNS_KEY);
      if (legacy) {
        const legacyTurns = normalizeRestoredTurns(JSON.parse(legacy) as Turn[]);
        if (Array.isArray(legacyTurns) && legacyTurns.length) {
          const now = new Date().toISOString();
          sessions = [{
            id: randomId(),
            title: makeSessionTitle(legacyTurns[0]?.question ?? ""),
            createdAt: legacyTurns[0]?.createdAt || now,
            updatedAt: now,
            turns: legacyTurns,
          }];
        }
        window.localStorage.removeItem(LEGACY_TURNS_KEY);
      }
    } catch {
      window.localStorage.removeItem(LEGACY_TURNS_KEY);
    }
  }
  let activeId: string | null = null;
  try {
    activeId = window.localStorage.getItem(ACTIVE_SESSION_KEY);
  } catch {
    activeId = null;
  }
  if (!activeId || !sessions.some((session) => session.id === activeId)) {
    activeId = sessions[0]?.id ?? null;
  }
  return { sessions, activeId };
}

function persistSessions(sessions: ChatSession[], activeId: string | null) {
  try {
    window.localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions));
    if (activeId) window.localStorage.setItem(ACTIVE_SESSION_KEY, activeId);
    else window.localStorage.removeItem(ACTIVE_SESSION_KEY);
  } catch {
    // 存储配额/隐私模式失败可接受——会话持久化是增强能力而非关键路径
  }
}

const ONBOARDING_STEPS = [
  { title: "① 直接提问", detail: "像问同事一样问制度问题，例如「逾期 45 天提交还能报吗？」" },
  { title: "② 看引用不猜答案", detail: "每个结论都带制度名、版本和生效日期；没有依据时系统会明确拒答。" },
  { title: "③ 进阶可调", detail: "需要历史版本或关系追溯时，展开「高级设置」按需开启。" },
];
type StepState = "waiting" | "running" | "done" | "warning";

const QUICK_QUESTIONS = [
  "2026 年 8 月发生的费用最晚多久提交？",
  "客户晚餐和差旅餐补能同时报销吗？",
  "无发票的 1500 元费用需要哪些审批？",
];

const INITIAL_STEPS: Record<string, StepState> = { ...INITIAL_RAIL.steps };

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

/* I6：高级设置持久化——刷新/重开页面后，用户调过的检索参数不丢 */
const SETTINGS_KEY = "mindgraph.chat.settings";

type ChatSettings = {
  strategy: ChatRequest["retrieval_strategy"];
  topK: number;
  graphEnabled: boolean;
  graphHops: number;
};

function loadSettings(): ChatSettings {
  const fallback: ChatSettings = { strategy: "auto", topK: 5, graphEnabled: false, graphHops: 1 };
  try {
    const raw = window.localStorage.getItem(SETTINGS_KEY);
    if (!raw) return fallback;
    const parsed = asRecord(JSON.parse(raw));
    return {
      strategy: ["auto", "hybrid", "hybrid_rerank", "dense", "bm25"].includes(parsed.strategy as string)
        ? (parsed.strategy as ChatSettings["strategy"])
        : fallback.strategy,
      topK: [3, 5, 8, 10].includes(parsed.topK as number) ? (parsed.topK as number) : fallback.topK,
      graphEnabled: typeof parsed.graphEnabled === "boolean" ? parsed.graphEnabled : fallback.graphEnabled,
      graphHops: parsed.graphHops === 2 ? 2 : 1,
    };
  } catch {
    return fallback;
  }
}

export function ChatPage() {
  const [question, setQuestion] = useState("");
  const [strategy, setStrategy] = useState<ChatRequest["retrieval_strategy"]>(() => loadSettings().strategy);
  const [topK, setTopK] = useState(() => loadSettings().topK);
  const [graphEnabled, setGraphEnabled] = useState(() => loadSettings().graphEnabled);
  /** M2：本地 Assist 开关（后端 AGENT_ASSIST_ENABLED 关闭时端点 404，回落普通流） */
  const [assistMode, setAssistMode] = useState(false);
  /** Assist 服务端可用性（null=探测中；false=服务端未开启，开关旁提示） */
  const [assistAvailable, setAssistAvailable] = useState<boolean | null>(null);
  /** M3：服务端会话迁移（显式、用户主动触发；服务端未开启时隐藏入口） */
  const [serverConversationsEnabled, setServerConversationsEnabled] = useState(false);
  const [migrating, setMigrating] = useState(false);
  const [migrationDone, setMigrationDone] = useState(0);
  const [migrationResult, setMigrationResult] = useState<{ migrated: string[]; failed: string[] } | null>(null);

  /** M3：探测服务端会话是否开启（列表端点 404 = 未开启，入口隐藏）；
   * 同时探测 assist 面——P0-1 后续：改读 /config/public 的
   * assist_agent_enabled（零副作用），不再 POST agent stream（旧探测会写
   * 无问题的 assist_stream 审计，flag 开启时甚至触发一次真实 agent 执行）。 */
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const enabled = await fetchServerConversationsEnabled();
        if (!cancelled) setServerConversationsEnabled(enabled);
      } catch {
        if (!cancelled) setServerConversationsEnabled(false);
      }
      const assist = await assistAgentEnabled();
      if (!cancelled) setAssistAvailable(assist);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  /** M3（§8.2 修订）：逐会话迁移——上传-校验-记标记，**本地正文保留**；
   * 删除本地副本是另一个单独确认动作（confirmDeleteLocalSession）。 */
  const runMigration = async (pending: Parameters<typeof migrateAllSessions>[0]) => {
    setMigrating(true);
    setMigrationDone(0);
    try {
      const result = await migrateAllSessions(pending, {
        onProgress: (done) => setMigrationDone(done),
      });
      setMigrationResult(result);
      // 迁移不清理本地会话列表（本地副本继续作为缓存保留）
    } finally {
      setMigrating(false);
    }
  };

  /** M3：单独确认动作——用户逐会话删除已迁移的本地副本（幂等） */
  const deleteLocalCopy = (sessionId: string) => {
    confirmDeleteLocalSession(sessionId);
    const kept = sessions.filter((session) => session.id !== sessionId);
    setSessions(kept);
    if (kept.length === 0) startNewSession();
  };
  const [graphHops, setGraphHops] = useState(() => loadSettings().graphHops);
  const [queryDate, setQueryDate] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  // UI-1（结构重构）：实时轨道状态收敛为单一 reducer；轮次快照仍在 Turn
  const [rail, dispatch] = useReducer(railReducer, INITIAL_RAIL);
  const citations = rail.citations;
  const trace = rail.trace;
  const routeDecision = rail.routeDecision;
  const resultState = rail.resultState;
  const citationFidelity = rail.citationFidelity;
  const steps = rail.steps;
  const [running, setRunning] = useState(false);
  // I5：展示本次生成的 token 用量；I4：降级原因可见
  const usage = rail.usage;
  const degradedReason = rail.degradedReason;
  // U6：证据链轨道定位到的轮次（null = 跟随最新一轮）
  const [activeTurnId, setActiveTurnId] = useState<string | null>(null);
  // 证据轨默认折叠成一条窄边栏，只在用户主动查看时展开
  const [railOpen, setRailOpen] = useState(false);
  // 研究项①：答案内引用角标点击后，证据链滚动并闪烁定位的目标
  const [citationFocus, setCitationFocus] = useState<{ rank: number; nonce: number } | null>(null);
  // I3：生成计时
  const [elapsed, setElapsed] = useState(0);
  // U4：清空对话的两步确认
  const [confirmClear, setConfirmClear] = useState(false);
  // 研究项⑥：会话菜单开合 + 删除会话的两步确认
  const [sessionMenuOpen, setSessionMenuOpen] = useState(false);
  const [confirmSessionDelete, setConfirmSessionDelete] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const railRef = useRef<HTMLElement | null>(null);
  const startedAtRef = useRef(0);
  const stepsRef = useRef<Record<string, StepState>>(INITIAL_STEPS);
  const usageRef = useRef<UsageInfo | null>(null);
  const degradedRef = useRef<string | null>(null);

  useEffect(() => {
    const { sessions: loadedSessions, activeId } = loadSessions();
    setSessions(loadedSessions);
    setActiveSessionId(activeId);
    const active = loadedSessions.find((session) => session.id === activeId);
    if (active) {
      setTurns(active.turns);
      restoreRailFromTurns(active.turns);
    }
  }, []);

  // 活跃会话的实时轮次同步回会话列表（内存），再由下方效果落盘
  useEffect(() => {
    if (!activeSessionId || !turns.length) return;
    setSessions((current) =>
      current.map((session) =>
        session.id === activeSessionId
          ? { ...session, turns, updatedAt: new Date().toISOString() }
          : session,
      ),
    );
  }, [turns, activeSessionId]);

  useEffect(() => {
    persistSessions(sessions, activeSessionId);
  }, [sessions, activeSessionId]);

  useEffect(() => {
    if (!turns.length) composerRef.current?.focus();
  }, [turns.length]);

  useEffect(() => {
    stepsRef.current = steps;
  }, [steps]);

  // I3：生成中每秒刷新已用时，避免 30s+ 的生成过程没有任何进度反馈
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - startedAtRef.current) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [running]);

  // I6：高级设置变化即持久化
  useEffect(() => {
    try {
      window.localStorage.setItem(SETTINGS_KEY, JSON.stringify({ strategy, topK, graphEnabled, graphHops }));
    } catch {
      // 存储失败可忽略：下次使用默认值
    }
  }, [strategy, topK, graphEnabled, graphHops]);

  // U4：清空确认态 3 秒后自动还原，避免按钮停留在危险态
  useEffect(() => {
    if (!confirmClear) return;
    const timer = window.setTimeout(() => setConfirmClear(false), 3000);
    return () => window.clearTimeout(timer);
  }, [confirmClear]);

  useEffect(() => {
    if (!confirmSessionDelete) return;
    const timer = window.setTimeout(() => setConfirmSessionDelete(null), 3000);
    return () => window.clearTimeout(timer);
  }, [confirmSessionDelete]);

  // 研究项①：点击答案内角标后，等轨道按所选轮次重渲染，再滚动并闪烁目标引用卡
  useEffect(() => {
    if (!citationFocus) return;
    const timer = window.setTimeout(() => {
      const target = railRef.current?.querySelector<HTMLElement>(
        `[data-citation-rank="${citationFocus.rank}"]`,
      );
      if (target) {
        target.scrollIntoView({ behavior: "smooth", block: "nearest" });
        target.classList.add("flash");
        window.setTimeout(() => target.classList.remove("flash"), 1800);
      }
    }, 80);
    return () => window.clearTimeout(timer);
  }, [citationFocus]);

  const updateTurn = (id: string, patch: Partial<Turn>) => {
    setTurns((current) => current.map((turn) => (turn.id === id ? { ...turn, ...patch } : turn)));
  };

  const appendAnswer = (id: string, text: string) => {
    setTurns((current) =>
      current.map((turn) => (turn.id === id ? { ...turn, answer: `${turn.answer}${text}` } : turn)),
    );
  };

  const handleEvent = (turnId: string, event: StreamEvent) => {
    const data = asRecord(event.data);
    /** 函数式读取该轮当前 assist 状态（setTurns 闭包内拿到的一定是最新值） */
    const patchTurnWith = (patch: (turn: Turn) => Partial<Turn>) => {
      setTurns((current) => current.map((turn) => (turn.id === turnId ? { ...turn, ...patch(turn) } : turn)));
    };
    // ── M2 assist 事件分支（UI-G 状态矩阵 §2；旧事件逻辑不变） ──
    if (event.event === "plan_created" && Array.isArray(data.steps)) {
      updateTurn(turnId, {
        plan: {
          steps: data.steps as AssistPlan["steps"],
          route: typeof data.route === "string" ? data.route : "",
          reason_codes: Array.isArray(data.reason_codes) ? (data.reason_codes as string[]) : [],
          routing_ms: typeof data.routing_ms === "number" ? data.routing_ms : undefined,
        },
        toolCalls: [],
      });
      return;
    }
    if (event.event === "tool_call_started" && typeof data.step === "string") {
      const entry: AssistToolCall = {
        step: data.step,
        label: typeof data.label === "string" ? data.label : data.step,
        status: "running",
      };
      patchTurnWith((turn) => ({ toolCalls: [...(turn.toolCalls ?? []), entry] }));
      return;
    }
    if (event.event === "tool_call_finished" && typeof data.step === "string") {
      const status: AssistToolCall["status"] =
        data.status === "ok" ? "ok" : data.status === "denied" ? "denied" : data.status === "timeout" ? "timeout" : "failed";
      patchTurnWith((turn) => ({
        toolCalls: (turn.toolCalls ?? []).map((item) =>
          item.step === data.step && item.status === "running"
            ? {
                ...item,
                status,
                result_state: typeof data.result_state === "string" ? data.result_state : undefined,
                latency_ms: typeof data.latency_ms === "number" ? data.latency_ms : undefined,
              }
            : item,
        ),
      }));
      return;
    }
    if (event.event === "clarification_required") {
      const clarification: AssistClarification = {
        clarification_id: String(data.clarification_id ?? ""),
        questions: Array.isArray(data.questions) ? (data.questions as string[]) : [],
        context_hash: String(data.context_hash ?? ""),
        expires_at: String(data.expires_at ?? ""),
      };
      updateTurn(turnId, { clarification, state: "complete" });
      return;
    }
    if (event.event === "loop_fell_back") {
      updateTurn(turnId, {
        fallbackReason:
          data.reason === "tool_budget_exceeded"
            ? "查询步骤过多，已回到单次检索；以下回答基于一次直接查找"
            : data.reason === "citation_integrity_failed"
              ? "本次回答未通过引用校验，已改为仅显示证据"
              : "已回到单次检索模式",
      });
      return;
    }
    if (event.event === "citation_integrity_checked") {
      const integrity: AssistIntegrity = {
        passed: data.passed === true,
        applicable: data.applicable !== false,
        checks: (data.checks as AssistIntegrity["checks"]) ?? {},
      };
      updateTurn(turnId, { citationIntegrity: integrity });
      return;
    }
    // ── 既有 14 事件（行为不变） ──
    if (event.event === "request_started") {
      dispatch({ type: "request_started" });
    }
    if (event.event === "scope_check_completed") {
      dispatch({ type: "scope_check_completed", outOfScope: data.out_of_scope === true });
    }
    if (event.event === "retrieval_started") {
      dispatch({ type: "retrieval_started" });
    }
    if (event.event === "retrieval_routed") {
      dispatch({ type: "retrieval_routed", route: data as unknown as RouteDecision });
    }
    if (event.event === "retrieval_completed" || event.event === "rerank_completed") {
      dispatch({ type: "retrieval_completed" });
    }
    if (event.event === "generation_started") {
      dispatch({ type: "generation_started" });
    }
    if (event.event === "answer_delta" && typeof data.text === "string") {
      appendAnswer(turnId, data.text);
    }
    if (event.event === "citations" && Array.isArray(data.citations)) {
      dispatch({ type: "citations", citations: data.citations as Citation[] });
    }
    // I5：usage 事件在 completed 之前到达，先落 ref，completed 时随轮次快照保存
    if (event.event === "usage") {
      const parsed: UsageInfo = {
        input_tokens: typeof data.input_tokens === "number" ? data.input_tokens : null,
        output_tokens: typeof data.output_tokens === "number" ? data.output_tokens : null,
        total_tokens: typeof data.total_tokens === "number" ? data.total_tokens : null,
        usage_source: typeof data.usage_source === "string" ? data.usage_source : undefined,
      };
      usageRef.current = parsed;
      dispatch({ type: "usage", usage: parsed });
    }
    if (event.event === "degraded" || event.event === "policy_conflict_detected") {
      // I4：降级不再只是一个隐藏的步骤状态，原因要对用户可见
      const reason = typeof data.reason === "string" && data.reason ? data.reason : null;
      degradedRef.current = reason;
      dispatch({ type: "degraded", reason });
    }
    if (event.event === "completed") {
      const result = data as unknown as AnswerResult;
      const finalSteps: Record<string, StepState> = {
        scope: stepsRef.current.scope === "running" ? "done" : stepsRef.current.scope,
        retrieval: result.retrieval_trace ? "done" : stepsRef.current.retrieval,
        generation: completionGenerationState(result),
      };
      updateTurn(turnId, {
        answer: result.answer,
        state: "complete",
        requestId: result.request_id,
        citations: result.citations || [],
        trace: result.retrieval_trace || null,
        route: result.retrieval_trace?.route_decision || null,
        resultState: completionViewState(result),
        citationFidelity: typeof result.citation_fidelity === "boolean" ? result.citation_fidelity : null,
        steps: finalSteps,
        usage: usageRef.current,
        degraded: degradedRef.current ?? (result.degraded ? result.degradation_reason || "已降级" : null),
        elapsedMs: startedAtRef.current ? Date.now() - startedAtRef.current : undefined,
        model: result.model,
        indexVersion: result.index_version ?? null,
      });
      dispatch({ type: "completed", result: {
        citations: (result.citations || []) as Citation[],
        trace: (result.retrieval_trace || null) as RetrievalTrace | null,
        resultState: completionViewState(result),
        citationFidelity: typeof result.citation_fidelity === "boolean" ? result.citation_fidelity : null,
        usage: usageRef.current,
        degraded: degradedRef.current,
      } });
    }
    if (event.event === "error") {
      const code = typeof data.code === "string" ? data.code : "stream_error";
      updateTurn(turnId, {
        answer: ERROR_MESSAGES[code] || "请求暂时失败，请稍后重试。",
        errorDetail: typeof data.detail === "string" ? data.detail : undefined,
        state: "error",
        resultState: code,
        steps: { ...stepsRef.current, retrieval: "warning" },
        citations: [],
        trace: null,
        route: null,
        usage: usageRef.current,
        degraded: degradedRef.current,
      });
      dispatch({ type: "error", code });
    }
  };

  // U6：轨道数据源——选中轮次时用该轮快照，否则跟随最新请求的实时状态
  const selectedTurn = activeTurnId ? turns.find((turn) => turn.id === activeTurnId) ?? null : null;
  const railSteps = selectedTurn?.steps ?? steps;
  const railCitations = selectedTurn ? selectedTurn.citations ?? [] : citations;
  const railTrace = selectedTurn ? selectedTurn.trace ?? null : trace;
  const railRoute = selectedTurn ? selectedTurn.route ?? null : routeDecision;
  const railResultState = selectedTurn ? selectedTurn.resultState ?? null : resultState;
  const railUsage = selectedTurn ? selectedTurn.usage ?? null : usage;
  const railDegraded = selectedTurn ? selectedTurn.degraded ?? null : degradedReason;
  const railFidelity = selectedTurn ? selectedTurn.citationFidelity ?? null : citationFidelity;
  // M2：实时轮次的 assist 轨道数据（selectedTurn 覆盖历史轮次快照）
  const latestAssistPlan = turns.find((turn) => turn.state === "streaming")?.plan ?? [...turns].reverse().find((turn) => turn.plan)?.plan ?? null;
  const latestAssistToolCalls =
    turns.find((turn) => turn.state === "streaming")?.toolCalls ?? [...turns].reverse().find((turn) => turn.toolCalls?.length)?.toolCalls ?? [];
  const conflictItems = policyConflictItems(railTrace);
  const routeView = railRoute ? routeDecisionView(railRoute) : null;
  // 研究项②：版本时效判定基准日——所选轮次的查询日期，未选时跟随当前设置
  const railAsOf = selectedTurn ? selectedTurn.queryDate ?? null : queryDate || null;

  // 研究项⑥：切换/恢复会话时，把证据链轨道恢复到该会话最后一轮的状态
  const restoreRailFromTurns = (list: Turn[]) => {
    const last = [...list].reverse().find((item) => item.state === "complete" || item.state === "error");
    dispatch({ type: "reset" });
    // 恢复语义：轨道显示该会话最后一轮的快照（selectedTurn=null 时用 rail 数据）
    // 快照本体在 Turn 内；这里把实时轨道重置，避免上一会话残留
    setActiveTurnId(null);
    setCitationFocus(null);
  };

  const resetRail = () => {
    dispatch({ type: "reset" });
    setActiveTurnId(null);
    setCitationFocus(null);
  };

  const startNewSession = () => {
    if (running) return;
    setActiveSessionId(null);
    setTurns([]);
    resetRail();
    setSessionMenuOpen(false);
    composerRef.current?.focus();
  };

  const switchSession = (sessionId: string) => {
    if (running || sessionId === activeSessionId) {
      setSessionMenuOpen(false);
      return;
    }
    const target = sessions.find((session) => session.id === sessionId);
    if (!target) return;
    setActiveSessionId(sessionId);
    setTurns(target.turns);
    restoreRailFromTurns(target.turns);
    setSessionMenuOpen(false);
  };

  const deleteSession = (sessionId: string) => {
    if (running) return;
    if (confirmSessionDelete !== sessionId) {
      setConfirmSessionDelete(sessionId);
      return;
    }
    const remaining = sessions.filter((session) => session.id !== sessionId);
    setSessions(remaining);
    setConfirmSessionDelete(null);
    if (sessionId === activeSessionId) {
      const next = remaining[0] ?? null;
      setActiveSessionId(next?.id ?? null);
      setTurns(next?.turns ?? []);
      if (next) restoreRailFromTurns(next.turns);
      else resetRail();
    }
  };

  /** 研究项③：单轮证据导出——问题+结论+引用+版本+时间戳，客户端生成 Markdown */
  const exportTurn = (turn: Turn) => {
    downloadTextFile(evidenceFilename(turn.question), buildEvidenceMarkdown([turn]));
  };

  const exportSession = (sessionId: string) => {
    const source = sessions.find((session) => session.id === sessionId);
    const list = sessionId === activeSessionId ? turns : source?.turns ?? [];
    if (!list.length) return;
    downloadTextFile(
      evidenceFilename(makeSessionTitle(list[0]?.question ?? "")),
      buildEvidenceMarkdown(list, { title: makeSessionTitle(list[0]?.question ?? "") }),
    );
  };

  /** 研究项①：答案内角标 → 轨道定位到该轮并闪烁对应引用卡（自动展开折叠的证据轨） */
  const focusCitation = (turnId: string, rank: number) => {
    setActiveTurnId(turnId);
    setRailOpen(true);
    setCitationFocus({ rank, nonce: Date.now() });
  };

  const submit = async (event?: FormEvent, preset?: string, retryId?: string) => {
    event?.preventDefault();
    const finalQuestion = (preset ?? question).trim();
    if (!finalQuestion || running) return;

    // 研究项⑥：提交即确保存在活跃会话（新对话在首次提问时创建，避免空会话堆积）
    if (!retryId && (!activeSessionId || !sessions.some((session) => session.id === activeSessionId))) {
      const sessionId = randomId();
      const now = new Date().toISOString();
      const session: ChatSession = {
        id: sessionId,
        title: makeSessionTitle(finalQuestion),
        createdAt: now,
        updatedAt: now,
        turns: [],
      };
      setSessions((current) => [session, ...current]);
      setActiveSessionId(sessionId);
    }

    const id = retryId || randomId();
    const createdAt = new Date().toISOString();
    setTurns((current) => retryId
      ? current.map((turn) => turn.id === retryId ? { ...turn, answer: "", errorDetail: undefined, state: "streaming" } : turn)
      : [...current, { id, question: finalQuestion, answer: "", state: "streaming", queryDate: queryDate || undefined, createdAt }]);
    setQuestion("");
    dispatch({ type: "reset" });
    setActiveTurnId(null);
    setElapsed(0);
    usageRef.current = null;
    degradedRef.current = null;
    startedAtRef.current = Date.now();
    setRunning(true);
    controller.current = new AbortController();

    try {
      // M2：Assist 开关（本地状态；后端 AGENT_ASSIST_ENABLED 关闭时端点 404，
      // 前端捕获后回落到普通流并提示一次）。P0-1：澄清补充 = 新的补充问题请求
      // （question 拼接补充信息），不发送 resume_from——后端无该字段与服务端恢复。
      const streamer = assistMode ? streamAssistAgent : streamChat;
      await streamer(
        {
          question: finalQuestion,
          retrieval_strategy: strategy,
          final_top_k: topK,
          include_retrieval_trace: true,
          include_historical: false,
          graph_enabled: graphEnabled,
          graph_hops: graphHops,
          ...(queryDate ? { query_date: queryDate } : {}),
        },
        (streamEvent) => handleEvent(id, streamEvent),
        controller.current.signal,
      );
    } catch (error) {
      if ((error as Error).name === "AbortError") {
        // U1：手动中止后，轮次必须离开"生成中"状态，保留已产出的部分回答并可重试
        setTurns((current) =>
          current.map((turn) =>
            turn.id === id
              ? {
                  ...turn,
                  state: "error",
                  answer: turn.answer
                    ? `${turn.answer}\n\n（生成已手动中止，以上内容可能不完整。）`
                    : "生成已手动中止，可以重新提交。",
                  errorDetail: "用户手动中止了本次生成。",
                  resultState: "aborted",
                  steps: { ...stepsRef.current, generation: "warning" },
                  usage: usageRef.current,
                  degraded: degradedRef.current,
                  elapsedMs: startedAtRef.current ? Date.now() - startedAtRef.current : undefined,
                }
              : turn,
          ),
        );
        dispatch({ type: "error", code: "aborted" });
      } else {
        updateTurn(id, {
          answer: "回答连接中断，请重试。",
          errorDetail: (error as Error).message,
          state: "error",
          resultState: "stream_error",
        });
        dispatch({ type: "error", code: "stream_error" });
      }
    } finally {
      setRunning(false);
      controller.current = null;
    }
  };

  // U4：清空对话（两步确认，防误触）；同时移除已清空的会话条目
  const clearConversation = () => {
    if (!confirmClear) {
      setConfirmClear(true);
      return;
    }
    setTurns([]);
    resetRail();
    setConfirmClear(false);
    if (activeSessionId) {
      setSessions((current) => current.filter((session) => session.id !== activeSessionId));
      setActiveSessionId(null);
    }
  };

  const sendFeedback = async (turnId: string, rating: "helpful" | "not_helpful") => {
    const turn = turns.find((item) => item.id === turnId);
    if (!turn?.requestId || turn.feedback === "helpful" || turn.feedback === "not_helpful") return;
    try {
      await api.submitFeedback({ request_id: turn.requestId, rating });
      updateTurn(turnId, { feedback: rating });
    } catch {
      updateTurn(turnId, { feedback: "failed" });
    }
  };

  return (
    <div className="page chat-page">
      <PageHeader
        title="可信问答"
        description="基于制度内容回答问题，每个回答都会标注来源，方便追溯。"
        eyebrow="提问 · 治理式问答"
        meta={["可直接开始提问，或按 / 快速聚焦", "回答带来源与版本，可一键导出证据"]}
      />

      <div className={railOpen ? "chat-layout rail-open reveal reveal-2" : "chat-layout reveal reveal-2"}>
        <section className="conversation-panel">
          <div className="conversation-topbar">
            {/* 研究项⑥：历史会话（本地工作留痕）——新建/切换/删除/整段导出 */}
            <details
              className="session-menu"
              onToggle={(event) => setSessionMenuOpen((event.target as HTMLDetailsElement).open)}
              open={sessionMenuOpen}
            >
              <summary title="查看历史会话">
                <History size={14} />
                <span className="session-menu-title">
                  {sessions.find((session) => session.id === activeSessionId)?.title || "历史会话"}
                </span>
              </summary>
              <div className="session-menu-panel">
                <p className="session-menu-note">会话仅保存在本机浏览器，用于工作留痕；正式留存请以证据导出文件为准。</p>
                <button className="button secondary small" disabled={running} onClick={startNewSession} type="button">
                  <Plus size={14} /> 新建对话
                </button>
                {/* M3：显式迁移到服务端（检测到待迁移会话且服务端开启时显示；
                    用户确认后逐会话上传，成功即清理本地正文，保留迁移标记） */}
                {serverConversationsEnabled && sessionsPendingMigration(sessions).length ? (
                  <div className="session-migration-block">
                    <p className="session-menu-note">
                      检测到 {sessionsPendingMigration(sessions).length} 个本机会话可迁移到服务端留存。
                      迁移是主动操作：逐个上传并校验；本机副本会保留，删除需要你单独确认。
                    </p>
                    {migrating ? (
                      <p className="session-menu-note" role="status">迁移中… 已完成 {migrationDone}/{sessionsPendingMigration(sessions).length}</p>
                    ) : (
                      <button
                        className="button secondary small"
                        onClick={() => void runMigration(sessionsPendingMigration(sessions))}
                        type="button"
                      >
                        <History size={14} /> 迁移到服务端
                      </button>
                    )}
                    {migrationResult ? (
                      <p className="session-menu-note" role="status">
                        {migrationResult.failed.length
                          ? `迁移完成：成功 ${migrationResult.migrated.length} 个，失败 ${migrationResult.failed.length} 个（可重试，已迁移的不会重复）`
                          : `迁移完成：${migrationResult.migrated.length} 个会话已留存服务端；本机副本保留，可在下方会话列表中选择删除。`}
                      </p>
                    ) : null}
                  </div>
                ) : null}
                {/* 已迁移会话的“删除本地副本”单独确认入口（§8.2 修订：不随迁移自动清理） */}
                {serverConversationsEnabled && migratedSessionsWithLocalCopy(sessions).length ? (
                  <div className="session-migration-block">
                    <p className="session-menu-note">
                      {migratedSessionsWithLocalCopy(sessions).length} 个会话已留存服务端，本机副本仍在（作为缓存）。
                      需要清理本机时逐个确认删除；服务端会话不受影响。
                    </p>
                    <ul className="session-list">
                      {migratedSessionsWithLocalCopy(sessions).map((session) => (
                        <li key={`del-${session.id}`} className="session-item">
                          <span className="session-item-open">{session.title}</span>
                          <button
                            className="session-item-action"
                            onClick={() => deleteLocalCopy(session.id)}
                            title="删除本机会话副本（服务端留存不变）"
                            type="button"
                          >
                            删除本机副本
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {sessions.length ? (
                  <ul className="session-list">
                    {sessions.map((session) => (
                      <li key={session.id} className={session.id === activeSessionId ? "session-item active" : "session-item"}>
                        <button
                          className="session-item-open"
                          disabled={running}
                          onClick={() => switchSession(session.id)}
                          title={session.title}
                          type="button"
                        >
                          <strong>{session.title}</strong>
                          <small>
                            {session.turns.length} 轮 · {new Date(session.updatedAt).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}
                          </small>
                        </button>
                        <button
                          aria-label={`导出会话「${session.title}」`}
                          className="session-item-action"
                          onClick={() => exportSession(session.id)}
                          title="导出整个会话的证据材料"
                          type="button"
                        >
                          <FileDown size={14} />
                        </button>
                        <button
                          aria-label={confirmSessionDelete === session.id ? `再次点击确认删除会话「${session.title}」` : `删除会话「${session.title}」`}
                          className={confirmSessionDelete === session.id ? "session-item-action danger armed" : "session-item-action danger"}
                          disabled={running}
                          onClick={() => deleteSession(session.id)}
                          title={confirmSessionDelete === session.id ? "再点一次确认删除" : "删除会话"}
                          type="button"
                        >
                          {confirmSessionDelete === session.id ? "确认?" : <Trash2 size={14} />}
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="session-menu-empty">还没有历史会话。提问后会自动保存到这里。</p>
                )}
              </div>
            </details>
            <span className="conversation-count">{turns.length ? `${turns.length} 轮对话` : "新对话"}</span>
            {turns.length > 0 && !running ? (
              <button
                className={confirmClear ? "button danger small" : "button ghost small"}
                onClick={clearConversation}
                type="button"
                aria-label={confirmClear ? "再次点击确认清空对话" : "清空对话"}
              >
                <Trash2 size={14} /> {confirmClear ? "再点一次确认清空" : "清空对话"}
              </button>
            ) : null}
            {/* P1-P3（走查）：长对话内存引导——50 轮后提示导出并清空 */}
            {turns.length >= 50 ? (
              <p className="long-conversation-hint" role="status">
                本会话已有 {turns.length} 轮。长时间使用会变慢——建议先导出证据存档，再清空对话。
              </p>
            ) : null}
          </div>
          <div className="query-controls">
            <label>
              <span>检索方式</span>
              <select aria-label="检索方式" value={strategy} onChange={(event) => setStrategy(event.target.value as ChatRequest["retrieval_strategy"])}>
                <option value="auto">自动匹配（推荐）</option>
                <option value="hybrid">综合检索</option>
                <option value="hybrid_rerank">综合检索 + 精准排序</option>
                <option value="dense">按意思检索</option>
                <option value="bm25">按关键词检索</option>
              </select>
            </label>
            <label>
              <span>引用数量</span>
              <select aria-label="引用数量" value={topK} onChange={(event) => setTopK(Number(event.target.value))}>
                {[3, 5, 8, 10].map((value) => (
                  <option key={value} value={value}>
                    Top {value}
                  </option>
                ))}
              </select>
            </label>
            {/* UI 审计 #3：受控关系扩展与版本类检索参数折叠进"高级设置"，默认收起 */}
            <details className="advanced-settings">
              <summary>高级设置</summary>
              <div className="advanced-settings-body">
                <label className="switch-control" title="开启后，例外/冲突类问题会用人工确认过的制度关系补充证据；默认关闭">
                  <span>关联制度扩展</span>
                  <button
                    aria-pressed={graphEnabled}
                    className={graphEnabled ? "switch on" : "switch"}
                    onClick={() => setGraphEnabled((value) => !value)}
                    type="button"
                  >
                    <i />
                  </button>
                </label>
                <label title="深入追溯用于版本变化与冲突来源追溯，速度稍慢；默认标准">
                  <span>追溯深度</span>
                  <select
                    aria-label="追溯深度"
                    value={graphHops}
                    disabled={!graphEnabled}
                    onChange={(event) => setGraphHops(Number(event.target.value))}
                  >
                    <option value={1}>标准（默认）</option>
                    <option value={2}>深入（追溯版本变化与冲突）</option>
                  </select>
                </label>
                <label title="按此日期判断制度是否生效/过期，用于版本与冲突判定">
                  <span>查询日期（默认今天）</span>
                  <input
                    aria-label="查询日期"
                    type="date"
                    value={queryDate}
                    onChange={(event) => setQueryDate(event.target.value)}
                  />
                </label>
              </div>
            </details>
          </div>

          <div className="conversation-stream">
            {/* 研究项⑨：onboarding 减重——从整卡改为输入区上方一行可折叠提示，不再占据首屏 */}
            {turns.length === 0 ? (
              <details className="onboarding-inline">
                <summary>
                  <Sparkles size={15} />
                  <span>第一次用？三步上手</span>
                </summary>
                <ol className="onboarding-steps">
                  {ONBOARDING_STEPS.map((step) => (
                    <li key={step.title}>
                      <strong>{step.title}</strong>
                      <span>{step.detail}</span>
                    </li>
                  ))}
                </ol>
              </details>
            ) : null}
            {turns.length === 0 ? (
              <div className="chat-intro">
                <span className="intro-seal">可审计</span>
                <h2>问一个和制度相关的问题。</h2>
                <p>回答会区分现行版本、历史规则与例外情形，拿不准时会明确告诉你。</p>
                <div className="quick-question-list">
                  {QUICK_QUESTIONS.map((item) => (
                    <button key={item} onClick={() => void submit(undefined, item)} type="button">
                      <span>{item}</span>
                      <ArrowUp size={16} />
                    </button>
                  ))}
                </div>
                {/* 5 任务可用性脚本 → 能力演示（引导面板：每个脚本任务一条
                    一键路径；docs/ui/AGENT-UI-DESIGN-SPEC.md §7） */}
                <section className="guided-capabilities" aria-label="这个工作台能做什么">
                  <h3>上手路径</h3>
                  <ol>
                    {GUIDED_TASKS.map((task) => (
                      <li key={task.scriptId}>
                        <strong>{task.title}</strong>
                        <span>{task.description}</span>
                        {task.starterQuestion ? (
                          <button
                            className="button secondary small"
                            onClick={() => void submit(undefined, task.starterQuestion)}
                            type="button"
                          >
                            试一下
                          </button>
                        ) : (
                          <small>{task.actionHint}</small>
                        )}
                      </li>
                    ))}
                  </ol>
                </section>
              </div>
            ) : (
              turns.map((turn) => (
                <article className={`conversation-turn${activeTurnId === turn.id ? " selected" : ""}`} key={turn.id}>
                  <div className="user-question">
                    <span>问</span>
                    <p>{turn.question}</p>
                  </div>
                  <div className={`assistant-answer ${turn.state}`}>
                    <div className="answer-heading">
                      <Sparkles size={17} />
                      <span>MindGraph 结论</span>
                      {turn.state === "streaming" ? (
                        <>
                          <LoaderCircle className="spin" size={16} />
                          {/* I3：长耗时生成必须有进度反馈；>15s 给出预期管理 */}
                          <span className="answer-timer">
                            已用时 {elapsed}s{elapsed >= 15 ? " · 复杂问题需要更久，请耐心等待" : ""}
                          </span>
                        </>
                      ) : (
                        /* U6：任意历史轮次都可以把证据轨定位到自己（并自动展开折叠的证据轨） */
                        <button
                          className={activeTurnId === turn.id ? "answer-evidence-toggle active" : "answer-evidence-toggle"}
                          onClick={() => {
                            setActiveTurnId((current) => (current === turn.id ? null : turn.id));
                            setRailOpen(true);
                          }}
                          type="button"
                          aria-pressed={activeTurnId === turn.id}
                        >
                          {activeTurnId === turn.id ? "正在查看本回答的证据" : "查看本回答的证据"}
                        </button>
                      )}
                    </div>
                    {/* ── M2 Assist 增量渲染（UI-G 设计规格 §3；flag off 时不出现） ── */}
                    {turn.plan ? (
                      <p className="agent-execution-summary">
                        {turn.toolCalls?.length
                          ? turn.toolCalls.every((call) => call.status !== "running")
                            ? `✓ 已完成 ${turn.toolCalls.length} 步核对 · 结论经逐步取证`
                            : `正在执行 ${turn.toolCalls.length} 步核对…`
                          : `已制定 ${turn.plan.steps.length} 个核对步骤`}
                      </p>
                    ) : null}
                    {turn.fallbackReason ? (
                      <div className="rail-degraded loop-fallback" role="status">
                        <AlertTriangle size={14} />
                        <span>{turn.fallbackReason}</span>
                      </div>
                    ) : null}
                    {turn.clarification && !turn.clarificationAnswered ? (
                      <ClarificationCard
                        clarification={turn.clarification}
                        onResolved={(answers) => {
                          updateTurn(turn.id, { clarificationAnswered: true });
                          const joined = Object.values(answers).filter(Boolean).join("；");
                          void submit(undefined, `${turn.question}（补充：${joined}）`);
                        }}
                      />
                    ) : null}
                    {turn.citationIntegrity && !turn.citationIntegrity.passed && turn.citationIntegrity.applicable ? (
                      <div className="citation-integrity-notice" role="alert">
                        <AlertTriangle size={14} />
                        <span>回答未通过引用校验：本次只提供证据原文，避免误导。</span>
                        {(turn.citations?.length ?? 0) > 0 ? (
                          <button
                            className="button secondary"
                            onClick={() => focusCitation(turn.id, turn.citations?.[0]?.final_rank ?? 1)}
                            type="button"
                          >
                            查看证据原文
                          </button>
                        ) : null}
                      </div>
                    ) : null}
                    {/* 研究项①⑤：Markdown 渲染 + [citation-N] 内联锚点（点击定位证据链） */}
                    {turn.answer ? (
                      <AnswerBody
                        citations={turn.citations ?? []}
                        onCitationClick={(rank) => focusCitation(turn.id, rank)}
                        streaming={turn.state === "streaming"}
                        text={turn.answer}
                      />
                    ) : (
                      <p>正在核对制度与证据……</p>
                    )}
                    {/* 研究项②：引用了非现行有效版本时，结论旁必须给出显式警示 */}
                    {turn.state === "complete" && (turn.citations?.length ?? 0) > 0 ? (
                      <VersionWarning asOf={turn.queryDate} citations={turn.citations ?? []} />
                    ) : null}
                    {/* P1-X5（走查修复）：版本冲突轮的答案卡内联冲突版本族——
                        治理的核心展示位从折叠证据轨前置到结论旁，可见即卖点 */}
                    {turn.state === "complete" && turn.resultState === "conflicting_evidence" ? (
                      <InlineConflictCard turn={turn} />
                    ) : null}
                    {/* M0：确定性引用保真核验——回答标注全部命中本次引用集才通过；
                        warning-first：不阻断，只在失真时给出可行动警示 */}
                    {turn.state === "complete" && turn.citationFidelity === false ? (
                      <FidelityWarning turn={turn} />
                    ) : null}
                    {turn.state === "complete" ? (
                      <div className="answer-meta-line">
                        {turn.elapsedMs != null ? <span>耗时 {(turn.elapsedMs / 1000).toFixed(1)}s</span> : null}
                        {turn.citationFidelity === true ? (
                          <span className="fidelity-ok" title="回答中的引用标注全部命中本次返回的证据">
                            <Check size={13} /> 引用标注已核验
                          </span>
                        ) : null}
                        {turn.usage && (turn.usage.input_tokens != null || turn.usage.output_tokens != null) ? (
                          <details className="answer-usage-fold">
                            <summary>本次用量</summary>
                            <span>
                              {turn.usage.input_tokens != null ? `输入 ${turn.usage.input_tokens} tokens` : ""}
                              {turn.usage.input_tokens != null && turn.usage.output_tokens != null ? " · " : ""}
                              {turn.usage.output_tokens != null ? `输出 ${turn.usage.output_tokens} tokens` : ""}
                            </span>
                          </details>
                        ) : null}
                        {turn.degraded ? (
                          <span className="degraded-badge">
                            <AlertTriangle size={12} /> 回答质量已降低：{turn.degraded}
                          </span>
                        ) : null}
                      </div>
                    ) : null}
                    {turn.state === "error" ? (
                      <div className="answer-actions">
                        <button className="button secondary" onClick={() => void submit(undefined, turn.question, turn.id)} type="button">
                          <RotateCcw size={15} /> 重试
                        </button>
                        {turn.errorDetail ? <details className="technical-details"><summary>查看技术详情</summary><code>{turn.errorDetail}</code></details> : null}
                      </div>
                    ) : null}
                    {turn.state === "complete" ? (
                      <div className="answer-actions">
                        {/* 研究项③：一键导出问题+结论+引用+版本+时间戳的可提交材料 */}
                        <button className="button secondary" onClick={() => exportTurn(turn)} type="button">
                          <FileDown size={14} /> 导出证据
                        </button>
                      </div>
                    ) : null}
                    {turn.state === "complete" && turn.requestId ? (
                      <div className="answer-actions feedback-row" aria-label="回答反馈">
                        {turn.feedback === "helpful" || turn.feedback === "not_helpful" ? (
                          <span className="feedback-done">已记录，感谢反馈 — 它会进入质量账本帮助改进。</span>
                        ) : turn.feedback === "failed" ? (
                          <>
                            <span className="feedback-failed">反馈暂时没能保存。</span>
                            <button className="button secondary feedback-button" onClick={() => void sendFeedback(turn.id, "helpful")} type="button">重试</button>
                          </>
                        ) : (
                          <>
                            <span className="feedback-ask">这个回答解决了你的问题吗？</span>
                            <button
                              className="button secondary feedback-button"
                              onClick={() => void sendFeedback(turn.id, "helpful")}
                              type="button"
                              aria-label="有帮助"
                            >
                              <ThumbsUp size={14} /> 有帮助
                            </button>
                            <button
                              className="button secondary feedback-button"
                              onClick={() => void sendFeedback(turn.id, "not_helpful")}
                              type="button"
                              aria-label="没帮助"
                            >
                              <ThumbsDown size={14} /> 没帮助
                            </button>
                          </>
                        )}
                      </div>
                    ) : null}
                    {/* 研究项⑧：回答后推荐追问——基于本轮命中的制度（文档名/版本/时效）生成，不是通用模板 */}
                    {turn.state === "complete" && !running ? (
                      <FollowUpSuggestions onSubmit={(next) => void submit(undefined, next)} turn={turn} />
                    ) : null}
                  </div>
                </article>
              ))
            )}
          </div>

          <form className="question-composer" onSubmit={(event) => void submit(event)}>
            <textarea
              ref={composerRef}
              aria-label="制度问题"
              id="chat-composer"
              maxLength={2000}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void submit();
                }
              }}
              placeholder="例如：逾期 45 天提交的费用，应该适用 30 天还是旧版 60 天规则？"
              rows={3}
              value={question}
            />
            <div className="composer-foot">
              <span>{question.length}/2000</span>
              {/* M2：Assist 本地开关（服务端未开启时 404 探测已提示，不再让用户踩空） */}
              <label className="assist-toggle" title="开启后系统会先核对版本与关联制度，再综合回答：更慢一些，但每一步的依据都能追溯">
                <input
                  checked={assistMode}
                  disabled={assistAvailable === false}
                  onChange={(event) => setAssistMode(event.target.checked)}
                  type="checkbox"
                />
                深度核对
                {assistAvailable === false ? (
                  <span className="assist-unavailable" role="note">服务端未开启</span>
                ) : null}
              </label>
              {running ? (
                /* U9：中止血用停止图标（Square），RotateCcw 保留给"重试" */
                <button className="button secondary" onClick={() => controller.current?.abort()} type="button">
                  <Square size={13} /> 中止
                </button>
              ) : (
                <button className="button primary" disabled={!question.trim()} type="submit">
                  提问 <ArrowUp size={16} />
                </button>
              )}
            </div>
          </form>
        </section>

        <aside className={railOpen ? "evidence-rail" : "evidence-rail collapsed"} ref={railRef} aria-label="回答依据">
          {railOpen ? (
            <>
          <div className="rail-heading">
            <h2>回答依据</h2>
            {selectedTurn ? <p className="rail-pinned">已定位到所选轮次 · 再次点击该轮「查看本回答的证据」可返回最新</p> : null}
            <button className="rail-collapse" onClick={() => setRailOpen(false)} type="button" aria-label="收起回答依据面板">
              <PanelRightClose size={16} />
            </button>
          </div>

          <div className="trace-steps">
            <TraceStep label="确认问题范围" state={railSteps.scope} />
            <TraceStep label="查找相关制度" state={railSteps.retrieval} />
            <TraceStep label="生成回答" state={railSteps.generation} last />
          </div>

          {/* M2：执行步骤/执行记录（默认折叠，flag 或轮次无数据时不渲染） */}
          {selectedTurn?.plan ?? (!activeTurnId && latestAssistPlan) ? (
            <section className="rail-section assist-section">
              <details className="technical-details assist-fold">
                <summary>
                  执行步骤（{((selectedTurn?.plan ?? latestAssistPlan)?.steps.length ?? 0)}）
                </summary>
                <ol className="assist-plan-list">
                  {(selectedTurn?.plan ?? latestAssistPlan)?.steps.map((step) => {
                    const call = (selectedTurn?.toolCalls ?? []).find((item) => item.step === step.name);
                    return (
                      <li data-status={call?.status ?? "pending"} key={step.name}>
                        <span>{call?.status === "ok" ? "✓" : call?.status === "running" ? "…" : call ? "×" : "·"}</span>
                        {step.label}
                      </li>
                    );
                  })}
                </ol>
              </details>
              {(selectedTurn?.toolCalls ?? latestAssistToolCalls)?.length ? (
                <details className="technical-details assist-fold">
                  <summary>执行记录（{(selectedTurn?.toolCalls ?? latestAssistToolCalls)?.length}）</summary>
                  <ul className="assist-tool-list">
                    {(selectedTurn?.toolCalls ?? latestAssistToolCalls)?.map((call) => (
                      <li key={`${call.step}-${call.label}`}>
                        <span className={`assist-tool-status ${call.status}`}>
                          {call.status === "running" ? "进行中" : call.status === "ok" ? "完成" : call.status === "timeout" ? "超时" : call.status === "denied" ? "需权限" : "失败"}
                        </span>
                        <span className="assist-tool-label">{call.label}</span>
                        {call.latency_ms != null ? <small>{(call.latency_ms / 1000).toFixed(1)}s</small> : null}
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}
            </section>
          ) : null}

          {/* I4：降级时给出显式横幅，说明降级原因，而不是静默改变行为 */}
          {railDegraded ? (
            <div className="rail-degraded" role="status">
              <AlertTriangle size={14} />
              <span>本次回答质量已降低：{railDegraded}</span>
            </div>
          ) : null}

          <section className="rail-section route-section">
            <div className="rail-section-title">
              <Gauge size={16} />
              <strong>检索方式</strong>
              <span>{railRoute ? (railRoute.mode === "adaptive" ? "自动" : "手动") : "—"}</span>
            </div>
            {routeView ? (
              /* UI 审计 #4：路由决策细节默认折叠，仅保留一行摘要 */
              <details className="technical-details route-decision-fold">
                <summary>{routeView.routeLabel} · {routeView.strategyLabel}{routeView.degraded ? " · 已降低质量" : ""}</summary>
                <div className="route-decision-card">
                  <div className="route-decision-heading">
                    <strong>{routeView.routeLabel}</strong>
                    <span>{routeView.strategyLabel}</span>
                  </div>
                  <p>{routeView.graphLabel}</p>
                  <ul>{routeView.reasonLabels.map((reason) => <li key={reason}>{reason}</li>)}</ul>
                  <small>检索路径：{routeView.strategyLabel}</small>
                  {routeView.degraded ? (
                    <div className="route-decision-tags">
                      <span>已降低质量</span>
                    </div>
                  ) : null}
                </div>
              </details>
            ) : (
              <p className="rail-placeholder">提交问题后，这里会说明系统用了哪种检索方式。</p>
            )}
          </section>

          <section className="rail-section">
            <div className="rail-section-title">
              <BookOpenCheck size={16} />
              <strong>引用来源</strong>
              <span>{railCitations.length}</span>
            </div>
            {railCitations.length ? (
                <>
                  <ol className="citation-list">
                    {railCitations.map((citation) => {
                      const validity = citationValidity(citation, railAsOf);
                      return (
                        <li data-citation-rank={citation.final_rank} key={citation.citation_id}>
                          <span className="citation-rank">{citation.final_rank}</span>
                          <div>
                            <div className="citation-heading-row">
                              <strong>{citation.document_name}</strong>
                              {/* Research item ②: validity badge on the evidence card (current / draft / expired / unregistered) */}
                              <span className={`citation-validity-pill ${validity.level}`} title={validity.detail}>
                                {validity.label}
                              </span>
                            </div>
                            <small>{citation.section_path || "正文"}</small>
                            {citation.policy_key || citation.document_version || citation.effective_from ? (
                              <span className="citation-policy-meta">
                                {citation.policy_key ? `${citation.policy_key} · ` : ""}
                                {citation.document_version ? `V${citation.document_version}` : "版本未登记"}
                                {citation.effective_from ? ` · 生效日期：${citation.effective_from}` : ""}
                              </span>
                            ) : null}
                            <p>{citation.excerpt}</p>
                          </div>
                        </li>
                      );
                    })}
                  </ol>
                  {railFidelity === false ? (
                    <p className="rail-fidelity-warning">
                      <AlertTriangle size={13} /> 本回答存在未命中引用集的标注，请以引文原文为准。
                    </p>
                  ) : null}
                </>
              ) : railResultState === "out_of_scope" ? (
              <p className="rail-placeholder">这个问题不在制度范围内，已停止回答。</p>
            ) : railResultState === "permission_denied" ? (
              <p className="rail-placeholder"><ShieldQuestion size={14} /> 你当前的账号权限看不到相关制度，因此没有任何引用。请联系管理员开通对应工作区/部门。</p>
            ) : railResultState === "insufficient_evidence" ? (
              <p className="rail-placeholder">检索到了问题，但未找到足够的制度证据，因此没有可展示引用。</p>
            ) : railResultState === "conflicting_evidence" ? (
              <p className="rail-placeholder">系统已因版本冲突停止生成，因此不会展示引用结果。</p>
            ) : railResultState === "system_error" ? (
              <p className="rail-placeholder">本次请求发生系统错误，没有生成可展示的引用。</p>
            ) : railResultState === "aborted" ? (
              <p className="rail-placeholder">本次生成已手动中止，未产生完整引用。可回到对话重新提交。</p>
            ) : (
              <p className="rail-placeholder">提交问题后，这里会显示实际引用的制度。</p>
            )}
          </section>

          {conflictItems.length ? (
            <section className="rail-section conflict-section" aria-label="制度版本冲突">
              <div className="rail-section-title conflict-title">
                <AlertTriangle size={16} />
                <strong>有效版本冲突</strong>
                <span>{conflictItems.length}</span>
              </div>
              <p className="conflict-guidance">多个版本的规则可能同时适用，已暂停回答。请确认按哪个版本判断。</p>
              <div className="conflict-list">
                {conflictItems.map((item) => (
                  <article className="conflict-item" key={item.key}>
                    <div className="conflict-item-heading">
                      <strong>{item.title}</strong>
                      <span>V{item.version}</span>
                    </div>
                    <dl>
                      <div><dt>制度族</dt><dd>{item.policyKey}</dd></div>
                      <div><dt>查询日期</dt><dd>{item.asOf}</dd></div>
                      <div><dt>有效期</dt><dd>{item.period}</dd></div>
                      <div><dt>责任人</dt><dd>{item.owner}</dd></div>
                      <div><dt>来源</dt><dd>{item.vaultPath}</dd></div>
                    </dl>
                  </article>
                ))}
              </div>
            </section>
          ) : railResultState === "conflicting_evidence" ? (
            <section className="rail-section conflict-section" aria-label="制度版本冲突">
              <div className="rail-section-title conflict-title">
                <AlertTriangle size={16} />
                <strong>有效版本冲突</strong>
                <span>1</span>
              </div>
              <p className="conflict-guidance">系统已停止生成，但当前轮次没有返回可枚举的冲突版本明细。</p>
            </section>
          ) : null}

          <section className="rail-section">
            <div className="rail-section-title">
              <GitBranch size={16} />
              <strong>关联制度</strong>
              <span>{railTrace?.graph_links.length || 0}</span>
            </div>
            {railTrace?.graph_links.length ? (
              <div className="graph-link-list">
                {railTrace.graph_links.map((link, index) => (
                  <div className="graph-link" key={`${link.source_note_id}-${link.target_note_id}-${index}`}>
                    <span>{link.source_title || "来源文档"}</span>
                    <i>{link.relation_type}</i>
                    <span>{link.target_title || "关联文档"}</span>
                    {link.evidence_chunk_id || link.evidence_span ? <small>证据：{link.evidence_section || link.evidence_chunk_id || link.evidence_span}</small> : null}
                  </div>
                ))}
              </div>
            ) : (
              <p className="rail-placeholder">这里只显示已确认的关联制度，可用来扩展检索范围。</p>
            )}
          </section>

          {/* I5：token 用量进证据链，审计视角下每次回答的成本可追溯 */}
          {railUsage && (railUsage.input_tokens != null || railUsage.output_tokens != null) ? (
            <section className="rail-section rail-usage-section">
              <div className="rail-section-title">
                <Gauge size={16} />
                <strong>本次用量</strong>
                <span>{railUsage.total_tokens != null ? `${railUsage.total_tokens} tokens` : "—"}</span>
              </div>
              <p className="rail-usage-line">
                {railUsage.input_tokens != null ? `输入 ${railUsage.input_tokens} tokens` : ""}
                {railUsage.input_tokens != null && railUsage.output_tokens != null ? " · " : ""}
                {railUsage.output_tokens != null ? `输出 ${railUsage.output_tokens} tokens` : ""}
                {railUsage.usage_source === "unavailable" ? "（模型服务未上报用量）" : ""}
              </p>
            </section>
          ) : null}
            </>
          ) : (
            <button
              className="rail-collapsed-toggle"
              onClick={() => setRailOpen(true)}
              type="button"
              aria-label="展开回答依据面板"
            >
              <BookOpenCheck size={16} />
              <span>回答依据</span>
              {railCitations.length ? <em>{railCitations.length}</em> : null}
            </button>
          )}
        </aside>
      </div>
    </div>
  );
}

function TraceStep({ label, state, last = false }: { label: string; state: StepState; last?: boolean }) {
  return (
    <div className={`trace-step ${state}`}>
      <span className="trace-step-icon">
        {state === "running" ? (
          <LoaderCircle className="spin" size={15} />
        ) : state === "done" ? (
          <Check size={15} />
        ) : state === "warning" ? (
          <AlertTriangle size={14} />
        ) : (
          <Circle size={11} />
        )}
      </span>
      <span>{label}</span>
      {!last ? <i /> : null}
    </div>
  );
}

/**
 * 研究项②：版本时效警示。
 * stale（已失效/已被替代/已归档/有效期早于查询日）→ 醒目横幅，逐条列出；
 * 仅有 caution（草案/尚未生效/状态未登记）→ 轻量提示行。
 */
function VersionWarning({ citations, asOf }: { citations: Citation[]; asOf?: string | null }) {
  const { stale, caution } = summarizeCitationValidity(citations, asOf);
  if (stale.length) {
    return (
      <div className="version-warning stale" role="alert">
        <AlertTriangle size={16} />
        <div>
          <strong>
            {stale.length} 条引用来自已失效或已更新的制度版本，采纳前请核对现行版本。
          </strong>
          <ul>
            {stale.map(({ citation, validity }) => (
              <li key={citation.citation_id}>
                《{citation.document_name}》
                {citation.document_version ? ` V${citation.document_version}` : ""} · {validity.label}
                {validity.level === "stale" && citation.policy_status === "superseded"
                  ? "（存在更新版本）"
                  : ""}
              </li>
            ))}
          </ul>
        </div>
      </div>
    );
  }
  if (caution.length) {
    return (
      <div className="version-warning caution">
        <AlertTriangle size={14} />
        <span>
          引用时效提示：
          {caution
            .map(({ citation, validity }) => `《${citation.document_name}》${validity.label}`)
            .join("；")}
          。
        </span>
      </div>
    );
  }
  return null;
}

/** M0：引用保真核验警示。后端已完成确定性检查（warning-first），
 *  失真时这里把缺失标注还原成用户可行动的提示，不阻断回答。 */
function FidelityWarning({ turn }: { turn: Turn }) {
  const marks = citationFidelityMarks(turn.trace?.warnings);
  return (
    <div className="fidelity-warning" role="status">
      <AlertTriangle size={14} />
      <span>
        引用保真警示：回答中有{marks ? `引用标注 ${marks}` : "引用标注"}未命中本次返回的证据，请核对后使用。
      </span>
    </div>
  );
}

/**
 * 研究项⑧：回答后推荐追问。基于本轮命中的制度证据生成（文档名/版本/时效），
 * 而不是与问题无关的通用模板；没有引用时不显示。
 */
function FollowUpSuggestions({ turn, onSubmit }: { turn: Turn; onSubmit: (question: string) => void }) {
  const cites = turn.citations ?? [];
  if (!cites.length) return null;
  const docs = [...new Set(cites.map((citation) => citation.document_name).filter(Boolean))];
  const primary = docs[0];
  const suggestions: string[] = [];
  if (primary && cites.some((citation) => citation.document_version)) {
    suggestions.push(`《${primary}》在此之前的历史版本有哪些变化？`);
  }
  if (docs.length > 1) {
    suggestions.push(`《${docs[0]}》和《${docs[1]}》在适用上是什么关系？`);
  }
  if (primary) {
    suggestions.push(`《${primary}》有哪些例外情形或适用边界？`);
  }
  if (cites.some((citation) => citation.effective_to || citation.policy_status === "superseded")) {
    suggestions.push("这条制度目前现行有效的是哪个版本？");
  }
  const finalSuggestions = suggestions.slice(0, 3);
  if (!finalSuggestions.length) return null;
  return (
    <div className="followup-chips">
      <span className="followup-label">继续追问</span>
      {finalSuggestions.map((item) => (
        <button key={item} onClick={() => onSubmit(item)} type="button">
          {item}
        </button>
      ))}
    </div>
  );
}

/** M2：澄清卡（UI-G 设计规格 §4.2；P0-1 诚实化）——提交即"新的补充问题请求"，
 * 补充信息拼进 question 重新核对；不声称恢复服务端上一轮执行，不发送 resume_from */
function ClarificationCard({
  clarification,
  onResolved,
}: {
  clarification: AssistClarification;
  onResolved: (answers: Record<string, string>) => void;
}) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const expired = clarification.expires_at ? new Date(clarification.expires_at).getTime() < Date.now() : false;
  const allFilled = clarification.questions.every((item) => (answers[item] ?? "").trim().length > 0);

  if (expired) {
    return (
      <div className="clarification-card expired" role="status">
        <strong>补充信息已过期</strong>
        <span>补充有时效（30 分钟内有效），请重新提问。</span>
      </div>
    );
  }

  return (
    <div className="clarification-card" aria-live="polite">
      <strong>为了准确回答，请补充 {clarification.questions.length} 个信息</strong>
      <span className="clarification-context">提交后将基于补充信息重新核对制度依据，作为新的问题处理。</span>
      <ol>
        {clarification.questions.map((item) => (
          <li key={item}>
            <label htmlFor={`clarify-${clarification.clarification_id}-${item}`}>{item}</label>
            <input
              id={`clarify-${clarification.clarification_id}-${item}`}
              onChange={(event) => setAnswers((current) => ({ ...current, [item]: event.target.value }))}
              type="text"
              value={answers[item] ?? ""}
            />
          </li>
        ))}
      </ol>
      <button className="button primary" disabled={!allFilled} onClick={() => onResolved(answers)} type="button">
        补充并继续
      </button>
    </div>
  );
}

/** P1-X5（走查修复）：冲突轮的答案卡内联冲突版本族。
 * 数据与右侧证据轨的 conflict-section 同源（turn.trace.policy_conflicts），
 * 治理卖点前置：系统为何停止回答、冲突在哪、找谁裁决——一眼可见。 */
function InlineConflictCard({ turn }: { turn: Turn }) {
  const items = policyConflictItems(turn.trace ?? null);
  if (!items.length) return null;
  return (
    <div className="inline-conflict-card" role="alert">
      <div className="inline-conflict-head">
        <AlertTriangle size={15} />
        <strong>系统已停止回答：同一制度在查询日期存在多个有效版本</strong>
      </div>
      <ul>
        {items.map((item) => (
          <li key={item.key}>
            <span className="conflict-doc">{item.title}</span>
            <span className="conflict-meta">
              {item.version} · 生效 {item.period} · {item.owner}
            </span>
          </li>
        ))}
      </ul>
      <p className="conflict-next">
        请确认按哪个版本判断；确认后可带日期重新提问（例如「按 {turn.queryDate || "2026-09-01"} 的有效版本，……」）。
      </p>
    </div>
  );
}
