import { FormEvent, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowUp,
  BookOpenCheck,
  Check,
  Circle,
  Gauge,
  GitBranch,
  LoaderCircle,
  RotateCcw,
  Sparkles,
} from "lucide-react";

import { streamChat } from "../lib/api";
import { completionGenerationState, completionViewState, policyConflictItems } from "../lib/policy-conflicts";
import { routeDecisionView } from "../lib/route-decision";
import type { AnswerResult, Citation, ChatRequest, RetrievalTrace, RouteDecision, StreamEvent } from "../types";
import { PageHeader } from "../components/Primitives";

type Turn = { id: string; question: string; answer: string; state: "streaming" | "complete" | "error"; errorDetail?: string };

const ERROR_MESSAGES: Record<string, string> = {
  retrieval_unavailable: "检索服务暂不可用，请稍后重试。",
  provider_error: "生成模型暂时不可用，可先查看引用原文。",
  stream_error: "回答连接中断，请重试。",
};
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

export function ChatPage() {
  const [question, setQuestion] = useState("");
  const [strategy, setStrategy] = useState<ChatRequest["retrieval_strategy"]>("auto");
  const [topK, setTopK] = useState(5);
  const [graphEnabled, setGraphEnabled] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [trace, setTrace] = useState<RetrievalTrace | null>(null);
  const [routeDecision, setRouteDecision] = useState<RouteDecision | null>(null);
  const [resultState, setResultState] = useState<string | null>(null);
  const [steps, setSteps] = useState(INITIAL_STEPS);
  const [running, setRunning] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    const saved = window.localStorage.getItem("mindgraph.chat.turns");
    if (saved) {
      try {
        setTurns(JSON.parse(saved) as Turn[]);
      } catch {
        window.localStorage.removeItem("mindgraph.chat.turns");
      }
    }
  }, []);

  useEffect(() => {
    if (turns.length) window.localStorage.setItem("mindgraph.chat.turns", JSON.stringify(turns));
  }, [turns]);

  useEffect(() => {
    if (!turns.length) composerRef.current?.focus();
  }, [turns.length]);

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
    if (event.event === "degraded" || event.event === "policy_conflict_detected") {
      setSteps((current) => ({ ...current, generation: "warning" }));
    }
    if (event.event === "completed") {
      const result = data as unknown as AnswerResult;
      updateTurn(turnId, { answer: result.answer, state: "complete" });
      setCitations(result.citations || []);
      setTrace(result.retrieval_trace || null);
      setRouteDecision(result.retrieval_trace?.route_decision || null);
      setResultState(completionViewState(result));
      setSteps((current) => ({
        scope: current.scope === "running" ? "done" : current.scope,
        retrieval: result.retrieval_trace ? "done" : current.retrieval,
        generation: completionGenerationState(result),
      }));
    }
    if (event.event === "error") {
      const code = typeof data.code === "string" ? data.code : "stream_error";
      updateTurn(turnId, {
        answer: ERROR_MESSAGES[code] || "请求暂时失败，请稍后重试。",
        errorDetail: typeof data.detail === "string" ? data.detail : undefined,
        state: "error",
      });
      setResultState(code);
      setSteps((current) => ({ ...current, retrieval: "warning" }));
    }
  };

  const conflictItems = policyConflictItems(trace);
  const routeView = routeDecision ? routeDecisionView(routeDecision) : null;

  const submit = async (event?: FormEvent, preset?: string, retryId?: string) => {
    event?.preventDefault();
    const finalQuestion = (preset ?? question).trim();
    if (!finalQuestion || running) return;

    const id = retryId || crypto.randomUUID();
    setTurns((current) => retryId
      ? current.map((turn) => turn.id === retryId ? { ...turn, answer: "", errorDetail: undefined, state: "streaming" } : turn)
      : [...current, { id, question: finalQuestion, answer: "", state: "streaming" }]);
    setQuestion("");
    setCitations([]);
    setTrace(null);
    setRouteDecision(null);
    setResultState(null);
    setSteps(INITIAL_STEPS);
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
        },
        (streamEvent) => handleEvent(id, streamEvent),
        controller.current.signal,
      );
    } catch (error) {
      if ((error as Error).name !== "AbortError") {
        updateTurn(id, { answer: "回答连接中断，请重试。", errorDetail: (error as Error).message, state: "error" });
        setResultState("stream_error");
      }
    } finally {
      setRunning(false);
      controller.current = null;
    }
  };

  return (
    <div className="page chat-page">
      <PageHeader
        eyebrow="可信问答 / 01"
        title="先给结论，再交付证据"
        description="面向制度判断，不追求聊天感。每个回答都必须留下来源、版本与检索轨迹。"
      />

      <div className="chat-layout reveal reveal-2">
        <section className="conversation-panel">
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
            <label className="switch-control">
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
          </div>

          <div className="conversation-stream">
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
                <article className="conversation-turn" key={turn.id}>
                  <div className="user-question">
                    <span>问</span>
                    <p>{turn.question}</p>
                  </div>
                  <div className={`assistant-answer ${turn.state}`}>
                    <div className="answer-heading">
                      <Sparkles size={17} />
                      <span>MindGraph 结论</span>
                      {turn.state === "streaming" ? <LoaderCircle className="spin" size={16} /> : null}
                    </div>
                    <p>{turn.answer || "正在核对制度与证据……"}</p>
                    {turn.state === "error" ? (
                      <div className="answer-actions">
                        <button className="button secondary" onClick={() => void submit(undefined, turn.question, turn.id)} type="button">
                          <RotateCcw size={15} /> 重试
                        </button>
                        {turn.errorDetail ? <details className="technical-details"><summary>查看技术详情</summary><code>{turn.errorDetail}</code></details> : null}
                      </div>
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
                <button className="button secondary" onClick={() => controller.current?.abort()} type="button">
                  <RotateCcw size={15} /> 中止
                </button>
              ) : (
                <button className="button primary" disabled={!question.trim()} type="submit">
                  提交判断 <ArrowUp size={16} />
                </button>
              )}
            </div>
          </form>
        </section>

        <aside className="evidence-rail">
          <div className="rail-heading">
            <p className="eyebrow">证据链</p>
            <h2>证据链轨道</h2>
          </div>

          <div className="trace-steps">
            <TraceStep label="范围与拒答检查" state={steps.scope} />
            <TraceStep label="混合检索与筛选" state={steps.retrieval} />
            <TraceStep label="依据约束下生成" state={steps.generation} last />
          </div>

          <section className="rail-section route-section">
            <div className="rail-section-title">
              <Gauge size={16} />
              <strong>检索路由</strong>
              <span>{routeDecision ? (routeDecision.mode === "adaptive" ? "自动" : "手动") : "—"}</span>
            </div>
            {routeView ? (
              <div className="route-decision-card">
                <div className="route-decision-heading">
                  <strong>{routeView.routeLabel}</strong>
                  <span>{routeView.strategyLabel}</span>
                </div>
                <p>{routeView.graphLabel}</p>
                <ul>{routeView.reasonLabels.map((reason) => <li key={reason}>{reason}</li>)}</ul>
                <small>检索路径：{routeView.strategyLabel}</small>
                <div className="route-decision-tags">
                  <span>{routeView.costTierLabel}</span>
                  <span>{routeView.latencyTierLabel}</span>
                  {routeView.degraded ? <span>已降级</span> : null}
                </div>

              </div>
            ) : (
              <p className="rail-placeholder">提交问题后，系统会说明为何选择当前检索成本与证据路径。</p>
            )}
          </section>

          <section className="rail-section">
            <div className="rail-section-title">
              <BookOpenCheck size={16} />
              <strong>引用原文</strong>
              <span>{citations.length}</span>
            </div>
            {citations.length ? (
              <ol className="citation-list">
                {citations.map((citation) => (
                  <li key={citation.citation_id}>
                    <span className="citation-rank">{citation.final_rank}</span>
                    <div>
                      <strong>{citation.document_name}</strong>
                      <small>{citation.section_path || "文档正文"}</small>
                      {citation.policy_key || citation.document_version || citation.policy_status || citation.effective_from ? (
                        <span className="citation-policy-meta">
                          {citation.policy_key ? `${citation.policy_key} · ` : ""}
                          {citation.document_version ? `V${citation.document_version}` : "版本未登记"}
                          {citation.policy_status ? ` · ${citation.policy_status}` : ""}
                          {citation.effective_from ? ` · ${citation.effective_from} 起` : ""}
                        </span>
                      ) : null}
                      <p>{citation.excerpt}</p>
                    </div>
                  </li>
                ))}
              </ol>
            ) : resultState === "out_of_scope" ? (
              <p className="rail-placeholder">问题已被范围检查拦截，因此没有引用原文。</p>
            ) : resultState === "insufficient_evidence" ? (
              <p className="rail-placeholder">检索到了问题，但未找到足够的制度证据，因此没有可展示引用。</p>
            ) : resultState === "conflicting_evidence" ? (
              <p className="rail-placeholder">系统已因版本冲突停止生成，因此不会展示引用结果。</p>
            ) : resultState === "system_error" ? (
              <p className="rail-placeholder">本次请求发生系统错误，没有生成可展示的引用。</p>
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
          ) : resultState === "conflicting_evidence" ? (
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
              <span>{trace?.graph_links.length || 0}</span>
            </div>
            {trace?.graph_links.length ? (
              <div className="graph-link-list">
                {trace.graph_links.map((link, index) => (
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
