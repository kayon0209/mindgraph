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

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from api.auth import current_actor, get_required_principal, require_role, resolve_access_scope
from api.dependencies import get_container
from application.access_control import note_acl_matches, record_access_audit
from application.mindgraph_graph_store import TYPED_RELATION_TYPES

logger = logging.getLogger("mindgraph.api.readonly")
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


def _governance_from_row(row: dict) -> dict:
    try:
        issues = json.loads(row.get("metadata_issues_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        issues = ["invalid_metadata_issues"]
    if not isinstance(issues, list):
        issues = ["invalid_metadata_issues"]
    derived_issues = []
    if not row.get("owner"):
        derived_issues.append("missing_owner")
    if not row.get("policy_key"):
        derived_issues.append("missing_policy_key")
    if not row.get("document_version"):
        derived_issues.append("missing_version")
    if not row.get("effective_from"):
        derived_issues.append("missing_effective_from")
    if not row.get("policy_status") or row.get("policy_status") == "unspecified":
        derived_issues.append("missing_policy_status")
    for issue in derived_issues:
        if issue not in issues:
            issues.append(issue)
    complete = bool(
        row.get("owner")
        and row.get("policy_key")
        and row.get("document_version")
        and row.get("effective_from")
        and row.get("policy_status") != "unspecified"
        and not issues
    )
    return {
        "owner": row.get("owner"),
        "policy_key": row.get("policy_key"),
        "version": row.get("document_version"),
        "effective_from": row.get("effective_from"),
        "effective_to": row.get("effective_to"),
        "policy_status": row.get("policy_status") or "unspecified",
        "metadata_complete": complete,
        "issues": issues,
    }


def _note_item(row: dict) -> dict:
    return {
        "id": row["note_id"],
        "title": row["title"],
        "vault_path": row["vault_path"],
        "category": row.get("department") or row.get("workspace") or _category_from_path(row["vault_path"]),
        "access_level": row["ai_access_level"],
        "workspace": row.get("workspace"),
        "department": row.get("department"),
        "status": row["index_status"],
        "chunk_count": row["chunk_count"],
        "updated": (row["updated_at"] or "")[:10],
        "excerpt": _excerpt_from_fm(row.get("frontmatter_json")),
        "governance": _governance_from_row(row),
    }


@router.get("/notes")
def list_notes(
    request: Request,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    status: str | None = None,
    policy_status: str | None = None,
    governance: Literal["complete", "incomplete"] | None = None,
    workspace: str | None = None,
    department: str | None = None,
    q: str | None = None,
):
    """知识库笔记列表（分页 / 按 index_status 过滤 / 关键词搜索 / ACL 裁剪）。"""
    db = get_container().database
    access_scope = resolve_access_scope(request)
    actor = current_actor(request)
    where, params = [], []
    if status:
        where.append("index_status = ?")
        params.append(status)
    if policy_status:
        where.append("policy_status = ?")
        params.append(policy_status)
    if workspace:
        where.append("workspace = ?")
        params.append(workspace)
    if department:
        where.append("department = ?")
        params.append(department)
    governance_complete_sql = (
        "COALESCE(TRIM(owner), '') <> '' AND COALESCE(TRIM(policy_key), '') <> '' "
        "AND COALESCE(TRIM(document_version), '') <> '' "
        "AND COALESCE(TRIM(effective_from), '') <> '' AND policy_status <> 'unspecified' "
        "AND metadata_issues_json = '[]'"
    )
    if governance == "complete":
        where.append(f"({governance_complete_sql})")
    elif governance == "incomplete":
        where.append(f"NOT ({governance_complete_sql})")
    if q:
        where.append("(title LIKE ? OR vault_path LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = db.fetch_all(
        f"""SELECT note_id, vault_path, title, ai_access_level, chunk_count,
                   index_status, updated_at, frontmatter_json, owner, document_version,
                   policy_key, effective_from, effective_to, policy_status, metadata_issues_json,
                   workspace, department, acl_json, acl_public
            FROM notes {where_sql}
            ORDER BY updated_at DESC""",
        tuple(params),
    )
    visible = [r for r in rows if note_acl_matches(r, access_scope)]
    total = len(visible)
    page = visible[offset : offset + limit]
    record_access_audit(
        db,
        actor=actor,
        action="list_notes",
        resource="notes",
        decision="allow" if total or not access_scope else "deny",
        reason=None,
        metadata={
            "filters": {"status": status, "policy_status": policy_status, "q": q, "workspace": workspace, "department": department},
            "matched": total,
            "scope_user": (access_scope or {}).get("user"),
        },
    )
    return {"total": total, "items": [_note_item(r) for r in page]}


@router.get("/notes/{note_id}")
def get_note(note_id: str, request: Request):
    """单篇笔记详情 + 其 incoming/outgoing 的 confirmed 关系（ACL 裁剪）。"""
    db = get_container().database
    access_scope = resolve_access_scope(request)
    actor = current_actor(request)
    row = db.fetch_one(
        """SELECT note_id, vault_path, title, ai_access_level, chunk_count,
                  index_status, updated_at, frontmatter_json, created_at, owner,
                  document_version, policy_key, effective_from, effective_to, policy_status,
                  metadata_issues_json, workspace, department, acl_json, acl_public
           FROM notes WHERE note_id = ?""",
        (note_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="note not found")
    if not note_acl_matches(row, access_scope):
        record_access_audit(
            db,
            actor=actor,
            action="get_note",
            resource=f"notes/{note_id}",
            decision="deny",
            reason="out_of_scope",
            metadata={"scope_user": (access_scope or {}).get("user")},
        )
        raise HTTPException(status_code=404, detail="note not found")
    record_access_audit(
        db,
        actor=actor,
        action="get_note",
        resource=f"notes/{note_id}",
        decision="allow",
        metadata={"scope_user": (access_scope or {}).get("user")},
    )
    store = get_container().mindgraph_graph_store
    outgoing = store.related_note_ids([note_id], status="confirmed", access_scope=access_scope)
    incoming_rows = db.fetch_all(
        """SELECT r.source_note_id, r.relation_type, r.confidence,
                  n.workspace, n.department, n.acl_json, n.acl_public
           FROM note_relations r JOIN notes n ON n.note_id = r.source_note_id
           WHERE r.target_note_id = ? AND r.status = 'confirmed'""",
        (note_id,),
    )
    incoming_rows = [item for item in incoming_rows if note_acl_matches(item, access_scope)]
    related_ids = [o["target_note_id"] for o in outgoing] + [i["source_note_id"] for i in incoming_rows]
    titles = store.note_titles(related_ids)
    return {
        "id": row["note_id"],
        "title": row["title"],
        "vault_path": row["vault_path"],
        "category": _category_from_path(row["vault_path"]),
        "access_level": row["ai_access_level"],
        "workspace": row.get("workspace"),
        "department": row.get("department"),
        "status": row["index_status"],
        "chunk_count": row["chunk_count"],
        "updated": (row["updated_at"] or "")[:10],
        "created": (row["created_at"] or "")[:10],
        "excerpt": _excerpt_from_fm(row["frontmatter_json"]),
        "governance": _governance_from_row(row),
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
def evaluation_ablation(request: Request):
    """评测看板：真实知识库规模 + 真实 evaluation_runs（按策略分组的消融结果）。"""
    db = get_container().database
    access_scope = resolve_access_scope(request)
    actor = current_actor(request)

    def cnt(sql: str, params=()) -> int:
        row = db.fetch_one(sql, params)
        return row["c"] if row else 0

    if access_scope:
        notes_rows = db.fetch_all("SELECT note_id, workspace, department, acl_json, acl_public FROM notes")
        visible_ids = {r["note_id"] for r in notes_rows if note_acl_matches(r, access_scope)}
        notes_total = len(visible_ids)
        relations_confirmed = 0
        relations_proposed = 0
        if visible_ids:
            placeholders = ",".join("?" for _ in visible_ids)
            relations_confirmed = cnt(f"SELECT COUNT(*) AS c FROM note_relations WHERE status='confirmed' AND source_note_id IN ({placeholders}) AND target_note_id IN ({placeholders})", tuple(visible_ids) * 2)
            relations_proposed = cnt(f"SELECT COUNT(*) AS c FROM note_relations WHERE status='proposed' AND source_note_id IN ({placeholders}) AND target_note_id IN ({placeholders})", tuple(visible_ids) * 2)
    else:
        notes_total = cnt("SELECT COUNT(*) AS c FROM notes")
        relations_confirmed = cnt("SELECT COUNT(*) AS c FROM note_relations WHERE status='confirmed'")
        relations_proposed = cnt("SELECT COUNT(*) AS c FROM note_relations WHERE status='proposed'")
    idx = _active_index_stats()
    chunks_total = idx["chunks"]
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
    record_access_audit(db, actor=actor, action="evaluation_ablation", resource="evaluation/ablation", decision="allow", metadata={"notes_total": notes_total, "scope_user": (access_scope or {}).get("user")})
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
def list_proposed(request: Request, limit: int = Query(200, ge=1, le=500)):
    """链接建议队列（Human-in-the-loop）：proposed 关系 + 采纳率趋势 + 冲突检测。"""
    db = get_container().database
    access_scope = resolve_access_scope(request)
    actor = current_actor(request)
    store = get_container().mindgraph_graph_store
    rows = db.fetch_all(
        """SELECT relation_id, source_note_id, target_note_id, relation_type,
                  confidence, proposed_at, evidence_chunk_id
           FROM note_relations WHERE status='proposed'
           ORDER BY confidence DESC LIMIT ?""",
        (limit,),
    )
    note_ids = [r["source_note_id"] for r in rows] + [r["target_note_id"] for r in rows]
    note_rows = {}
    if note_ids:
        placeholders = ",".join("?" for _ in note_ids)
        fetched = db.fetch_all(f"SELECT note_id, title, vault_path, workspace, department, acl_json, acl_public FROM notes WHERE note_id IN ({placeholders})", tuple(note_ids))
        note_rows = {row["note_id"]: row for row in fetched}
    titles = store.note_titles(note_ids)
    confirmed_pairs = set()
    for c in db.fetch_all("SELECT source_note_id, target_note_id FROM note_relations WHERE status='confirmed'"):
        confirmed_pairs.add((c["source_note_id"], c["target_note_id"]))
        confirmed_pairs.add((c["target_note_id"], c["source_note_id"]))
    items = []
    for r in rows:
        source_row = note_rows.get(r["source_note_id"])
        target_row = note_rows.get(r["target_note_id"])
        if not source_row or not target_row:
            continue
        if not note_acl_matches(source_row, access_scope) or not note_acl_matches(target_row, access_scope):
            continue
        s, t = r["source_note_id"], r["target_note_id"]
        conflict = (s, t) in confirmed_pairs or (t, s) in confirmed_pairs
        items.append({
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
        })
    record_access_audit(db, actor=actor, action="list_relations_proposed", resource="note_relations/proposed", decision="allow", metadata={"count": len(items)})
    trend_rows = db.fetch_all(
        """SELECT substr(resolved_at,1,7) AS month, COUNT(*) AS c
           FROM note_relations WHERE status='confirmed' AND resolved_at IS NOT NULL
           GROUP BY month ORDER BY month"""
    )
    adoption_trend = [{"month": tr["month"], "count": tr["c"]} for tr in trend_rows]
    return {"proposed": items, "adoption_trend": adoption_trend}


@router.get("/relations/confirmed")
def list_confirmed(request: Request, limit: int = Query(200, ge=1, le=500)):
    """已确认关系列表（已进入 Graph RAG 检索路径），用于知识库关系概览。"""
    db = get_container().database
    access_scope = resolve_access_scope(request)
    actor = current_actor(request)
    store = get_container().mindgraph_graph_store
    rows = db.fetch_all(
        """SELECT relation_id, source_note_id, target_note_id, relation_type, confidence
           FROM note_relations WHERE status='confirmed'
           ORDER BY confidence DESC LIMIT ?""",
        (limit,),
    )
    note_ids = [r["source_note_id"] for r in rows] + [r["target_note_id"] for r in rows]
    note_rows = {}
    if note_ids:
        placeholders = ",".join("?" for _ in note_ids)
        fetched = db.fetch_all(f"SELECT note_id, title, vault_path, workspace, department, acl_json, acl_public FROM notes WHERE note_id IN ({placeholders})", tuple(note_ids))
        note_rows = {row["note_id"]: row for row in fetched}
    titles = store.note_titles(note_ids)
    items = []
    for r in rows:
        source_row = note_rows.get(r["source_note_id"])
        target_row = note_rows.get(r["target_note_id"])
        if not source_row or not target_row:
            continue
        if not note_acl_matches(source_row, access_scope) or not note_acl_matches(target_row, access_scope):
            continue
        items.append({
            "id": r["relation_id"],
            "source": titles.get(r["source_note_id"], r["source_note_id"]),
            "target": titles.get(r["target_note_id"], r["target_note_id"]),
            "source_id": r["source_note_id"],
            "target_id": r["target_note_id"],
            "type": r["relation_type"],
            "confidence": r["confidence"],
        })
    record_access_audit(db, actor=actor, action="list_relations_confirmed", resource="note_relations/confirmed", decision="allow", metadata={"count": len(items)})
    return {"confirmed": items}


class ResolveBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["confirm", "reject"]
    reason: str = Field(min_length=1, max_length=500)


@router.post("/relations/{relation_id}/resolve")
def resolve_relation(
    relation_id: str,
    body: ResolveBody,
    request: Request,
    reviewer: dict = Depends(require_role("admin")),
):
    """确认（confirm）或拒绝（reject）一条 proposed 关系（需对两端笔记有 ACL 权限）。

    confirm 后 status=confirmed，该关系进入 Graph RAG 检索路径。
    """
    db = get_container().database
    access_scope = resolve_access_scope(request)
    actor = reviewer.get("name") or reviewer.get("username") or "anonymous"
    row = db.fetch_one("SELECT relation_id, status, source_note_id, target_note_id FROM note_relations WHERE relation_id=?", (relation_id,))
    if not row:
        raise HTTPException(status_code=404, detail="relation not found")
    if row["status"] != "proposed":
        raise HTTPException(status_code=409, detail="only proposed relations can be resolved")
    relation_detail = db.fetch_one(
        "SELECT relation_type, evidence_chunk_id, evidence_span FROM note_relations WHERE relation_id=?",
        (relation_id,),
    )
    if not relation_detail or relation_detail["relation_type"] not in TYPED_RELATION_TYPES or (not relation_detail["evidence_chunk_id"] and not relation_detail["evidence_span"]):
        raise HTTPException(status_code=409, detail="relation lacks an allowed type or evidence")
    note_rows = db.fetch_all(
        "SELECT note_id, title, vault_path, workspace, department, acl_json, acl_public FROM notes WHERE note_id IN (?, ?)",
        (row["source_note_id"], row["target_note_id"]),
    )
    note_by_id = {n["note_id"]: n for n in note_rows}
    for nid in (row["source_note_id"], row["target_note_id"]):
        n = note_by_id.get(nid)
        if not n or not note_acl_matches(n, access_scope):
            record_access_audit(db, actor=actor, action="resolve_relation", resource=f"note_relations/{relation_id}", decision="deny", reason="out_of_scope")
            raise HTTPException(status_code=403, detail="not allowed to resolve this relation")
    new_status = "confirmed" if body.decision == "confirm" else "rejected"
    db.execute(
        "UPDATE note_relations SET status=?, resolved_at=?, resolved_by=? WHERE relation_id=?",
        (new_status, _now_iso(), actor, relation_id),
    )
    record_access_audit(
        db,
        actor=actor,
        action="resolve_relation",
        resource=f"note_relations/{relation_id}",
        decision="allow",
        metadata={"new_status": new_status, "reason": body.reason},
    )
    return {"ok": True, "relation_id": relation_id, "status": new_status}


class ResolveBatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ids: list[str] = Field(min_length=1, max_length=200)
    decision: Literal["reject"]
    reason: str = Field(min_length=1, max_length=500)


@router.post("/relations/resolve-batch")
def resolve_relations_batch(
    body: ResolveBatchBody,
    request: Request,
    reviewer: dict = Depends(require_role("admin")),
):
    """批量拒绝 proposed 关系；确认必须逐条核对证据并调用单条端点。"""
    db = get_container().database
    access_scope = resolve_access_scope(request)
    actor = reviewer.get("name") or reviewer.get("username") or "anonymous"
    placeholders = ",".join("?" for _ in body.ids)
    rows = db.fetch_all(
        f"""SELECT r.relation_id, r.status, r.relation_type, r.evidence_chunk_id, r.evidence_span,
                   r.source_note_id, r.target_note_id,
                   s.workspace AS source_workspace, s.department AS source_department,
                   s.acl_json AS source_acl_json, s.acl_public AS source_acl_public,
                   t.workspace AS target_workspace, t.department AS target_department,
                   t.acl_json AS target_acl_json, t.acl_public AS target_acl_public
            FROM note_relations r
            JOIN notes s ON s.note_id = r.source_note_id
            JOIN notes t ON t.note_id = r.target_note_id
            WHERE r.relation_id IN ({placeholders})""",
        tuple(body.ids),
    )
    permitted = []
    denied = []
    for row in rows:
        source = {"workspace": row["source_workspace"], "department": row["source_department"], "acl_json": row["source_acl_json"], "acl_public": row["source_acl_public"]}
        target = {"workspace": row["target_workspace"], "department": row["target_department"], "acl_json": row["target_acl_json"], "acl_public": row["target_acl_public"]}
        relation_type = row.get("relation_type")
        has_evidence = row.get("evidence_chunk_id") or row.get("evidence_span")
        if row["status"] == "proposed" and relation_type in TYPED_RELATION_TYPES and has_evidence and note_acl_matches(source, access_scope) and note_acl_matches(target, access_scope):
            permitted.append(row["relation_id"])
        else:
            denied.append(row["relation_id"])
    new_status = "rejected"
    ts = _now_iso()
    for rid in permitted:
        db.execute(
            "UPDATE note_relations SET status=?, resolved_at=?, resolved_by=? WHERE relation_id=?",
            (new_status, ts, actor, rid),
        )
        record_access_audit(
            db,
            actor=actor,
            action="resolve_relation",
            resource=f"note_relations/{rid}",
            decision="allow",
            metadata={"new_status": new_status, "batch": True, "reason": body.reason},
        )
    for rid in denied:
        record_access_audit(db, actor=actor, action="resolve_relation", resource=f"note_relations/{rid}", decision="deny", reason="out_of_scope_or_not_proposed", metadata={"batch": True})
    return {"ok": True, "processed": len(permitted), "skipped": len(body.ids) - len(permitted), "status": new_status}


class ExtractBody(BaseModel):
    method: str = "embedding"  # 'embedding' | 'all'
    top_k: int = 5
    similarity_threshold: float = 0.5
    max_candidates: int = 300
    use_llm: bool = False
    dry_run: bool = False


@router.post("/relations/extract")
def extract_relations(
    body: ExtractBody,
    principal: dict = Depends(get_required_principal),
    reviewer: dict = Depends(require_role("admin")),
):
    """自动抽取候选关系（Human-in-the-loop：仅写 proposed）。

    - 默认 method='embedding'：基于离线 BGE 笔记语义相似度发现候选，无需 LLM；
    - use_llm=True：对高相似度候选用已配置 Chat Provider 精炼关系类型与依据；
    - dry_run=True：只预测不落库。
    已存在（任意状态/任一方向）的 pair 自动去重，避免重复写入。
    """
    container = get_container()
    svc = container.relation_extraction
    actor = reviewer.get("name") or reviewer.get("username") or "anonymous"
    result = svc.extract(
        method=body.method,
        top_k=body.top_k,
        similarity_threshold=body.similarity_threshold,
        max_candidates=body.max_candidates,
        use_llm=body.use_llm,
        dry_run=body.dry_run,
    )
    if not result.get("ok", True):
        record_access_audit(
            container.database,
            actor=actor,
            action="extract_relations",
            resource="note_relations/proposed",
            decision="deny",
            reason=result.get("reason", "extract_failed"),
            metadata={"method": body.method, "dry_run": body.dry_run, "use_llm": body.use_llm},
        )
        raise HTTPException(status_code=409, detail=result.get("reason", "extract_failed"))
    record_access_audit(
        container.database,
        actor=actor,
        action="extract_relations",
        resource="note_relations/proposed",
        decision="allow",
        metadata={
            "method": body.method,
            "dry_run": body.dry_run,
            "use_llm": body.use_llm,
            "created": int(result.get("created", 0) or 0),
        },
    )
    return result
