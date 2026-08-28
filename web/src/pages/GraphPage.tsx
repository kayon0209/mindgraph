import { useCallback, useEffect, useRef, useState } from "react";
import { BookMarked, Maximize, Network, RefreshCw, Sparkles, X } from "lucide-react";
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, type Simulation } from "d3-force";

import { ContextHint, EmptyState, ErrorState, LoadingState, PageHeader, StatusPill } from "../components/Primitives";
import { api } from "../lib/api";
import { categoryColor, categoryLabel, relationTypeColor, relationTypeLabel } from "../lib/graph-meta";
import type { NoteDetail, NoteItem, RelationItem } from "../types";

/** 图谱页（阶段A需求1）：d3-force 可视化 confirmed 关系；proposed 仅虚线展示，不参与检索。
 *  治理约束：图谱默认关闭（ADR-002）指检索时图路由，本页只做可视化与数据积累。 */

type GraphNodeDatum = {
  id: string;
  title: string;
  category: string;
  degree: number;
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  fx?: number | null;
  fy?: number | null;
};

type GraphEdgeDatum = {
  key: string;
  type: string;
  proposed: boolean;
  confidence: number | null;
  /** forceLink 初始化后由 id 字符串变异为节点对象 */
  source: string | GraphNodeDatum;
  target: string | GraphNodeDatum;
};

type GraphData = {
  notes: NoteItem[];
  confirmed: RelationItem[];
  proposed: RelationItem[];
};

const MIN_ZOOM = 0.25;
const MAX_ZOOM = 4;
const CLICK_DRAG_THRESHOLD_PX = 4;

function nodeRadius(node: GraphNodeDatum): number {
  return 7 + Math.min(node.degree * 2, 10);
}

function truncateLabel(text: string, max = 14): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

/** 深链 #/graph?node=<id>：进入即选中（App 的 viewFromHash 用 ^#\/(\w+) 兼容） */
function nodeFromHash(): string | null {
  const match = window.location.hash.match(/^#\/graph\?(?:[^#]*&)?node=([^&]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

function edgeNode(value: string | GraphNodeDatum): GraphNodeDatum | null {
  return typeof value === "object" && value !== null ? value : null;
}

export function GraphPage() {
  const [data, setData] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showProposed, setShowProposed] = useState(false);

  const [selectedId, setSelectedId] = useState<string | null>(() => nodeFromHash());
  const [detail, setDetail] = useState<NoteDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");

  const [mineBusy, setMineBusy] = useState(false);
  const [mineMessage, setMineMessage] = useState("");

  const canvasRef = useRef<HTMLDivElement | null>(null);
  const nodesRef = useRef<GraphNodeDatum[]>([]);
  const edgesRef = useRef<GraphEdgeDatum[]>([]);
  const simRef = useRef<Simulation<GraphNodeDatum, undefined> | null>(null);
  const [, setTickCount] = useState(0);
  const [transform, setTransform] = useState({ x: 0, y: 0, k: 1 });
  const transformRef = useRef(transform);
  transformRef.current = transform;
  const centeredRef = useRef(false);
  const panRef = useRef<{ pointerId: number; lastX: number; lastY: number } | null>(null);
  const dragRef = useRef<{ pointerId: number; nodeId: string; startX: number; startY: number; moved: boolean } | null>(null);

  // ── 数据装载 ──
  const load = useCallback(async (withSpinner = true) => {
    if (withSpinner) setLoading(true);
    setError("");
    try {
      const [notesRes, confirmedRes, proposedRes] = await Promise.all([
        api.notes("", 0, 500),
        api.confirmedRelations(),
        api.proposedRelations(),
      ]);
      setData({ notes: notesRes.items, confirmed: confirmedRes.confirmed, proposed: proposedRes.proposed });
    } catch (loadError) {
      setError((loadError as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // ── 仿真构建（数据或待审开关变化时重建；节点位置尽量沿用） ──
  useEffect(() => {
    if (!data) return;
    const previous = new Map(nodesRef.current.map((node) => [node.id, node]));
    const degree = new Map<string, number>();
    for (const rel of data.confirmed) {
      degree.set(rel.source_id, (degree.get(rel.source_id) ?? 0) + 1);
      degree.set(rel.target_id, (degree.get(rel.target_id) ?? 0) + 1);
    }
    const nodes: GraphNodeDatum[] = data.notes.map((note) => {
      const prev = previous.get(note.id);
      return {
        id: note.id,
        title: note.title,
        category: note.category,
        degree: degree.get(note.id) ?? 0,
        x: prev?.x,
        y: prev?.y,
        vx: prev?.vx,
        vy: prev?.vy,
      };
    });
    const nodeIds = new Set(nodes.map((node) => node.id));
    const toEdge = (rel: RelationItem, proposed: boolean): GraphEdgeDatum | null =>
      nodeIds.has(rel.source_id) && nodeIds.has(rel.target_id)
        ? {
            key: rel.id,
            type: rel.type,
            proposed,
            confidence: rel.confidence ?? null,
            source: rel.source_id,
            target: rel.target_id,
          }
        : null;
    const edges: GraphEdgeDatum[] = [
      ...data.confirmed.map((rel) => toEdge(rel, false)),
      ...(showProposed ? data.proposed.map((rel) => toEdge(rel, true)) : []),
    ].filter((edge): edge is GraphEdgeDatum => edge !== null);

    nodesRef.current = nodes;
    edgesRef.current = edges;

    const sim = forceSimulation<GraphNodeDatum>(nodes)
      .force("link", forceLink<GraphNodeDatum, GraphEdgeDatum>(edges).id((node) => node.id).distance(110))
      .force("charge", forceManyBody().strength(-220))
      .force("center", forceCenter(0, 0))
      .force("collide", forceCollide<GraphNodeDatum>((node) => nodeRadius(node) + 14));
    sim.on("tick", () => setTickCount((count) => count + 1));
    simRef.current = sim;
    return () => {
      sim.stop();
      simRef.current = null;
    };
  }, [data, showProposed]);

  // ── 画布尺寸与初始居中 ──
  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (rect && rect.width > 0 && rect.height > 0) {
        if (!centeredRef.current) {
          centeredRef.current = true;
          setTransform({ x: rect.width / 2, y: rect.height / 2, k: 1 });
        }
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // ── 滚轮缩放（向光标缩放；wheel 必须 passive:false 才能 preventDefault） ──
  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = el.getBoundingClientRect();
      const px = event.clientX - rect.left;
      const py = event.clientY - rect.top;
      const factor = Math.exp(-event.deltaY * 0.0016);
      setTransform((t) => {
        const k = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, t.k * factor));
        if (k === t.k) return t;
        const wx = (px - t.x) / t.k;
        const wy = (py - t.y) / t.k;
        return { k, x: px - wx * k, y: py - wy * k };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const screenToWorld = useCallback((clientX: number, clientY: number) => {
    const el = canvasRef.current;
    const t = transformRef.current;
    if (!el) return { x: 0, y: 0 };
    const rect = el.getBoundingClientRect();
    return { x: (clientX - rect.left - t.x) / t.k, y: (clientY - rect.top - t.y) / t.k };
  }, []);

  // ── 选中与详情（双向链接/反向引用） ──
  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setDetailError("");
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    setDetailError("");
    api
      .note(selectedId)
      .then((noteDetail) => {
        if (!cancelled) setDetail(noteDetail);
      })
      .catch((detailErr) => {
        if (!cancelled) setDetailError((detailErr as Error).message);
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  // 选中状态同步到深链（replaceState 不触发 hashchange，不会干扰 App 视图）
  useEffect(() => {
    const target = selectedId ? `#/graph?node=${encodeURIComponent(selectedId)}` : "#/graph";
    if (window.location.hash !== target) {
      window.history.replaceState(null, "", target);
    }
  }, [selectedId]);

  /** 选中并居中（详情面板关系跳转用） */
  const focusNode = useCallback((id: string) => {
    setSelectedId(id);
    const node = nodesRef.current.find((item) => item.id === id);
    const el = canvasRef.current;
    if (!node || !el) return;
    const rect = el.getBoundingClientRect();
    setTransform((t) => ({
      k: t.k,
      x: rect.width / 2 - (node.x ?? 0) * t.k,
      y: rect.height / 2 - (node.y ?? 0) * t.k,
    }));
  }, []);

  const fitView = useCallback(() => {
    const nodes = nodesRef.current;
    const el = canvasRef.current;
    if (!nodes.length || !el) return;
    const rect = el.getBoundingClientRect();
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const node of nodes) {
      minX = Math.min(minX, node.x ?? 0);
      maxX = Math.max(maxX, node.x ?? 0);
      minY = Math.min(minY, node.y ?? 0);
      maxY = Math.max(maxY, node.y ?? 0);
    }
    const pad = 70;
    const boundsW = Math.max(maxX - minX + pad * 2, 1);
    const boundsH = Math.max(maxY - minY + pad * 2, 1);
    const k = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Math.min(rect.width / boundsW, rect.height / boundsH)));
    setTransform({ k, x: rect.width / 2 - (k * (minX + maxX)) / 2, y: rect.height / 2 - (k * (minY + maxY)) / 2 });
  }, []);

  const resetLayout = useCallback(() => {
    for (const node of nodesRef.current) {
      node.x = (Math.random() - 0.5) * 320;
      node.y = (Math.random() - 0.5) * 320;
      node.vx = 0;
      node.vy = 0;
      node.fx = null;
      node.fy = null;
    }
    simRef.current?.alpha(1).restart();
  }, []);

  // ── 背景拖拽 = 平移 ──
  const onSvgPointerDown = (event: React.PointerEvent<SVGSVGElement>) => {
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    panRef.current = { pointerId: event.pointerId, lastX: event.clientX, lastY: event.clientY };
  };

  const onSvgPointerMove = (event: React.PointerEvent<SVGSVGElement>) => {
    const pan = panRef.current;
    if (!pan || pan.pointerId !== event.pointerId) return;
    const dx = event.clientX - pan.lastX;
    const dy = event.clientY - pan.lastY;
    pan.lastX = event.clientX;
    pan.lastY = event.clientY;
    setTransform((t) => ({ ...t, x: t.x + dx, y: t.y + dy }));
  };

  const onSvgPointerUp = (event: React.PointerEvent<SVGSVGElement>) => {
    if (panRef.current?.pointerId === event.pointerId) panRef.current = null;
  };

  // ── 节点拖拽（位移 < 4px 判定为点击） ──
  const onNodePointerDown = (event: React.PointerEvent<SVGCircleElement>, node: GraphNodeDatum) => {
    if (event.button !== 0) return;
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = { pointerId: event.pointerId, nodeId: node.id, startX: event.clientX, startY: event.clientY, moved: false };
    node.fx = node.x;
    node.fy = node.y;
    simRef.current?.alphaTarget(0.3).restart();
  };

  const onNodePointerMove = (event: React.PointerEvent<SVGCircleElement>, node: GraphNodeDatum) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId || drag.nodeId !== node.id) return;
    if (!drag.moved && Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) >= CLICK_DRAG_THRESHOLD_PX) {
      drag.moved = true;
    }
    if (drag.moved) {
      const world = screenToWorld(event.clientX, event.clientY);
      node.fx = world.x;
      node.fy = world.y;
    }
  };

  const onNodePointerUp = (event: React.PointerEvent<SVGCircleElement>, node: GraphNodeDatum) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId || drag.nodeId !== node.id) return;
    dragRef.current = null;
    simRef.current?.alphaTarget(0);
    node.fx = null;
    node.fy = null;
    if (!drag.moved) setSelectedId(node.id);
  };

  // ── 手动挖掘（规则式，只产 proposed；HITL 确认后才入图） ──
  const runMine = async () => {
    setMineBusy(true);
    setMineMessage("");
    try {
      const result = await api.mineQuestions();
      const parts = [`已挖掘 ${result.mined ?? 0} 条问题`, `新增 ${result.proposed_created ?? 0} 条待审关系`];
      if (result.gap_terms) parts.push(`发现 ${result.gap_terms} 个覆盖缺口`);
      setMineMessage(`${parts.join("，")}。待审关系需到「关系审核」确认后才会进入图谱。`);
      await load(false);
    } catch (mineError) {
      setMineMessage(`挖掘失败：${(mineError as Error).message}`);
    } finally {
      setMineBusy(false);
    }
  };

  // ── 渲染 ──
  if (loading && !data) return <div className="page graph-page"><LoadingState label="正在装载图谱数据" /></div>;
  if (error) {
    return (
      <div className="page graph-page">
        <ErrorState message="图谱数据读取失败" detail={error} onRetry={() => void load()} />
      </div>
    );
  }
  if (!data || data.notes.length === 0) {
    return (
      <div className="page graph-page">
        <PageHeader
          eyebrow="制度知识图谱"
          title="制度关系图谱"
          description="每个节点是一份制度，连线表示人工确认过的关联。滚轮缩放，拖拽平移，点击节点查看详情。"
        />
        <EmptyState title="还没有图谱数据" detail="请先在「制度台账」上传或同步制度材料，确认关系后这里才会出现节点与边。" />
      </div>
    );
  }

  const nodes = nodesRef.current;
  const edges = edgesRef.current;
  const confirmedTypes = Array.from(new Set(edges.filter((edge) => !edge.proposed).map((edge) => edge.type)));
  const relationCount = data.confirmed.length + (showProposed ? data.proposed.length : 0);

  return (
    <div className="page graph-page">
      <PageHeader
        eyebrow="制度知识图谱"
        title="制度关系图谱"
        description="每个节点是一份制度，连线表示人工确认过的关联。滚轮缩放，拖拽平移，点击节点查看详情。"
      />

      <ContextHint storageKey="mindgraph.hint.graph">
        虚线表示待确认的候选关系，到「关系审核」确认后才会成为正式关系。
      </ContextHint>

      <div className="graph-toolbar reveal reveal-2">
        <div className="graph-counts" aria-live="polite">
          <span><strong>{data.notes.length}</strong> 份制度</span>
          <span><strong>{data.confirmed.length}</strong> 已确认关系</span>
          {showProposed ? <span className="graph-counts-proposed"><strong>{data.proposed.length}</strong> 待审（虚线）</span> : null}
        </div>
        <label className="switch-control graph-switch" title="候选关系仅可视化展示，不参与检索；确认后才入图">
          <span>显示候选关系</span>
          <button
            aria-pressed={showProposed}
            className={showProposed ? "switch on" : "switch"}
            onClick={() => setShowProposed((value) => !value)}
            type="button"
          >
            <i />
          </button>
        </label>
        <div className="graph-toolbar-actions">
          <button className="button ghost small" disabled={mineBusy} onClick={() => void runMine()} type="button" title="从历史提问中发现可能相关的制度">
            <Sparkles size={14} /> {mineBusy ? "分析中…" : "发现新关系"}
          </button>
          <button className="button ghost small" onClick={resetLayout} type="button">
            <RefreshCw size={14} /> 重置布局
          </button>
          <button className="button ghost small" onClick={fitView} type="button">
            <Maximize size={14} /> 居中显示
          </button>
          <button className="button ghost small" onClick={() => void load(false)} type="button">
            <RefreshCw size={14} /> 刷新
          </button>
        </div>
      </div>

      {mineMessage ? <p className="graph-mine-message" role="status">{mineMessage}</p> : null}

      <div className="graph-main reveal reveal-3">
        <div className="graph-canvas" ref={canvasRef}>
          {data.confirmed.length === 0 ? (
            <div className="graph-empty-banner" role="note">
              <p>还没有已确认的关联关系，图谱目前只有单独的制度。可到「关系审核」确认候选关系。</p>
              <button className="button secondary small" onClick={() => { window.location.hash = "#/relations"; }} type="button">
                去关系审核
              </button>
            </div>
          ) : null}
          <svg
            className="graph-svg"
            onPointerDown={onSvgPointerDown}
            onPointerMove={onSvgPointerMove}
            onPointerUp={onSvgPointerUp}
            onPointerCancel={onSvgPointerUp}
            role="img"
            aria-label={`制度关系图谱：${data.notes.length} 个节点，${relationCount} 条关系`}
          >
            <defs>
              {confirmedTypes.map((type) => (
                <marker
                  id={`graph-arrow-${type}`}
                  key={`graph-arrow-${type}`}
                  viewBox="0 0 10 10"
                  refX={9}
                  refY={5}
                  markerWidth={7}
                  markerHeight={7}
                  orient="auto-start-reverse"
                >
                  <path d="M0,0 L10,5 L0,10 z" style={{ fill: relationTypeColor(type) }} />
                </marker>
              ))}
            </defs>
            <g transform={`translate(${transform.x},${transform.y}) scale(${transform.k})`}>
              {edges.map((edge) => {
                const source = edgeNode(edge.source);
                const target = edgeNode(edge.target);
                if (!source || !target) return null;
                const dx = (target.x ?? 0) - (source.x ?? 0);
                const dy = (target.y ?? 0) - (source.y ?? 0);
                const dist = Math.hypot(dx, dy) || 1;
                const ux = dx / dist;
                const uy = dy / dist;
                const sourceR = nodeRadius(source) + 1;
                const targetR = nodeRadius(target) + (edge.proposed ? 2 : 5);
                const x1 = (source.x ?? 0) + ux * sourceR;
                const y1 = (source.y ?? 0) + uy * sourceR;
                const x2 = (target.x ?? 0) - ux * targetR;
                const y2 = (target.y ?? 0) - uy * targetR;
                const color = edge.proposed ? "var(--line-dark)" : relationTypeColor(edge.type);
                return (
                  <line
                    className={edge.proposed ? "graph-edge proposed" : "graph-edge"}
                    key={edge.key}
                    x1={x1}
                    y1={y1}
                    x2={x2}
                    y2={y2}
                    style={{ stroke: color }}
                    markerEnd={edge.proposed ? undefined : `url(#graph-arrow-${edge.type})`}
                  >
                    <title>
                      {`${source.title} → ${target.title} · ${relationTypeLabel(edge.type)}${edge.proposed ? "（待审核）" : ""}${edge.confidence != null ? ` · 置信度 ${edge.confidence}` : ""}`}
                    </title>
                  </line>
                );
              })}
              {nodes.map((node) => {
                const r = nodeRadius(node);
                const isSelected = node.id === selectedId;
                return (
                  <g key={node.id} transform={`translate(${node.x ?? 0},${node.y ?? 0})`}>
                    <circle
                      className={isSelected ? "graph-node selected" : "graph-node"}
                      r={r}
                      style={{ fill: categoryColor(node.category) }}
                      onPointerDown={(event) => onNodePointerDown(event, node)}
                      onPointerMove={(event) => onNodePointerMove(event, node)}
                      onPointerUp={(event) => onNodePointerUp(event, node)}
                      onPointerCancel={(event) => onNodePointerUp(event, node)}
                    >
                      <title>{`${node.title} · ${categoryLabel(node.category)} · 关联数 ${node.degree}`}</title>
                    </circle>
                    <text className="graph-node-label" textAnchor="middle" y={r + 14}>
                      {truncateLabel(node.title)}
                    </text>
                  </g>
                );
              })}
            </g>
          </svg>
          <div className="graph-legend" aria-hidden="true">
            {(["policies", "workflows", "cases", "external"] as const).map((category) => (
              <span className="graph-legend-item" key={category}>
                <i style={{ background: categoryColor(category) }} />
                {categoryLabel(category)}
              </span>
            ))}
            <span className="graph-legend-item">
              <i className="graph-legend-line" />
              候选关系（虚线）
            </span>
          </div>
        </div>

        <aside className="graph-detail-panel">
          {selectedId === null ? (
            <div className="graph-detail-placeholder">
              <Network size={22} />
              <p>点击节点查看制度详情与关联关系</p>
            </div>
          ) : detailLoading ? (
            <LoadingState label="读取制度档案" />
          ) : detailError ? (
            <ErrorState message="制度档案读取失败" detail={detailError} />
          ) : detail ? (
            <>
              <div className="graph-detail-head">
                <p className="eyebrow">制度档案</p>
                <h2>{detail.title}</h2>
                <p className="drawer-path">{detail.vault_path}</p>
                <div className="graph-detail-tags">
                  <span className="graph-category-chip" style={{ borderColor: categoryColor(detail.category), color: categoryColor(detail.category) }}>
                    {categoryLabel(detail.category)}
                  </span>
                  <StatusPill value={detail.status} />
                </div>
              </div>
              <div className="graph-detail-meta">
                <span><small>版本</small><strong>{detail.governance.version ? `V${detail.governance.version}` : "未设置"}</strong></span>
                <span><small>责任人</small><strong>{detail.governance.owner || "未设置"}</strong></span>
                <span><small>制度状态</small><strong>{detail.governance.policy_status && detail.governance.policy_status !== "unspecified" ? detail.governance.policy_status : "未设置"}</strong></span>
                <span><small>生效区间</small><strong>{detail.governance.effective_from || "未设置"} — {detail.governance.effective_to || "长期"}</strong></span>
              </div>
              <section className="graph-detail-links">
                <div className="drawer-section-title">
                  <BookMarked size={16} /> 关联关系（{detail.outgoing_relations.length + detail.incoming_relations.length}）
                </div>
                {detail.outgoing_relations.length + detail.incoming_relations.length === 0 ? (
                  <p className="rail-placeholder">当前制度还没有已确认的关联关系。</p>
                ) : (
                  <div className="graph-link-list">
                    {detail.outgoing_relations.map((relation) => (
                      <button
                        className="graph-link-item"
                        key={`out-${relation.target_id}-${relation.relation_type}`}
                        onClick={() => focusNode(relation.target_id)}
                        title="跳转到对端节点"
                        type="button"
                      >
                        <span className="graph-link-dir">出</span>
                        <i style={{ color: relationTypeColor(relation.relation_type) }}>{relationTypeLabel(relation.relation_type)}</i>
                        <strong>{relation.target_title}</strong>
                      </button>
                    ))}
                    {detail.incoming_relations.map((relation) => (
                      <button
                        className="graph-link-item"
                        key={`in-${relation.source_id}-${relation.relation_type}`}
                        onClick={() => focusNode(relation.source_id)}
                        title="跳转到对端节点"
                        type="button"
                      >
                        <span className="graph-link-dir in">入</span>
                        <i style={{ color: relationTypeColor(relation.relation_type) }}>{relationTypeLabel(relation.relation_type)}</i>
                        <strong>{relation.source_title}</strong>
                      </button>
                    ))}
                  </div>
                )}
              </section>
              <div className="graph-detail-actions">
                <button className="button secondary" onClick={() => { window.location.hash = "#/knowledge"; }} type="button">
                  在台账中查看
                </button>
                <button className="button ghost" onClick={() => setSelectedId(null)} type="button">
                  <X size={14} /> 关闭
                </button>
              </div>
            </>
          ) : null}
        </aside>
      </div>
    </div>
  );
}
