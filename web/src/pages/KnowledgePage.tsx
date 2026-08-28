import { FormEvent, useEffect, useRef, useState } from "react";
import { BookMarked, ChevronLeft, ChevronRight, FileText, Search, Sparkles, UploadCloud, X } from "lucide-react";

import { ContextHint, EmptyState, ErrorState, LoadingState, MetricCard, PageHeader, StatusPill } from "../components/Primitives";
import { PolicyGovernance } from "../components/PolicyGovernance";
import { api } from "../lib/api";
import { relationTypeColor, relationTypeLabel } from "../lib/graph-meta";
import type { EvaluationResponse, NoteDetail, NoteItem } from "../types";

/** U2：台账分页——后端 notes 接口支持 offset/limit，前端不再一次性拉全量 */
const PAGE_SIZE = 50;

/** 上传流程状态机：idle → uploading → rebuilding → done →（extracting → extracted）| error */
type UploadPhase = "idle" | "uploading" | "rebuilding" | "done" | "extracting" | "extracted" | "error";
type UploadState = { phase: UploadPhase; message: string };

export function KnowledgePage() {
  const [notes, setNotes] = useState<NoteItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [stats, setStats] = useState<EvaluationResponse["library_stats"] | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [statsUnavailable, setStatsUnavailable] = useState(false);
  const [selected, setSelected] = useState<NoteDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");

  useEffect(() => {
    if (!selected) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSelected(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selected]);

  const load = async (search = "", pageOffset = 0) => {
    setLoading(true);
    setError("");
    setStatsUnavailable(false);
    try {
      const [noteResult, evaluationResult] = await Promise.allSettled([
        api.notes(search, pageOffset, PAGE_SIZE),
        api.evaluations(),
      ]);

      if (noteResult.status === "fulfilled") {
        setNotes(noteResult.value.items);
        setTotal(noteResult.value.total);
        setOffset(pageOffset);
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
    void load(query.trim(), 0);
  };

  const clearSearch = () => {
    setQuery("");
    void load("", 0);
  };

  const goPage = (delta: number) => {
    const next = Math.min(Math.max(offset + delta * PAGE_SIZE, 0), Math.max(total - 1, 0));
    void load(query.trim(), next);
  };

  const openDetail = async (id: string) => {
    setDetailLoading(true);
    setDetailError("");
    try {
      setSelected(await api.note(id));
    } catch (openError) {
      // 详情失败不影响台账主列表（分离的错误态，而非整页 ErrorState）
      setDetailError((openError as Error).message);
    } finally {
      setDetailLoading(false);
    }
  };

  // ── 材料上传与融合（阶段A需求3）──
  const [upload, setUpload] = useState<UploadState>({ phase: "idle", message: "" });
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const handleUploadFile = async (file: File) => {
    if (!file.name.toLowerCase().endsWith(".md")) {
      setUpload({ phase: "error", message: "仅支持 .md Markdown 文件（后端限制）。" });
      return;
    }
    setUpload({ phase: "uploading", message: `正在上传「${file.name}」…` });
    try {
      const record = await api.uploadDocument(file, "upload");
      setUpload({ phase: "rebuilding", message: "上传成功，正在增量重建索引…" });
      try {
        await api.incrementalRebuild();
      } catch (rebuildError) {
        setUpload({ phase: "error", message: `上传成功但索引重建失败：${(rebuildError as Error).message}` });
        return;
      }
      await load(query.trim(), offset);
      const title = (record.title as string | undefined) || file.name;
      setUpload({
        phase: "done",
        message: `「${title}」已入库并可检索。下一步可把它融入图谱：自动发现候选关系（proposed），经人工审核确认后才进入图谱与检索。`,
      });
    } catch (uploadError) {
      setUpload({ phase: "error", message: `上传失败：${(uploadError as Error).message}` });
    }
  };

  const fuseIntoGraph = async () => {
    setUpload({ phase: "extracting", message: "正在离线抽取关系（只产 proposed 候选，不调用生成模型）…" });
    try {
      const result = await api.extractRelations();
      const created = Number(result.inserted ?? result.created ?? 0);
      setUpload({
        phase: "extracted",
        message: created > 0
          ? `已生成 ${created} 条候选关系（proposed）。请到「关系审核」逐条确认——确认后才成为 confirmed 关系并进入图谱。`
          : "未发现新的候选关系（与已有候选/已确认关系去重）。可稍后积累更多材料再试。",
      });
    } catch (extractError) {
      setUpload({ phase: "error", message: `关系抽取失败：${(extractError as Error).message}` });
    }
  };

  const onDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragOver(false);
    const file = event.dataTransfer.files?.[0];
    if (file) void handleUploadFile(file);
  };

  return (
    <div className="page knowledge-page">
      <PageHeader
        eyebrow="制度台账与版本"
        title="制度不是文件堆，是有状态的台账"
        description="查看真实同步状态、分块数量与已确认关系。索引数字直接来自当前激活 manifest。"
        actions={
          <form className="search-box" onSubmit={search}>
            <Search size={17} />
            <input onChange={(event) => setQuery(event.target.value)} placeholder="按标题或路径搜索" value={query} />
            {/* U11：搜索词可一键清空并复位列表 */}
            {query ? (
              <button className="search-clear" onClick={clearSearch} type="button" aria-label="清空搜索并复位列表">
                <X size={15} />
              </button>
            ) : null}
            <button type="submit">检索</button>
          </form>
        }
      />

      <ContextHint storageKey="mindgraph.hint.knowledge">
        台账数字实时来自数据库与当前激活索引 manifest，不是静态占位。点击任意一行可查看制度档案与已确认关系；「当前索引」一行可核对索引版本与构建时间。
      </ContextHint>

      {/* 材料上传与融合（阶段A需求3）：.md 上传 → 自动增量重建 → 引导融入图谱（HITL） */}
      <section className="upload-card reveal reveal-2" aria-label="上传新材料">
        <div
          className={dragOver ? "upload-dropzone drag-over" : "upload-dropzone"}
          onClick={() => fileInputRef.current?.click()}
          onDragOver={(event) => {
            event.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          role="button"
          tabIndex={0}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") fileInputRef.current?.click();
          }}
        >
          <UploadCloud size={22} />
          <div>
            <strong>上传 .md 材料</strong>
            <span>点击选择或拖拽 Markdown 文件到此处；上传后自动增量重建索引（后端仅接收 .md）</span>
          </div>
          <input
            ref={fileInputRef}
            accept=".md,text/markdown"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void handleUploadFile(file);
              event.target.value = "";
            }}
            style={{ display: "none" }}
            type="file"
          />
        </div>
        {upload.phase !== "idle" ? (
          <div className={upload.phase === "error" ? "upload-status error" : "upload-status"} role="status">
            <p>{upload.message}</p>
            {upload.phase === "uploading" || upload.phase === "rebuilding" || upload.phase === "extracting" ? (
              <span className="upload-spinner" aria-hidden="true" />
            ) : null}
            {upload.phase === "done" ? (
              <button className="button secondary small" onClick={() => void fuseIntoGraph()} type="button">
                <Sparkles size={14} /> 融入图谱（发现候选关系）
              </button>
            ) : null}
            {upload.phase === "extracted" ? (
              <button className="button secondary small" onClick={() => { window.location.hash = "#/relations"; }} type="button">
                去关系审核确认
              </button>
            ) : null}
            {upload.phase === "error" || upload.phase === "extracted" || upload.phase === "done" ? (
              <button className="upload-status-dismiss" onClick={() => setUpload({ phase: "idle", message: "" })} type="button" aria-label="关闭上传状态">
                <X size={14} />
              </button>
            ) : null}
          </div>
        ) : null}
      </section>

      <div className="metrics-grid reveal reveal-2">
        <MetricCard label="已同步制度" note="notes 表真实数量" value={total} />
        <MetricCard label="已进入当前索引" note={statsUnavailable ? "评测看板暂不可用，展示台账主数据" : "读取 CURRENT manifest"} value={stats?.indexed_notes ?? "—"} />
        <MetricCard label="有效分块" note={statsUnavailable ? "评测看板暂不可用，展示台账主数据" : "当前索引版本"} value={stats?.chunks_total ?? "—"} />
        <MetricCard label="已确认关系" note="可参与一跳扩展" value={stats?.relations_confirmed ?? "—"} />
      </div>

      {/* P1：索引新鲜度——用户能直接判断"答案依据的是哪一版索引、什么时候建的" */}
      {stats?.index_version ? (
        <p className="index-freshness reveal reveal-2">
          当前索引 <code>{stats.index_version}</code>
          {stats.index_built_at ? <span> · 构建于 {new Date(stats.index_built_at).toLocaleString("zh-CN")}</span> : null}
        </p>
      ) : null}

      <section className="ledger-section reveal reveal-3">
        <div className="section-heading">
          <div>
            <p className="eyebrow">当前登记</p>
            <h2>制度登记册</h2>
          </div>
          <span>
            {total ? `${offset + 1}–${Math.min(offset + notes.length, total)} / ${total}` : "0"}
          </span>
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
                <span className="row-number">{String(offset + index + 1).padStart(2, "0")}</span>
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

        {/* U2：分页控件——仅在有数据且超过一页时出现 */}
        {!loading && !error && total > PAGE_SIZE ? (
          <div className="ledger-pagination">
            <button className="button ghost small" disabled={offset <= 0} onClick={() => goPage(-1)} type="button">
              <ChevronLeft size={15} /> 上一页
            </button>
            <span>第 {Math.floor(offset / PAGE_SIZE) + 1} / {Math.ceil(total / PAGE_SIZE)} 页</span>
            <button
              className="button ghost small"
              disabled={offset + notes.length >= total}
              onClick={() => goPage(1)}
              type="button"
            >
              下一页 <ChevronRight size={15} />
            </button>
          </div>
        ) : null}
      </section>

      {detailError ? (
        <div className="detail-error" role="alert">
          制度详情读取失败：{detailError}
          <button className="button secondary" onClick={() => setDetailError("")} type="button">知道了</button>
        </div>
      ) : null}
      {detailLoading ? <div className="detail-loading"><LoadingState label="读取制度关系" /></div> : null}
      {selected ? (
        <div className="drawer-backdrop" onMouseDown={() => setSelected(null)} role="presentation">
          <aside className="detail-drawer" onMouseDown={(event) => event.stopPropagation()}>
            <button className="drawer-close" onClick={() => setSelected(null)} type="button" aria-label="关闭详情">
              <X size={19} />
            </button>
            <p className="eyebrow">制度档案</p>
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
                    <button
                      className="relation-mini-item"
                      key={`${relation.target_id}-${relation.relation_type}`}
                      onClick={() => void openDetail(relation.target_id)}
                      title="查看对端制度档案"
                      type="button"
                    >
                      <span>{selected.title}</span>
                      <i style={{ color: relationTypeColor(relation.relation_type) }}>{relationTypeLabel(relation.relation_type)}</i>
                      <strong>{relation.target_title}</strong>
                    </button>
                  ))}
                  {selected.incoming_relations.map((relation) => (
                    <button
                      className="relation-mini-item"
                      key={`${relation.source_id}-${relation.relation_type}`}
                      onClick={() => void openDetail(relation.source_id)}
                      title="查看对端制度档案"
                      type="button"
                    >
                      <strong>{relation.source_title}</strong>
                      <i style={{ color: relationTypeColor(relation.relation_type) }}>{relationTypeLabel(relation.relation_type)}</i>
                      <span>{selected.title}</span>
                    </button>
                  ))}
                </div>
              )}
            </section>
          </aside>
        </div>
      ) : null}
    </div>
  );
}
