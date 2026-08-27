import { useEffect, useState } from "react";
import { AlertOctagon, Check, GitCompareArrows, RefreshCw, X } from "lucide-react";

import { EmptyState, ErrorState, LoadingState, MetricCard, PageHeader } from "../components/Primitives";
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

  const load = async () => {
    setLoading(true);
    setError("");
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
      setError("确认或拒绝关系前必须填写审核原因。");
      return;
    }
    setResolving(relation.id);
    setError("");
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

  return (
    <div className="page relations-page">
      <PageHeader
        eyebrow="Relation review / 04"
        title="相似只是候选，确认才是关系"
        description="默认 BGE 路径只能发现相似笔记。审核者确认之后，关系才有资格进入一跳扩展。"
        actions={
          <button className="button secondary" onClick={() => void load()} type="button">
            <RefreshCw size={16} className={loading ? "spin" : ""} /> 刷新队列
          </button>
        }
      />

      <div className="metrics-grid reveal reveal-2">
        <MetricCard label="待人工判断" note="proposed，不参与检索" value={proposed.length} />
        <MetricCard label="已确认关系" note="可参与一跳扩展" value={confirmed.length} />
        <MetricCard label="冲突候选" note="同一文档对已有确认边" value={conflicts} />
        <MetricCard label="默认候选来源" note="不是业务关系类型" value="BGE 相似度" />
      </div>

      <section className="relation-register reveal reveal-3">
        <div className="relation-tabs" role="tablist">
          <button className={tab === "proposed" ? "active" : ""} onClick={() => setTab("proposed")} type="button">
            待审核 <span>{proposed.length}</span>
          </button>
          <button className={tab === "confirmed" ? "active" : ""} onClick={() => setTab("confirmed")} type="button">
            已确认 <span>{confirmed.length}</span>
          </button>
        </div>

        {loading ? <LoadingState label="读取关系审核队列" /> : null}
        {!loading && error ? <ErrorState message={error} onRetry={() => void load()} /> : null}
        {!loading && !error && visible.length === 0 ? (
          <EmptyState
            title={tab === "proposed" ? "没有待审核候选" : "还没有 confirmed 关系"}
            detail={tab === "proposed" ? "可以通过关系抽取 API 生成 proposed 候选。" : "候选必须经过人工确认才会出现在这里。"}
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
                  {relation.evidence_chunk_id ? <span>证据片段 {relation.evidence_chunk_id}</span> : null}
                  {relation.conflict ? <span className="conflict-label"><AlertOctagon size={14} /> 已有确认边</span> : null}
                </div>
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
                  <span className="confirmed-stamp">CONFIRMED</span>
                )}
              </article>
            ))}
          </div>
        ) : null}
      </section>

      <div className="semantic-warning reveal reveal-4">
        <AlertOctagon size={19} />
        <p><strong>当前不是完整知识图谱。</strong> 默认候选来自两两余弦相似度；只有后续引入制度条款、条件、例外、替代和冲突的 typed edge，才形成企业制度断言图。</p>
      </div>
    </div>
  );
}
