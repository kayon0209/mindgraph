"""MindGraph 只读 API（知识库浏览 / 评测看板 / 链接建议队列）。

所有数据来自真实 SQLite（notes / note_relations / evaluation_runs），无任何 Mock。
挂载于 /api/v1/mindgraph/notes、/evaluation/ablation、/relations/proposed。
Human-in-the-loop：POST /relations/{id}/resolve 把 proposed 关系确认为 confirmed（进入检索路径）或拒绝。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from api.dependencies import get_container

logger = logging.getLogger("expense_rag.api.mindgraph_readonly")
router = APIRouter(prefix="/mindgraph", tags=["mindgraph-readonly"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _category_from_path(vault_path: str) -> str:
    parts = [p for p in vault_path.replace("\\", "/").split("/") if p]
    return parts[-2] if len(parts) >= 2 else "根目录"


def _excerpt_from_fm(frontmatter_json: str | None) -> str:
    if not frontmatter_json:
        return ""
    try:
        fm = json.loads(frontmatter_json)
    except Exception:
        return ""
    if not isinstance(fm, dict):
        return ""
    return (fm.get("summary") or fm.get("description") or fm.get("excerpt") or "").strip()


def _note_item(row: dict) -> dict:
    return {
        "id": row["note_id"],
        "title": row["title"],
        "vault_path": row["vault_path"],
        "category": _category_from_path(row["vault_path"]),
        "access_level": row["ai_access_level"],
        "status": row["index_status"],
        "chunk_count": row["chunk_count"],
        "updated": (row["updated_at"] or "")[:10],
        "excerpt": _excerpt_from_fm(row.get("frontmatter_json")),
    }


@router.get("/notes")
def list_notes(
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    status: str | None = None,
    q: str | None = None,
):
    """知识库笔记列表（分页 / 按 index_status 过滤 / 关键词搜索）。"""
    db = get_container().database
    where, params = [], []
    if status:
        where.append("index_status = ?")
        params.append(status)
    if q:
        where.append("(title LIKE ? OR vault_path LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = db.fetch_all(
        f"""SELECT note_id, vault_path, title, ai_access_level, chunk_count,
                   index_status, updated_at, frontmatter_json
            FROM notes {where_sql}
            ORDER BY updated_at DESC LIMIT ? OFFSET ?""",
        (*params, limit, offset),
    )
    total_row = db.fetch_one(f"SELECT COUNT(*) AS c FROM notes {where_sql}", tuple(params))
    total = total_row["c"] if total_row else 0
    return {"total": total, "items": [_note_item(r) for r in rows]}


@router.get("/notes/{note_id}")
def get_note(note_id: str):
    """单篇笔记详情 + 其 incoming/outgoing 的 confirmed 关系。"""
    db = get_container().database
    row = db.fetch_one(
        """SELECT note_id, vault_path, title, ai_access_level, chunk_count,
                  index_status, updated_at, frontmatter_json, created_at
           FROM notes WHERE note_id = ?""",
        (note_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="note not found")
    store = get_container().mindgraph_graph_store
    outgoing = store.related_note_ids([note_id], status="confirmed")
    incoming_rows = db.fetch_all(
        """SELECT source_note_id, relation_type, confidence
           FROM note_relations WHERE target_note_id = ? AND status = 'confirmed'""",
        (note_id,),
    )
    related_ids = [o["target_note_id"] for o in outgoing] + [i["source_note_id"] for i in incoming_rows]
    titles = store.note_titles(related_ids)
    return {
        "id": row["note_id"],
        "title": row["title"],
        "vault_path": row["vault_path"],
        "category": _category_from_path(row["vault_path"]),
        "access_level": row["ai_access_level"],
        "status": row["index_status"],
        "chunk_count": row["chunk_count"],
        "updated": (row["updated_at"] or "")[:10],
        "created": (row["created_at"] or "")[:10],
        "excerpt": _excerpt_from_fm(row["frontmatter_json"]),
        "outgoing_relations": [
            {
                "target_id": o["target_note_id"],
                "target_title": titles.get(o["target_note_id"], o["target_note_id"]),
                "relation_type": o["relation_type"],
                "confidence": o["confidence"],
            }
            for o in outgoing
        ],
        "incoming_relations": [
            {
                "source_id": i["source_note_id"],
                "source_title": titles.get(i["source_note_id"], i["source_note_id"]),
                "relation_type": i["relation_type"],
                "confidence": i["confidence"],
            }
            for i in incoming_rows
        ],
    }


def _active_index_stats() -> dict:
    """读取当前激活索引的 manifest，返回真实 chunk / note 数量。"""
    try:
        root = get_container().mindgraph_index_root
        cur = root / "CURRENT"
        if not cur.exists():
            return {"chunks": 0, "notes": 0}
        version = cur.read_text(encoding="utf-8").strip()
        mpath = root / version / "manifest.json"
        if not mpath.exists():
            return {"chunks": 0, "notes": 0}
        m = json.loads(mpath.read_text(encoding="utf-8"))
        return {"chunks": m.get("chunk_count", 0), "notes": m.get("note_count", 0)}
    except Exception:
        return {"chunks": 0, "notes": 0}


@router.get("/evaluation/ablation")
def evaluation_ablation():
    """评测看板：真实知识库规模 + 真实 evaluation_runs（按策略分组的消融结果）。"""
    db = get_container().database

    def cnt(sql: str, params=()) -> int:
        row = db.fetch_one(sql, params)
        return row["c"] if row else 0

    notes_total = cnt("SELECT COUNT(*) AS c FROM notes")
    idx = _active_index_stats()
    chunks_total = idx["chunks"]
    relations_confirmed = cnt("SELECT COUNT(*) AS c FROM note_relations WHERE status='confirmed'")
    relations_proposed = cnt("SELECT COUNT(*) AS c FROM note_relations WHERE status='proposed'")
    indexed_notes = idx["notes"]

    run_rows = db.fetch_all(
        """SELECT run_id, status, dataset_name, retrieval_strategy, chat_model,
                  started_at, finished_at, summary_metrics_json
           FROM evaluation_runs ORDER BY started_at DESC LIMIT 20"""
    )
    runs = []
    for r in run_rows:
        try:
            metrics = json.loads(r["summary_metrics_json"]) if r["summary_metrics_json"] else {}
        except Exception:
            metrics = {}
        runs.append(
            {
                "run_id": r["run_id"],
                "status": r["status"],
                "dataset": r["dataset_name"],
                "strategy": r["retrieval_strategy"],
                "model": r["chat_model"],
                "started_at": r["started_at"],
                "finished_at": r["finished_at"],
                "metrics": metrics,
            }
        )
    return {
        "library_stats": {
            "notes_total": notes_total,
            "chunks_total": chunks_total,
            "relations_confirmed": relations_confirmed,
            "relations_proposed": relations_proposed,
            "indexed_notes": indexed_notes,
        },
        "runs": runs,
    }


@router.get("/relations/proposed")
def list_proposed(limit: int = Query(200, ge=1, le=500)):
    """链接建议队列（Human-in-the-loop）：proposed 关系 + 采纳率趋势 + 冲突检测。"""
    db = get_container().database
    store = get_container().mindgraph_graph_store
    rows = db.fetch_all(
        """SELECT relation_id, source_note_id, target_note_id, relation_type,
                  confidence, proposed_at, evidence_chunk_id
           FROM note_relations WHERE status='proposed'
           ORDER BY confidence DESC LIMIT ?""",
        (limit,),
    )
    titles = store.note_titles(
        [r["source_note_id"] for r in rows] + [r["target_note_id"] for r in rows]
    )
    # 冲突检测：是否已存在 confirmed 的同 pair（任一方向）
    confirmed_pairs = set()
    for c in db.fetch_all(
        "SELECT source_note_id, target_note_id FROM note_relations WHERE status='confirmed'"
    ):
        confirmed_pairs.add((c["source_note_id"], c["target_note_id"]))
        confirmed_pairs.add((c["target_note_id"], c["source_note_id"]))

    items = []
    for r in rows:
        s, t = r["source_note_id"], r["target_note_id"]
        conflict = (s, t) in confirmed_pairs or (t, s) in confirmed_pairs
        items.append(
            {
                "id": r["relation_id"],
                "source": titles.get(s, s),
                "target": titles.get(t, t),
                "source_id": s,
                "target_id": t,
                "type": r["relation_type"],
                "confidence": r["confidence"],
                "proposed_at": (r["proposed_at"] or "")[:10],
                "evidence_chunk_id": r["evidence_chunk_id"],
                "conflict": conflict,
            }
        )

    trend_rows = db.fetch_all(
        """SELECT substr(resolved_at,1,7) AS month, COUNT(*) AS c
           FROM note_relations WHERE status='confirmed' AND resolved_at IS NOT NULL
           GROUP BY month ORDER BY month"""
    )
    adoption_trend = [{"month": tr["month"], "count": tr["c"]} for tr in trend_rows]
    return {"proposed": items, "adoption_trend": adoption_trend}


@router.get("/relations/confirmed")
def list_confirmed(limit: int = Query(200, ge=1, le=500)):
    """已确认关系列表（已进入 Graph RAG 检索路径），用于知识库关系概览。"""
    db = get_container().database
    store = get_container().mindgraph_graph_store
    rows = db.fetch_all(
        """SELECT relation_id, source_note_id, target_note_id, relation_type, confidence
           FROM note_relations WHERE status='confirmed'
           ORDER BY confidence DESC LIMIT ?""",
        (limit,),
    )
    titles = store.note_titles(
        [r["source_note_id"] for r in rows] + [r["target_note_id"] for r in rows]
    )
    items = [
        {
            "id": r["relation_id"],
            "source": titles.get(r["source_note_id"], r["source_note_id"]),
            "target": titles.get(r["target_note_id"], r["target_note_id"]),
            "source_id": r["source_note_id"],
            "target_id": r["target_note_id"],
            "type": r["relation_type"],
            "confidence": r["confidence"],
        }
        for r in rows
    ]
    return {"confirmed": items}


class ResolveBody(BaseModel):
    decision: Literal["confirm", "reject"]
    resolved_by: str = "user"


@router.post("/relations/{relation_id}/resolve")
def resolve_relation(relation_id: str, body: ResolveBody):
    """确认（confirm）或拒绝（reject）一条 proposed 关系。

    confirm 后 status=confirmed，该关系进入 Graph RAG 检索路径。
    """
    db = get_container().database
    row = db.fetch_one("SELECT relation_id, status FROM note_relations WHERE relation_id=?", (relation_id,))
    if not row:
        raise HTTPException(status_code=404, detail="relation not found")
    new_status = "confirmed" if body.decision == "confirm" else "rejected"
    db.execute(
        "UPDATE note_relations SET status=?, resolved_at=?, resolved_by=? WHERE relation_id=?",
        (new_status, _now_iso(), body.resolved_by, relation_id),
    )
    return {"ok": True, "relation_id": relation_id, "status": new_status}


class ResolveBatchBody(BaseModel):
    ids: list[str]
    decision: Literal["confirm", "reject"]
    resolved_by: str = "user"


@router.post("/relations/resolve-batch")
def resolve_relations_batch(body: ResolveBatchBody):
    """批量确认 / 拒绝若干 proposed 关系（Human-in-the-loop）。

    用于「一键确认高置信度候选」等场景；仅作用于传入的 relation_id 列表。
    返回成功处理的数量与跳过的无效 id。
    """
    if not body.ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")
    db = get_container().database
    placeholders = ",".join("?" for _ in body.ids)
    rows = db.fetch_all(
        f"SELECT relation_id, status FROM note_relations WHERE relation_id IN ({placeholders})",
        tuple(body.ids),
    )
    valid = {r["relation_id"] for r in rows}
    new_status = "confirmed" if body.decision == "confirm" else "rejected"
    ts = _now_iso()
    for rid in body.ids:
        if rid in valid:
            db.execute(
                "UPDATE note_relations SET status=?, resolved_at=?, resolved_by=? WHERE relation_id=?",
                (new_status, ts, body.resolved_by, rid),
            )
    return {"ok": True, "processed": len(valid), "skipped": len(body.ids) - len(valid), "status": new_status}


class ExtractBody(BaseModel):
    method: str = "embedding"  # 'embedding' | 'all'
    top_k: int = 5
    similarity_threshold: float = 0.5
    max_candidates: int = 300
    use_llm: bool = False
    dry_run: bool = False


@router.post("/relations/extract")
def extract_relations(body: ExtractBody):
    """自动抽取候选关系（Human-in-the-loop：仅写 proposed）。

    - 默认 method='embedding'：基于离线 BGE 笔记语义相似度发现候选，无需 LLM；
    - use_llm=True：对高相似度候选用已配置 Chat Provider 精炼关系类型与依据；
    - dry_run=True：只预测不落库。
    已存在（任意状态/任一方向）的 pair 自动去重，避免重复写入。
    """
    svc = get_container().relation_extraction
    result = svc.extract(
        method=body.method,
        top_k=body.top_k,
        similarity_threshold=body.similarity_threshold,
        max_candidates=body.max_candidates,
        use_llm=body.use_llm,
        dry_run=body.dry_run,
    )
    if not result.get("ok", True):
        raise HTTPException(status_code=409, detail=result.get("reason", "extract_failed"))
    return result
