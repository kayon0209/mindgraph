import { FormEvent, useEffect, useState } from "react";
import { BookMarked, ChevronRight, FileText, Search, X } from "lucide-react";

import { EmptyState, ErrorState, LoadingState, MetricCard, PageHeader, StatusPill } from "../components/Primitives";
import { PolicyGovernance } from "../components/PolicyGovernance";
import { api } from "../lib/api";
import type { EvaluationResponse, NoteDetail, NoteItem } from "../types";

export function KnowledgePage() {
  const [notes, setNotes] = useState<NoteItem[]>([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState<EvaluationResponse["library_stats"] | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [statsUnavailable, setStatsUnavailable] = useState(false);
  const [selected, setSelected] = useState<NoteDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const load = async (search = "") => {
    setLoading(true);
    setError("");
    setStatsUnavailable(false);
    try {
      const [noteResult, evaluationResult] = await Promise.allSettled([api.notes(search), api.evaluations()]);

      if (noteResult.status === "fulfilled") {
        setNotes(noteResult.value.items);
        setTotal(noteResult.value.total);
      } else {
        setError(noteResult.reason instanceof Error ? noteResult.reason.message : "制度清单读取失败");
      }

      if (evaluationResult.status === "fulfilled") {
        setStats(evaluationResult.value.library_stats);
      } else {
        setStats(null);
        if (noteResult.status === "fulfilled") {
          setStatsUnavailable(true);
        }
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const search = (event: FormEvent) => {
    event.preventDefault();
    void load(query.trim());
  };

  const openDetail = async (id: string) => {
    setDetailLoading(true);
    try {
      setSelected(await api.note(id));
    } catch (detailError) {
      setError((detailError as Error).message);
    } finally {
      setDetailLoading(false);
    }
  };

  return (
    <div className="page knowledge-page">
      <PageHeader
        eyebrow="Policy ledger / 02"
        title="制度不是文件堆，是有状态的台账"
        description="查看真实同步状态、分块数量与已确认关系。索引数字直接来自当前激活 manifest。"
        actions={
          <form className="search-box" onSubmit={search}>
            <Search size={17} />
            <input onChange={(event) => setQuery(event.target.value)} placeholder="按标题或路径搜索" value={query} />
            <button type="submit">检索</button>
          </form>
        }
      />

      <div className="metrics-grid reveal reveal-2">
        <MetricCard label="已同步制度" note="notes 表真实数量" value={total} />
        <MetricCard label="已进入当前索引" note={statsUnavailable ? "评测看板暂不可用，展示台账主数据" : "读取 CURRENT manifest"} value={stats?.indexed_notes ?? "—"} />
        <MetricCard label="有效分块" note={statsUnavailable ? "评测看板暂不可用，展示台账主数据" : "当前索引版本"} value={stats?.chunks_total ?? "—"} />
        <MetricCard label="已确认关系" note="可参与一跳扩展" value={stats?.relations_confirmed ?? "—"} />
      </div>

      <section className="ledger-section reveal reveal-3">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Current register</p>
            <h2>制度登记册</h2>
          </div>
          <span>{notes.length} / {total}</span>
        </div>

        {loading ? <LoadingState /> : null}
        {!loading && error ? <ErrorState message={error} onRetry={() => void load(query)} /> : null}
        {!loading && !error && notes.length === 0 ? (
          <EmptyState title="没有匹配制度" detail="当前筛选没有返回真实文档，请调整关键词。" />
        ) : null}

        {!loading && !error && notes.length ? (
          <div className="note-ledger">
            {notes.map((note, index) => (
              <button className="note-row" key={note.id} onClick={() => void openDetail(note.id)} type="button">
                <span className="row-number">{String(index + 1).padStart(2, "0")}</span>
                <span className="note-icon"><FileText size={18} /></span>
                <span className="note-primary">
                  <strong>{note.title}</strong>
                  <small>{note.vault_path}</small>
                </span>
                <span className="note-category">{note.category}</span>
                <PolicyGovernance compact value={note.governance} />
                <StatusPill value={note.status} />
                <ChevronRight size={17} />
              </button>
            ))}
          </div>
        ) : null}
      </section>

      {detailLoading ? <div className="detail-loading"><LoadingState label="读取制度关系" /></div> : null}
      {selected ? (
        <div className="drawer-backdrop" onMouseDown={() => setSelected(null)} role="presentation">
          <aside className="detail-drawer" onMouseDown={(event) => event.stopPropagation()}>
            <button className="drawer-close" onClick={() => setSelected(null)} type="button" aria-label="关闭详情">
              <X size={19} />
            </button>
            <p className="eyebrow">Policy record</p>
            <h2>{selected.title}</h2>
            <p className="drawer-path">{selected.vault_path}</p>
            <div className="drawer-metadata">
              <span><small>制度族标识</small><strong>{selected.governance.policy_key || "未设置"}</strong></span>
              <span><small>责任部门</small><strong>{selected.governance.owner || "未设置"}</strong></span>
              <span><small>制度版本</small><strong>{selected.governance.version ? `V${selected.governance.version}` : "未设置"}</strong></span>
              <span><small>制度状态</small><PolicyGovernance compact value={selected.governance} /></span>
              <span><small>生效区间</small><strong>{selected.governance.effective_from || "未设置"}<br />— {selected.governance.effective_to || "长期有效"}</strong></span>
              <span><small>索引状态</small><StatusPill value={selected.status} /></span>
              <span><small>访问级别 / 分块</small><strong>{selected.access_level} · {selected.chunk_count}</strong></span>
            </div>

            <PolicyGovernance value={selected.governance} />

            <section className="drawer-section">
              <div className="drawer-section-title"><BookMarked size={17} /> 已确认关系</div>
              {selected.outgoing_relations.length + selected.incoming_relations.length === 0 ? (
                <p className="rail-placeholder">当前文档没有 confirmed 关系。</p>
              ) : (
                <div className="relation-mini-list">
                  {selected.outgoing_relations.map((relation) => (
                    <div key={`${relation.target_id}-${relation.relation_type}`}>
                      <span>{selected.title}</span><i>{relation.relation_type}</i><strong>{relation.target_title}</strong>
                    </div>
                  ))}
                  {selected.incoming_relations.map((relation) => (
                    <div key={`${relation.source_id}-${relation.relation_type}`}>
                      <strong>{relation.source_title}</strong><i>{relation.relation_type}</i><span>{selected.title}</span>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <details className="technical-details">
              <summary>技术标识</summary>
              <code>{selected.id}</code>
            </details>
          </aside>
        </div>
      ) : null}
    </div>
  );
}
