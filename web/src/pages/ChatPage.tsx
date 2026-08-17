import { FormEvent, useRef, useState } from "react";
import {
  ArrowUp,
  BookOpenCheck,
  Check,
  Circle,
  GitBranch,
  LoaderCircle,
  RotateCcw,
  Sparkles,
} from "lucide-react";

import { streamChat } from "../lib/api";
import type { AnswerResult, Citation, ChatRequest, RetrievalTrace, StreamEvent } from "../types";
import { PageHeader } from "../components/Primitives";

type Turn = { id: string; question: string; answer: string; state: "streaming" | "complete" | "error" };
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
  const [strategy, setStrategy] = useState<ChatRequest["retrieval_strategy"]>("hybrid");
  const [topK, setTopK] = useState(5);
  const [graphEnabled, setGraphEnabled] = useState(true);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [trace, setTrace] = useState<RetrievalTrace | null>(null);
  const [steps, setSteps] = useState(INITIAL_STEPS);
  const [running, setRunning] = useState(false);
  const controller = useRef<AbortController | null>(null);

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
    if (event.event === "degraded") {
      setSteps((current) => ({ ...current, generation: "warning" }));
    }
    if (event.event === "completed") {
      const result = data as unknown as AnswerResult;
      updateTurn(turnId, { answer: result.answer, state: "complete" });
      setCitations(result.citations || []);
      setTrace(result.retrieval_trace || null);
      setSteps((current) => ({
        scope: current.scope === "running" ? "done" : current.scope,
        retrieval: result.retrieval_trace ? "done" : current.retrieval,
        generation: result.degraded ? "warning" : "done",
      }));
    }
    if (event.event === "error") {
      updateTurn(turnId, {
        answer: typeof data.message === "string" ? data.message : "请求失败，请检查服务日志。",
        state: "error",
      });
    }
  };

  const submit = async (event?: FormEvent, preset?: string) => {
    event?.preventDefault();
    const finalQuestion = (preset ?? question).trim();
    if (!finalQuestion || running) return;

    const id = crypto.randomUUID();
    setTurns((current) => [...current, { id, question: finalQuestion, answer: "", state: "streaming" }]);
    setQuestion("");
    setCitations([]);
    setTrace(null);
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
        updateTurn(id, { answer: `连接失败：${(error as Error).message}`, state: "error" });
      }
    } finally {
      setRunning(false);
      controller.current = null;
    }
  };

  return (
    <div className="page chat-page">
      <PageHeader
        eyebrow="Decision evidence / 01"
        title="先给结论，再交付证据"
        description="面向制度判断，不追求聊天感。每个回答都必须留下来源、版本与检索轨迹。"
      />

      <div className="chat-layout reveal reveal-2">
        <section className="conversation-panel">
          <div className="query-controls">
            <label>
              <span>检索策略</span>
              <select value={strategy} onChange={(event) => setStrategy(event.target.value as ChatRequest["retrieval_strategy"])}>
                <option value="hybrid">Hybrid</option>
                <option value="hybrid_rerank">Hybrid + Rerank</option>
                <option value="dense">Dense</option>
                <option value="bm25">BM25</option>
              </select>
            </label>
            <label>
              <span>证据数量</span>
              <select value={topK} onChange={(event) => setTopK(Number(event.target.value))}>
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
                  </div>
                </article>
              ))
            )}
          </div>

          <form className="question-composer" onSubmit={(event) => void submit(event)}>
            <textarea
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
            <p className="eyebrow">Evidence chain</p>
            <h2>证据链轨道</h2>
          </div>

          <div className="trace-steps">
            <TraceStep label="范围与拒答检查" state={steps.scope} />
            <TraceStep label="混合检索与筛选" state={steps.retrieval} />
            <TraceStep label="依据约束下生成" state={steps.generation} last />
          </div>

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
                      {citation.document_version || citation.policy_status || citation.effective_from ? (
                        <span className="citation-policy-meta">
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
            ) : (
              <p className="rail-placeholder">回答完成后，这里会显示实际引用，而不是预设示例。</p>
            )}
          </section>

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
        {state === "running" ? <LoaderCircle className="spin" size={15} /> : state === "done" ? <Check size={15} /> : <Circle size={11} />}
      </span>
      <span>{label}</span>
      {!last ? <i /> : null}
    </div>
  );
}
