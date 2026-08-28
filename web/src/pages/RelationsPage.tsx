import { useEffect, useState, type KeyboardEvent } from "react";
import { AlertOctagon, Check, GitCompareArrows, RefreshCw, X } from "lucide-react";

import { ContextHint, EmptyState, ErrorState, LoadingState, MetricCard, PageHeader } from "../components/Primitives";
import { api } from "../lib/api";
import type { RelationItem } from "../types";

type Tab = "proposed" | "confirmed";

export function RelationsPage() {
  const [proposed, setProposed] = useState<RelationItem[]>([]);
  const [confirmed, setConfirmed] = useState<RelationItem[]>([]);
  const [tab, setTab] = useState<Tab>("proposed");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [resolving, setResolving] = useState<string | null>(null);
  const [reviewReasons, setReviewReasons] = useState<Record<string, string>>({});
  // U3：校验提示用页内轻量提示，而不是整页 ErrorState（那会吞掉整个审核队列）
  const [hint, setHint] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    setHint("");
    try {
      const [candidateData, confirmedData] = await Promise.all([api.proposedRelations(), api.confirmedRelations()]);
      setProposed(candidateData.proposed);
      setConfirmed(confirmedData.confirmed);
    } catch (loadError) {
      setError((loadError as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const resolve = async (relation: RelationItem, decision: "confirm" | "reject") => {
    const reason = (reviewReasons[relation.id] || "").trim();
    if (!reason) {
      setHint("确认或拒绝关系前必须填写审核原因。");
      return;
    }
    setResolving(relation.id);
    setError("");
    setHint("");
    try {
      await api.resolveRelation(relation.id, decision, reason);
      setReviewReasons((current) => {
        const next = { ...current };
        delete next[relation.id];
        return next;
      });
      await load();
    } catch (resolveError) {
      setError((resolveError as Error).message);
    } finally {
      setResolving(null);
    }
  };

  const visible = tab === "proposed" ? proposed : confirmed;
  const conflicts = proposed.filter((item) => item.conflict).length;

  // P0-3：标签页键盘导航（左右/Home/End + roving tabindex）
  const onTablistKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const order: Tab[] = ["proposed", "confirmed"];
    const currentIndex = order.indexOf(tab);
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % order.length;
    else if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + order.length) % order.length;
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = order.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    const nextTab = order[nextIndex];
    setTab(nextTab);
    document.getElementById(`relations-tab-${nextTab}`)?.focus();
  };

  return (
    <div className="page relations-page">
      <PageHeader
        eyebrow="制度关系审核"
        title="相似只是候选，确认才是关系"
        description="默认 BGE 路径只能发现相似笔记。审核者确认之后，关系才有资格进入一跳扩展。"
        actions={
          <button className="button secondary" onClick={() => void load()} type="button">
            <RefreshCw size={16} className={loading ? "spin" : ""} /> 刷新队列
          </button>
        }
      />

      <ContextHint storageKey="mindgraph.hint.relations">
        候选（proposed）来自 BGE 相似度发现的相似制度对，不参与检索；审核者填写原因并确认后，才成为 confirmed 关系并进入一跳扩展。拒绝同样需要原因，便于追溯。
      </ContextHint>

      <div className="metrics-grid reveal reveal-2">
        <MetricCard label="待人工判断" note="proposed，不参与检索" value={proposed.length} />
        <MetricCard label="已确认关系" note="可参与一跳扩展" value={confirmed.length} />
        <MetricCard label="冲突候选" note="同一文档对已有确认边" value={conflicts} />
        <MetricCard label="默认候选来源" note="当前仅使用 BGE 相似度，非业务关系类型" value="BGE 语义相似" />
      </div>

      <section className="relation-register reveal reveal-3">
        {/* U10：标签页补全 ARIA 语义；P0-3：键盘导航 + roving tabindex */}
        <div className="relation-tabs" role="tablist" aria-label="关系审核状态" onKeyDown={onTablistKeyDown}>
          <button
            aria-controls="relations-panel"
            aria-selected={tab === "proposed"}
            className={tab === "proposed" ? "active" : ""}
            id="relations-tab-proposed"
            onClick={() => setTab("proposed")}
            role="tab"
            tabIndex={tab === "proposed" ? 0 : -1}
            type="button"
          >
            待审核 <span>{proposed.length}</span>
          </button>
          <button
            aria-controls="relations-panel"
            aria-selected={tab === "confirmed"}
            className={tab === "confirmed" ? "active" : ""}
            id="relations-tab-confirmed"
            onClick={() => setTab("confirmed")}
            role="tab"
            tabIndex={tab === "confirmed" ? 0 : -1}
            type="button"
          >
            已确认 <span>{confirmed.length}</span>
          </button>
        </div>

        <div aria-labelledby={`relations-tab-${tab}`} id="relations-panel" role="tabpanel">
          {hint ? <p className="relations-hint" role="alert">{hint}</p> : null}

          {loading ? <LoadingState label="读取关系审核队列" /> : null}
          {!loading && error ? <ErrorState message={error} onRetry={() => void load()} /> : null}
          {!loading && !error && visible.length === 0 ? (
            <EmptyState
              title={tab === "proposed" ? "没有待审核候选" : "还没有已确认关系"}
              detail={tab === "proposed"
                ? "当前没有等待人工判断的关系候选。系统发现相似制度时会先进入这里，确认后才会参与检索。"
                : "候选关系必须经过人工确认才会出现在这里。确认后的关系可参与一跳扩展。"}
            />
          ) : null}

        {!loading && !error && visible.length ? (
          <div className="relation-list">
            {visible.map((relation, index) => (
              <article className={relation.conflict ? "relation-card conflict" : "relation-card"} key={relation.id}>
                <div className="relation-number">{String(index + 1).padStart(2, "0")}</div>
                <div className="relation-flow">
                  <div className="relation-document">
                    <small>来源制度</small>
                    <strong>{relation.source}</strong>
                  </div>
                  <div className="relation-arrow">
                    <GitCompareArrows size={19} />
                    <span>{relation.type || "related_to"}</span>
                  </div>
                  <div className="relation-document">
                    <small>目标制度</small>
                    <strong>{relation.target}</strong>
                  </div>
                </div>
                <div className="relation-meta">
                  <span>置信度 <strong>{typeof relation.confidence === "number" ? `${(relation.confidence * 100).toFixed(0)}%` : "—"}</strong></span>
                  {relation.proposed_at ? <span>提出于 {relation.proposed_at}</span> : null}
                  {relation.conflict ? <span className="conflict-label"><AlertOctagon size={14} /> 已有确认边</span> : null}
                </div>
                {relation.evidence_span || relation.evidence_section ? (
                  /* P3-24：审核者必须能对照证据原文，而不是只有 chunk id */
                  <blockquote className="relation-evidence">
                    <small>证据原文{relation.evidence_section ? ` · ${relation.evidence_section}` : ""}</small>
                    <p>{relation.evidence_span || relation.evidence_chunk_id}</p>
                  </blockquote>
                ) : relation.evidence_chunk_id ? (
                  <div className="relation-meta"><span>证据片段 {relation.evidence_chunk_id}</span></div>
                ) : null}
                {tab === "proposed" ? (
                  <div className="relation-review-controls">
                    <label>
                      <span>审核原因</span>
                      <textarea
                        aria-label={`审核原因：${relation.source} → ${relation.target}`}
                        maxLength={500}
                        onChange={(event) => setReviewReasons((current) => ({ ...current, [relation.id]: event.target.value }))}
                        placeholder="说明证据如何支持或不足以支持这条关系"
                        rows={2}
                        value={reviewReasons[relation.id] || ""}
                      />
                    </label>
                    <div className="relation-actions">
                      <button
                        className="button approve"
                        disabled={resolving === relation.id || !(reviewReasons[relation.id] || "").trim()}
                        onClick={() => void resolve(relation, "confirm")}
                        type="button"
                      >
                        <Check size={15} /> 确认
                      </button>
                      <button
                        className="button reject"
                        disabled={resolving === relation.id || !(reviewReasons[relation.id] || "").trim()}
                        onClick={() => void resolve(relation, "reject")}
                        type="button"
                      >
                        <X size={15} /> 拒绝
                      </button>
                    </div>
                  </div>
                ) : (
                  <span className="confirmed-stamp">已确认</span>
                )}
              </article>
            ))}
          </div>
        ) : null}
        </div>
      </section>

      <div className="semantic-warning reveal reveal-4">
        <AlertOctagon size={19} />
        <p><strong>当前不是完整知识图谱。</strong> 默认候选来自两两余弦相似度；只有后续引入制度条款、条件、例外、替代和冲突的 typed edge，才形成企业制度断言图。</p>
      </div>
    </div>
  );
}
