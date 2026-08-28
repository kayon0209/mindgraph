import { FormEvent, useEffect, useRef, useState } from "react";
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
import { api, streamChat } from "../lib/api";
import { citationValidity, summarizeCitationValidity } from "../lib/citation-status";
import { buildEvidenceMarkdown, downloadTextFile, evidenceFilename } from "../lib/export-evidence";
import { completionGenerationState, completionViewState, policyConflictItems } from "../lib/policy-conflicts";
import { routeDecisionView } from "../lib/route-decision";
import type { AnswerResult, Citation, ChatRequest, RetrievalTrace, RouteDecision, StreamEvent, UsageInfo } from "../types";
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
  elapsedMs?: number;
  /** 版本时效判定所需的查询日期（缺省按今天）与轮次创建时间 */
  queryDate?: string;
  createdAt?: string;
  /** 证据导出需要记录实际使用的模型与索引版本 */
  model?: string;
  indexVersion?: string | null;
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

const INITIAL_STEPS: Record<string, StepState> = {
  scope: "waiting",
  retrieval: "waiting",
  generation: "waiting",
};

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
  const [graphHops, setGraphHops] = useState(() => loadSettings().graphHops);
  const [queryDate, setQueryDate] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [trace, setTrace] = useState<RetrievalTrace | null>(null);
  const [routeDecision, setRouteDecision] = useState<RouteDecision | null>(null);
  const [resultState, setResultState] = useState<string | null>(null);
  const [steps, setSteps] = useState(INITIAL_STEPS);
  const [running, setRunning] = useState(false);
  // I5：展示本次生成的 token 用量；I4：降级原因可见
  const [usage, setUsage] = useState<UsageInfo | null>(null);
  const [degradedReason, setDegradedReason] = useState<string | null>(null);
  // U6：证据链轨道定位到的轮次（null = 跟随最新一轮）
  const [activeTurnId, setActiveTurnId] = useState<string | null>(null);
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
    if (event.event === "request_started") {
      setSteps({ scope: "running", retrieval: "waiting", generation: "waiting" });
    }
    if (event.event === "scope_check_completed") {
      setSteps((current) => ({ ...current, scope: data.out_of_scope ? "warning" : "done" }));
    }
    if (event.event === "retrieval_started") {
      setSteps((current) => ({ ...current, retrieval: "running" }));
    }
    if (event.event === "retrieval_routed") {
      setRouteDecision(data as unknown as RouteDecision);
    }
    if (event.event === "retrieval_completed" || event.event === "rerank_completed") {
      setSteps((current) => ({ ...current, retrieval: "done" }));
    }
    if (event.event === "generation_started") {
      setSteps((current) => ({ ...current, generation: "running" }));
    }
    if (event.event === "answer_delta" && typeof data.text === "string") {
      appendAnswer(turnId, data.text);
    }
    if (event.event === "citations" && Array.isArray(data.citations)) {
      setCitations(data.citations as Citation[]);
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
      setUsage(parsed);
    }
    if (event.event === "degraded" || event.event === "policy_conflict_detected") {
      // I4：降级不再只是一个隐藏的步骤状态，原因要对用户可见
      const reason = typeof data.reason === "string" && data.reason ? data.reason : null;
      degradedRef.current = reason;
      setDegradedReason(reason);
      setSteps((current) => ({ ...current, generation: "warning" }));
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
        steps: finalSteps,
        usage: usageRef.current,
        degraded: degradedRef.current ?? (result.degraded ? result.degradation_reason || "已降级" : null),
        elapsedMs: startedAtRef.current ? Date.now() - startedAtRef.current : undefined,
        model: result.model,
        indexVersion: result.index_version ?? null,
      });
      setCitations(result.citations || []);
      setTrace(result.retrieval_trace || null);
      setRouteDecision(result.retrieval_trace?.route_decision || null);
      setResultState(completionViewState(result));
      setSteps(finalSteps);
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
      setResultState(code);
      setSteps((current) => ({ ...current, retrieval: "warning" }));
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
  const conflictItems = policyConflictItems(railTrace);
  const routeView = railRoute ? routeDecisionView(railRoute) : null;
  // 研究项②：版本时效判定基准日——所选轮次的查询日期，未选时跟随当前设置
  const railAsOf = selectedTurn ? selectedTurn.queryDate ?? null : queryDate || null;

  // 研究项⑥：切换/恢复会话时，把证据链轨道恢复到该会话最后一轮的状态
  const restoreRailFromTurns = (list: Turn[]) => {
    const last = [...list].reverse().find((item) => item.state === "complete" || item.state === "error");
    setCitations(last?.citations ?? []);
    setTrace(last?.trace ?? null);
    setRouteDecision(last?.route ?? null);
    setResultState(last?.resultState ?? null);
    setSteps(last?.steps ?? INITIAL_STEPS);
    setUsage(last?.usage ?? null);
    setDegradedReason(last?.degraded ?? null);
    setActiveTurnId(null);
    setCitationFocus(null);
  };

  const resetRail = () => {
    setCitations([]);
    setTrace(null);
    setRouteDecision(null);
    setResultState(null);
    setSteps(INITIAL_STEPS);
    setUsage(null);
    setDegradedReason(null);
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

  /** 研究项①：答案内角标 → 轨道定位到该轮并闪烁对应引用卡 */
  const focusCitation = (turnId: string, rank: number) => {
    setActiveTurnId(turnId);
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
    setCitations([]);
    setTrace(null);
    setRouteDecision(null);
    setResultState(null);
    setSteps(INITIAL_STEPS);
    setUsage(null);
    setDegradedReason(null);
    setActiveTurnId(null);
    setElapsed(0);
    usageRef.current = null;
    degradedRef.current = null;
    startedAtRef.current = Date.now();
    setRunning(true);
    controller.current = new AbortController();

    try {
      await streamChat(
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
        setResultState("aborted");
      } else {
        updateTurn(id, {
          answer: "回答连接中断，请重试。",
          errorDetail: (error as Error).message,
          state: "error",
          resultState: "stream_error",
        });
        setResultState("stream_error");
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
        eyebrow="基于制度证据的问答"
        title="先给结论，再交付证据"
        description="面向制度判断，不追求聊天感。每个回答都必须留下来源、版本与检索轨迹。"
      />

      <div className="chat-layout reveal reveal-2">
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
          </div>
          <div className="query-controls">
            <label>
              <span>检索方式</span>
              <select aria-label="检索方式" value={strategy} onChange={(event) => setStrategy(event.target.value as ChatRequest["retrieval_strategy"])}>
                <option value="auto">自动匹配（推荐）</option>
                <option value="hybrid">混合检索</option>
                <option value="hybrid_rerank">混合检索 + 精排</option>
                <option value="dense">语义检索</option>
                <option value="bm25">关键词检索</option>
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
                  <span>受控关系扩展</span>
                  <button
                    aria-pressed={graphEnabled}
                    className={graphEnabled ? "switch on" : "switch"}
                    onClick={() => setGraphEnabled((value) => !value)}
                    type="button"
                  >
                    <i />
                  </button>
                </label>
                <label title="2 跳用于版本继承/冲突来源追溯，速度稍慢；默认 1 跳">
                  <span>关系跳数</span>
                  <select
                    aria-label="关系跳数"
                    value={graphHops}
                    disabled={!graphEnabled}
                    onChange={(event) => setGraphHops(Number(event.target.value))}
                  >
                    <option value={1}>1 跳（默认）</option>
                    <option value={2}>2 跳（版本/冲突追溯）</option>
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
                <h2>问一个会影响审批决定的问题。</h2>
                <p>系统会区分现行版本、历史规则、例外条件与证据不足，而不是只返回相似文本。</p>
                <div className="quick-question-list">
                  {QUICK_QUESTIONS.map((item) => (
                    <button key={item} onClick={() => void submit(undefined, item)} type="button">
                      <span>{item}</span>
                      <ArrowUp size={16} />
                    </button>
                  ))}
                </div>
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
                        /* U6：任意历史轮次都可以把证据链轨道定位到自己 */
                        <button
                          className={activeTurnId === turn.id ? "answer-evidence-toggle active" : "answer-evidence-toggle"}
                          onClick={() => setActiveTurnId((current) => (current === turn.id ? null : turn.id))}
                          type="button"
                          aria-pressed={activeTurnId === turn.id}
                        >
                          {activeTurnId === turn.id ? "证据链已定位本轮" : "定位本轮证据链"}
                        </button>
                      )}
                    </div>
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
                    {turn.state === "complete" ? (
                      <div className="answer-meta-line">
                        {turn.elapsedMs != null ? <span>耗时 {(turn.elapsedMs / 1000).toFixed(1)}s</span> : null}
                        {turn.usage && turn.usage.input_tokens != null ? <span>输入 {turn.usage.input_tokens} tokens</span> : null}
                        {turn.usage && turn.usage.output_tokens != null ? <span>输出 {turn.usage.output_tokens} tokens</span> : null}
                        {turn.degraded ? (
                          <span className="degraded-badge">
                            <AlertTriangle size={12} /> 生成已降级：{turn.degraded}
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
              {running ? (
                /* U9：中止血用停止图标（Square），RotateCcw 保留给"重试" */
                <button className="button secondary" onClick={() => controller.current?.abort()} type="button">
                  <Square size={13} /> 中止
                </button>
              ) : (
                <button className="button primary" disabled={!question.trim()} type="submit">
                  提交判断 <ArrowUp size={16} />
                </button>
              )}
            </div>
          </form>
        </section>

        <aside className="evidence-rail" ref={railRef}>
          <div className="rail-heading">
            <p className="eyebrow">证据链</p>
            <h2>证据链轨道</h2>
            {selectedTurn ? <p className="rail-pinned">已定位到所选轮次 · 再次点击该轮「定位本轮证据链」可返回最新</p> : null}
          </div>

          <div className="trace-steps">
            <TraceStep label="范围与拒答检查" state={railSteps.scope} />
            <TraceStep label="混合检索与筛选" state={railSteps.retrieval} />
            <TraceStep label="依据约束下生成" state={railSteps.generation} last />
          </div>

          {/* I4：降级时给出显式横幅，说明降级原因，而不是静默改变行为 */}
          {railDegraded ? (
            <div className="rail-degraded" role="status">
              <AlertTriangle size={14} />
              <span>本次生成已降级：{railDegraded}</span>
            </div>
          ) : null}

          <section className="rail-section route-section">
            <div className="rail-section-title">
              <Gauge size={16} />
              <strong>检索路由</strong>
              <span>{railRoute ? (railRoute.mode === "adaptive" ? "自动" : "手动") : "—"}</span>
            </div>
            {routeView ? (
              /* UI 审计 #4：路由决策细节默认折叠，仅保留一行摘要 */
              <details className="technical-details route-decision-fold">
                <summary>{routeView.routeLabel} · {routeView.strategyLabel}{routeView.degraded ? " · 已降级" : ""}</summary>
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
                      <span>已降级</span>
                    </div>
                  ) : null}
                </div>
              </details>
            ) : (
              <p className="rail-placeholder">提交问题后，系统会说明为何选择当前检索成本与证据路径。</p>
            )}
          </section>

          <section className="rail-section">
            <div className="rail-section-title">
              <BookOpenCheck size={16} />
              <strong>引用原文</strong>
              <span>{railCitations.length}</span>
            </div>
            {railCitations.length ? (
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
                        <small>{citation.section_path || "Document body"}</small>
                        {citation.policy_key || citation.document_version || citation.effective_from ? (
                          <span className="citation-policy-meta">
                            {citation.policy_key ? `${citation.policy_key} · ` : ""}
                            {citation.document_version ? `V${citation.document_version}` : "Version unregistered"}
                            {citation.effective_from ? ` · Effective from ${citation.effective_from}` : ""}
                          </span>
                        ) : null}
                        <p>{citation.excerpt}</p>
                      </div>
                    </li>
                  );
                })}
              </ol>
            ) : railResultState === "out_of_scope" ? (
              <p className="rail-placeholder">问题已被范围检查拦截，因此没有引用原文。</p>
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
              <p className="rail-placeholder">回答完成后，这里会显示实际引用，而不是预设示例。</p>
            )}
          </section>

          {conflictItems.length ? (
            <section className="rail-section conflict-section" aria-label="制度版本冲突">
              <div className="rail-section-title conflict-title">
                <AlertTriangle size={16} />
                <strong>有效版本冲突</strong>
                <span>{conflictItems.length}</span>
              </div>
              <p className="conflict-guidance">系统已停止生成。请制度责任人确认查询日期应适用的唯一版本。</p>
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
              <strong>确认关系</strong>
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
              <p className="rail-placeholder">只有 confirmed 关系会出现在这里并参与一跳扩展。</p>
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
                {railUsage.usage_source === "unavailable" ? "（Provider 未上报用量）" : ""}
              </p>
            </section>
          ) : null}
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
            本回答引用了 {stale.length} 条非现行有效的制度版本，采纳前请核实现行版本。
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
