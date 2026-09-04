"""TaskService：企业级后台任务的提交、查询、取消（M4-A，ADR-004）。

Worker 执行逻辑在 application/task_worker.py；本服务只负责队列与状态语义：
- 幂等提交：UNIQUE(principal_id, idempotency_key)，重复提交返回原任务；
- owner 隔离：跨主体一律 not found（不暴露存在性）；
- 协作式取消：只置 cancel_requested_at，Worker 在步骤边界检查；
- 轨迹脱敏：steps_json 只存步骤名/状态/耗时，不存检索词与证据正文。
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from domain.task_models import (
    ALLOWED_CONSTRAINT_KEYS,
    MAX_CONSTRAINT_TOP_K,
    MAX_CONSTRAINT_VAULT_PATHS,
    TERMINAL_STATUSES,
    TaskStatus,
)
from infrastructure.database import ProductDatabase, dumps


class TaskNotFoundError(LookupError):
    """统一 not-found：跨主体与不存在不可区分。"""


class InvalidTaskConstraints(ValueError):
    pass


class DuplicateTaskSubmission(RuntimeError):
    """同 principal 已存在相同 idempotency_key 的任务（幂等场景由调用方处理）。"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class TaskService:
    def __init__(self, database: ProductDatabase) -> None:
        self.database = database

    # ── 提交（幂等） ──

    def submit(
        self,
        *,
        principal_id: str,
        idempotency_key: str,
        task_type: str = "batch_policy_check",
        constraints: dict[str, Any],
        workspace: str | None = None,
        department: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        if task_type not in ("batch_policy_check", "directory_delta_sync"):
            raise InvalidTaskConstraints(f"unsupported task_type: {task_type}")
        if task_type == "directory_delta_sync":
            if not str(constraints.get("since") or "").strip():
                raise InvalidTaskConstraints("directory_delta_sync requires constraints.since (ISO timestamp)")
            clean = self._validate_constraints(constraints, require_target=False)
        else:
            clean = self._validate_constraints(constraints)
        existing = self.database.fetch_one(
            "SELECT task_id, status FROM agent_tasks WHERE principal_id=? AND idempotency_key=?",
            (principal_id, idempotency_key),
        )
        if existing is not None:
            # 幂等：返回原任务（含终态——重复提交不复活已完成任务）
            return self._public(self._get_row(existing["task_id"]))
        task_id = f"task-{uuid.uuid4().hex[:16]}"
        now = _now_iso()
        self.database.execute(
            "INSERT INTO agent_tasks (task_id, principal_id, workspace, department, conversation_id,"
            " task_type, constraints_json, status, idempotency_key, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?, 'queued', ?, ?, ?)",
            (task_id, principal_id, workspace, department, conversation_id,
             task_type, dumps(clean), idempotency_key, now, now),
        )
        return self._public(self._get_row(task_id))

    def _validate_constraints(self, constraints: dict[str, Any], *, require_target: bool = True) -> dict[str, Any]:
        if not isinstance(constraints, dict):
            raise InvalidTaskConstraints("constraints must be an object")
        unknown = set(constraints) - ALLOWED_CONSTRAINT_KEYS
        if unknown:
            raise InvalidTaskConstraints(f"unknown constraint fields: {sorted(unknown)}")
        # 任务 A 必须有明确核对对象；任务 C 的对象是 since 时间轴（快照对比）
        if require_target and not ("document_query" in constraints or "vault_paths" in constraints):
            raise InvalidTaskConstraints("document_query (or vault_paths) is required")
        clean: dict[str, Any] = {}
        if "document_query" in constraints:
            query = str(constraints["document_query"] or "").strip()
            if not query or len(query) > 500:
                raise InvalidTaskConstraints("document_query: 1-500 chars required")
            clean["document_query"] = query
        if "as_of" in constraints:
            as_of = str(constraints["as_of"] or "").strip()
            try:
                datetime.fromisoformat(as_of)
            except ValueError as exc:
                raise InvalidTaskConstraints("as_of must be YYYY-MM-DD") from exc
            clean["as_of"] = as_of
        if "since" in constraints:
            # 任务 C（delta sync）的时间轴键：ISO 时间戳，提交期即校验
            since_value = str(constraints["since"] or "").strip()
            try:
                datetime.fromisoformat(since_value)
            except ValueError as exc:
                raise InvalidTaskConstraints("since must be an ISO timestamp") from exc
            clean["since"] = since_value
        if "top_k" in constraints:
            top_k = constraints["top_k"]
            if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= MAX_CONSTRAINT_TOP_K:
                raise InvalidTaskConstraints(f"top_k: 1-{MAX_CONSTRAINT_TOP_K}")
            clean["top_k"] = top_k
        if "include_historical" in constraints:
            if not isinstance(constraints["include_historical"], bool):
                raise InvalidTaskConstraints("include_historical must be boolean")
            clean["include_historical"] = constraints["include_historical"]
        if "vault_paths" in constraints:
            paths = constraints["vault_paths"]
            if not isinstance(paths, list) or not paths or len(paths) > MAX_CONSTRAINT_VAULT_PATHS:
                raise InvalidTaskConstraints(f"vault_paths: 1-{MAX_CONSTRAINT_VAULT_PATHS} items")
            clean["vault_paths"] = [str(p).strip() for p in paths if str(p).strip()]
            if not clean["vault_paths"]:
                raise InvalidTaskConstraints("vault_paths: non-empty paths required")
        return clean

    # ── 查询 ──

    def list_tasks(self, *, principal_id: str, cursor: str | None = None, limit: int = 50) -> dict[str, Any]:
        bound = max(1, min(int(limit), 100))
        if cursor:
            anchor = self._owned_row(cursor, principal_id)
            rows = self.database.fetch_all(
                "SELECT * FROM agent_tasks WHERE principal_id=?"
                " AND (updated_at < ? OR (updated_at = ? AND task_id < ?))"
                " ORDER BY updated_at DESC, task_id DESC LIMIT ?",
                (principal_id, anchor["updated_at"], anchor["updated_at"], cursor, bound + 1),
            )
        else:
            rows = self.database.fetch_all(
                "SELECT * FROM agent_tasks WHERE principal_id=? ORDER BY updated_at DESC, task_id DESC LIMIT ?",
                (principal_id, bound + 1),
            )
        has_more = len(rows) > bound
        page = rows[:bound]
        return {
            "items": [self._public(row) for row in page],
            "next_cursor": page[-1]["task_id"] if has_more and page else None,
        }

    def get_task(self, *, task_id: str, principal_id: str) -> dict[str, Any]:
        row = self._owned_row(task_id, principal_id)
        payload = self._public(row)
        payload["artifacts"] = self._artifacts(task_id, principal_id)
        return payload

    def _artifacts(self, task_id: str, principal_id: str) -> list[dict[str, Any]]:
        rows = self.database.fetch_all(
            "SELECT artifact_id, kind, title, visibility, checksum, created_at FROM artifacts"
            " WHERE task_id=? AND owner_principal_id=?",
            (task_id, principal_id),
        )
        return [
            {
                "artifact_id": row["artifact_id"],
                "kind": row["kind"],
                "title": row["title"],
                "visibility": row["visibility"],
                "checksum": row["checksum"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_artifact_content(self, *, artifact_id: str, principal_id: str) -> dict[str, Any]:
        row = self.database.fetch_one(
            "SELECT * FROM artifacts WHERE artifact_id=? AND owner_principal_id=?",
            (artifact_id, principal_id),
        )
        if row is None:
            raise TaskNotFoundError(artifact_id)
        return {
            "artifact_id": row["artifact_id"],
            "task_id": row["task_id"],
            "kind": row["kind"],
            "title": row["title"],
            "content": json.loads(row["content_json"] or "{}"),
            "evidence_snapshot": json.loads(row["evidence_snapshot_json"] or "[]"),
            "citations": json.loads(row["citations_json"] or "[]"),
            "checksum": row["checksum"],
            "created_at": row["created_at"],
        }

    # ── 取消（协作式） ──

    def cancel_task(self, *, task_id: str, principal_id: str) -> dict[str, Any]:
        row = self._owned_row(task_id, principal_id)
        if row["status"] in TERMINAL_STATUSES:
            # 终态取消 = no-op（幂等），返回当前状态
            return self._public(row)
        self.database.execute(
            "UPDATE agent_tasks SET cancel_requested_at=?, updated_at=? WHERE task_id=?",
            (_now_iso(), _now_iso(), task_id),
        )
        return self._public(self._get_row(task_id))

    # ── 内部 ──

    def _get_row(self, task_id: str) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM agent_tasks WHERE task_id=?", (task_id,))
        if row is None:
            raise TaskNotFoundError(task_id)
        return row

    def _owned_row(self, task_id: str, principal_id: str) -> dict[str, Any]:
        row = self.database.fetch_one(
            "SELECT * FROM agent_tasks WHERE task_id=? AND principal_id=?",
            (task_id, principal_id),
        )
        if row is None:
            raise TaskNotFoundError(task_id)
        return row

    @staticmethod
    def _public(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": row["task_id"],
            "task_type": row["task_type"],
            "status": row["status"],
            "result_state": row["result_state"],
            "constraints": json.loads(row["constraints_json"] or "{}"),
            "attempt_count": row["attempt_count"],
            "cancel_requested": bool(row["cancel_requested_at"]),
            "error_code": row["error_code"],
            "error_message": row["error_message"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "workspace": row["workspace"],
            "department": row["department"],
        }
