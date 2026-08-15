"""MindGraph 图谱查询（M1-D4）。

封装 note_relations / notes 的查询，供 MindGraphRetrievalPipeline 做图谱一跳扩展。
只读取「用户已确认（confirmed）」的关系，待确认（proposed）的关系不进入检索路径
（Human-in-the-loop 原则：AI 建议需用户确认后才生效）。
"""
from __future__ import annotations

from typing import Any, Iterable


class MindGraphGraphStore:
    def __init__(self, db: Any) -> None:
        self.db = db

    def related_note_ids(self, note_ids: Iterable[str], status: str = "confirmed") -> list[dict[str, Any]]:
        ids = list(note_ids)
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        rows = self.db.fetch_all(
            f"""SELECT source_note_id, target_note_id, relation_type, status,
                       evidence_chunk_id, confidence
                FROM note_relations
                WHERE source_note_id IN ({placeholders}) AND status=?""",
            (*ids, status),
        )
        return [
            {
                "source_note_id": r["source_note_id"],
                "target_note_id": r["target_note_id"],
                "relation_type": r["relation_type"],
                "status": r["status"],
                "evidence_chunk_id": r["evidence_chunk_id"],
                "confidence": r["confidence"],
            }
            for r in rows
        ]

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
