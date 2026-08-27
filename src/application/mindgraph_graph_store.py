"""MindGraph 图谱查询（M1-D4）。

封装 note_relations / notes 的查询，供 MindGraphRetrievalPipeline 做图谱一跳扩展。
只读取「用户已确认（confirmed）」的关系，待确认（proposed）的关系不进入检索路径
（Human-in-the-loop 原则：AI 建议需用户确认后才生效）。
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable


RELATION_STATUSES = {"proposed", "confirmed", "rejected", "expired"}
TYPED_RELATION_TYPES = {
    "APPLIES_TO",
    "REQUIRES_APPROVAL",
    "HAS_LIMIT",
    "EXCEPTION_TO",
    "SUPERSEDES",
    "CONTRADICTS",
    "related_to",
    "references",
    "elaborates",
}
# 治理型 typed 关系必须引用具体原文 chunk（不能只靠无锚点的 span 摘要）。
GOVERNANCE_RELATION_TYPES = {
    "APPLIES_TO",
    "REQUIRES_APPROVAL",
    "HAS_LIMIT",
    "EXCEPTION_TO",
    "SUPERSEDES",
    "CONTRADICTS",
}
DEFAULT_RELATION_HOPS = 1
MAX_RELATION_HOPS = 2
DEFAULT_MAX_EDGES_PER_HOP = 50
DEFAULT_MAX_NODES_PER_HOP = 20


class MindGraphGraphStore:
    def __init__(self, db: Any) -> None:
        self.db = db

    def related_note_ids(
        self,
        note_ids: Iterable[str],
        status: str = "confirmed",
        *,
        hops: int = 1,
        access_scope: dict[str, Any] | None = None,
        as_of: str | None = None,
        max_edges_per_hop: int = DEFAULT_MAX_EDGES_PER_HOP,
        max_nodes_per_hop: int = DEFAULT_MAX_NODES_PER_HOP,
    ) -> list[dict[str, Any]]:
        ids = list(note_ids)
        if not ids:
            return []
        if hops < 1 or hops > MAX_RELATION_HOPS:
            raise ValueError(f"hops must be between 1 and {MAX_RELATION_HOPS}")
        if status not in RELATION_STATUSES:
            raise ValueError(f"unsupported relation status: {status}")
        for name, value in (
            ("max_edges_per_hop", max_edges_per_hop),
            ("max_nodes_per_hop", max_nodes_per_hop),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")

        target_date = date.fromisoformat(as_of).isoformat() if as_of else date.today().isoformat()
        seen: set[str] = set(ids)
        frontier = set(ids)
        results: list[dict[str, Any]] = []
        for hop in range(1, hops + 1):
            if not frontier:
                break
            placeholders = ",".join("?" * len(frontier))
            rows = self.db.fetch_all(
                f"""SELECT r.relation_id, r.source_note_id, r.target_note_id, r.relation_type, r.direction,
                           r.status, r.evidence_chunk_id, r.confidence, r.model_version, r.prompt_version,
                           r.proposed_at, r.resolved_at, r.resolved_by,
                           r.evidence_span, r.evidence_section, r.source_document_version,
                           r.effective_from, r.effective_to, r.extraction_method,
                           s.workspace AS source_workspace, s.department AS source_department,
                           s.acl_json AS source_acl_json, s.acl_public AS source_acl_public,
                           t.workspace AS target_workspace, t.department AS target_department,
                           t.acl_json AS target_acl_json, t.acl_public AS target_acl_public,
                           s.document_version AS source_note_version, t.document_version AS target_note_version,
                           s.effective_from AS source_note_effective_from, s.effective_to AS source_note_effective_to,
                           t.effective_from AS target_note_effective_from, t.effective_to AS target_note_effective_to
                    FROM note_relations r
                    JOIN notes s ON s.note_id = r.source_note_id
                    JOIN notes t ON t.note_id = r.target_note_id
                    WHERE (r.source_note_id IN ({placeholders}) OR r.target_note_id IN ({placeholders}))
                      AND r.status=?""",
                (*sorted(frontier), *sorted(frontier), status),
            )
            next_frontier: set[str] = set()
            accepted_edges = 0
            for row in rows:
                if accepted_edges >= max_edges_per_hop or len(next_frontier) >= max_nodes_per_hop:
                    break
                if row.get("relation_type") not in TYPED_RELATION_TYPES:
                    continue
                if not self._relation_is_effective(row, target_date):
                    continue
                has_chunk_evidence = row.get("evidence_chunk_id") is not None
                has_span_evidence = row.get("evidence_span") is not None
                if not has_chunk_evidence and not has_span_evidence:
                    continue
                if row.get("relation_type") in GOVERNANCE_RELATION_TYPES and not has_chunk_evidence:
                    continue
                # 双向遍历：命中笔记既可能是 source（outgoing）也可能是 target（incoming）。
                # 统一归一化为「命中笔记 → 扩展目标」，保证方向语义完整。
                if row["source_note_id"] in frontier:
                    from_id, to_id = row["source_note_id"], row["target_note_id"]
                    traversed_direction = "outgoing"
                else:
                    from_id, to_id = row["target_note_id"], row["source_note_id"]
                    traversed_direction = "incoming"
                if to_id in seen:
                    continue
                relation = self._row_to_relation(row, hop=hop, from_id=from_id, to_id=to_id, traversed_direction=traversed_direction)
                if access_scope is not None and not self._relation_visible(row, access_scope):
                    continue
                seen.add(to_id)
                next_frontier.add(to_id)
                results.append(relation)
                accepted_edges += 1
            frontier = next_frontier
        return results

    def note_titles(self, note_ids: Iterable[str]) -> dict[str, str]:
        ids = list(note_ids)
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        rows = self.db.fetch_all(
            f"SELECT note_id, title FROM notes WHERE note_id IN ({placeholders})",
            tuple(ids),
        )
        return {r["note_id"]: r["title"] for r in rows}

    def _row_to_relation(
        self,
        row: Any,
        *,
        hop: int,
        from_id: str | None = None,
        to_id: str | None = None,
        traversed_direction: str | None = None,
    ) -> dict[str, Any]:
        return {
            "relation_id": row["relation_id"],
            "source_note_id": from_id or row["source_note_id"],
            "target_note_id": to_id or row["target_note_id"],
            "relation_type": row["relation_type"],
            "direction": row["direction"],
            "traversed_direction": traversed_direction or row["direction"],
            "status": row["status"],
            "evidence": {
                "chunk_id": row["evidence_chunk_id"],
            },
            "evidence_chunk_id": row["evidence_chunk_id"],
            "evidence_span": row.get("evidence_span"),
            "evidence_section": row.get("evidence_section"),
            "source_document_version": row.get("source_document_version"),
            "effective_from": row.get("effective_from"),
            "effective_to": row.get("effective_to"),
            "extraction_method": row.get("extraction_method"),
            "confidence": row["confidence"],
            "model_version": row["model_version"],
            "prompt_version": row["prompt_version"],
            "proposed_at": row["proposed_at"],
            "resolved_at": row["resolved_at"],
            "resolved_by": row["resolved_by"],
            "hop": hop,
        }

    @staticmethod
    def _relation_visible(row: Any, access_scope: dict[str, Any]) -> bool:
        if access_scope is None:
            return True
        from application.access_control import note_acl_matches

        source = {
            "workspace": row.get("source_workspace"),
            "department": row.get("source_department"),
            "acl_json": row.get("source_acl_json"),
            "acl_public": row.get("source_acl_public"),
        }
        target = {
            "workspace": row.get("target_workspace"),
            "department": row.get("target_department"),
            "acl_json": row.get("target_acl_json"),
            "acl_public": row.get("target_acl_public"),
        }
        return note_acl_matches(source, access_scope) and note_acl_matches(target, access_scope)

    @staticmethod
    def _relation_is_effective(row: Any, target_date: str) -> bool:
        for prefix in ("source_note", "target_note"):
            effective_from = row.get(f"{prefix}_effective_from")
            effective_to = row.get(f"{prefix}_effective_to")
            if effective_from and effective_from > target_date:
                return False
            if effective_to and effective_to < target_date:
                return False
        relation_effective_from = row.get("effective_from")
        relation_effective_to = row.get("effective_to")
        if relation_effective_from and relation_effective_from > target_date:
            return False
        if relation_effective_to and relation_effective_to < target_date:
            return False
        return True
