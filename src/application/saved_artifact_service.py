"""SavedArtifactService：用户显式保存的私有证据存档（M5-A 工具 A，ADR-004 扩展）。

语义（方案 M5-A · save_artifact）：
- 低风险：保存到当前主体 private 空间，操作者显式保存即确认，无企业审批流；
- 保存草稿 ≠ 发布：无任何共享/发布路径（M5-B+ 增量）；
- 幂等：idempotency_key 重复保存返回原 artifact（内容变化则拒绝——保存草稿
  的幂等语义是"同一 key = 同一份草稿"，避免静默覆盖）；
- owner 隔离：跨主体统一 not found，不暴露存在性；
- 证据快照 + checksum：保存的是当时证据的可回放副本。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from infrastructure.database import ProductDatabase, dumps


class SavedArtifactNotFoundError(LookupError):
    """统一 not-found：越权与不存在不可区分。"""


class SavedArtifactConflictError(RuntimeError):
    """同 key 保存了不同内容（幂等冲突：拒绝静默覆盖）。"""


class SavedArtifactValidationError(ValueError):
    pass


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


MAX_TITLE_LENGTH = 200
MAX_SNAPSHOT_ITEMS = 50


class SavedArtifactService:
    def __init__(self, database: ProductDatabase) -> None:
        self.database = database

    def save(
        self,
        *,
        principal_id: str,
        title: str,
        content: dict[str, Any] | None,
        evidence_snapshot: list[dict[str, Any]],
        citations: list[dict[str, Any]],
        idempotency_key: str,
        request_id: str | None = None,
        conversation_id: str | None = None,
        kind: str = "chat_evidence_snapshot",
    ) -> dict[str, Any]:
        clean_title = (title or "").strip()
        if not clean_title or len(clean_title) > MAX_TITLE_LENGTH:
            raise SavedArtifactValidationError(f"title: 1-{MAX_TITLE_LENGTH} chars required")
        if kind != "chat_evidence_snapshot":
            raise SavedArtifactValidationError(f"unsupported kind: {kind}")
        if not isinstance(evidence_snapshot, list) or len(evidence_snapshot) > MAX_SNAPSHOT_ITEMS:
            raise SavedArtifactValidationError(f"evidence_snapshot: 0-{MAX_SNAPSHOT_ITEMS} items")
        if not isinstance(citations, list):
            raise SavedArtifactValidationError("citations must be a list")
        if not idempotency_key or len(idempotency_key) < 8:
            raise SavedArtifactValidationError("idempotency_key: >=8 chars required")

        checksum = self._checksum(evidence_snapshot, citations, clean_title)
        existing = self.database.fetch_one(
            "SELECT artifact_id, checksum FROM saved_artifacts WHERE owner_principal_id=? AND idempotency_key=?",
            (principal_id, idempotency_key),
        )
        if existing is not None:
            if existing["checksum"] != checksum:
                raise SavedArtifactConflictError(
                    "同一保存键已对应不同内容；如需保存新草稿请使用新的保存键（不静默覆盖）"
                )
            return self._public(self._owned_row(existing["artifact_id"], principal_id))

        artifact_id = f"saved-{uuid.uuid4().hex[:16]}"
        now = _now_iso()
        self.database.execute(
            "INSERT INTO saved_artifacts (artifact_id, owner_principal_id, kind, title, content_json,"
            " visibility, request_id, conversation_id, evidence_snapshot_json, citations_json, checksum,"
            " idempotency_key, created_at, updated_at) VALUES (?,?,?,?,?, 'private', ?,?,?,?,?, ?, ?, ?)",
            (
                artifact_id, principal_id, kind, clean_title, dumps(content or {}), request_id, conversation_id,
                dumps(evidence_snapshot), dumps(citations), checksum, idempotency_key, now, now,
            ),
        )
        return self._public(self._owned_row(artifact_id, principal_id))

    def list_mine(self, *, principal_id: str, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        bound = max(1, min(int(limit), 100))
        if cursor:
            anchor = self._owned_row(cursor, principal_id)
            rows = self.database.fetch_all(
                "SELECT * FROM saved_artifacts WHERE owner_principal_id=?"
                " AND (created_at < ? OR (created_at = ? AND artifact_id < ?))"
                " ORDER BY created_at DESC, artifact_id DESC LIMIT ?",
                (principal_id, anchor["created_at"], anchor["created_at"], cursor, bound + 1),
            )
        else:
            rows = self.database.fetch_all(
                "SELECT * FROM saved_artifacts WHERE owner_principal_id=? ORDER BY created_at DESC, artifact_id DESC LIMIT ?",
                (principal_id, bound + 1),
            )
        has_more = len(rows) > bound
        page = rows[:bound]
        return {
            "items": [self._public(row) for row in page],
            "next_cursor": page[-1]["artifact_id"] if has_more and page else None,
        }

    def get_mine(self, *, artifact_id: str, principal_id: str) -> dict[str, Any]:
        return self._public_full(self._owned_row(artifact_id, principal_id))

    def delete_mine(self, *, artifact_id: str, principal_id: str) -> None:
        """删除自己的草稿（私有空间的普通用户动作；非物理审计证据，允许删）。"""
        self._owned_row(artifact_id, principal_id)
        self.database.execute(
            "DELETE FROM saved_artifacts WHERE artifact_id=? AND owner_principal_id=?",
            (artifact_id, principal_id),
        )

    # ── 内部 ──

    def _owned_row(self, artifact_id: str, principal_id: str) -> dict[str, Any]:
        row = self.database.fetch_one(
            "SELECT * FROM saved_artifacts WHERE artifact_id=? AND owner_principal_id=?",
            (artifact_id, principal_id),
        )
        if row is None:
            raise SavedArtifactNotFoundError(artifact_id)
        return row

    @staticmethod
    def _checksum(evidence_snapshot: list[dict[str, Any]], citations: list[dict[str, Any]], title: str) -> str:
        import json

        payload = json.dumps(
            {"title": title, "evidence": evidence_snapshot, "citations": citations},
            ensure_ascii=False, sort_keys=True, default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _public(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "artifact_id": row["artifact_id"],
            "kind": row["kind"],
            "title": row["title"],
            "visibility": row["visibility"],
            "checksum": row["checksum"],
            "request_id": row["request_id"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _public_full(row: dict[str, Any]) -> dict[str, Any]:
        import json

        payload = SavedArtifactService._public(row)
        payload.update(
            {
                "content": json.loads(row["content_json"] or "{}"),
                "evidence_snapshot": json.loads(row["evidence_snapshot_json"] or "[]"),
                "citations": json.loads(row["citations_json"] or "[]"),
                "updated_at": row["updated_at"],
            }
        )
        return payload
