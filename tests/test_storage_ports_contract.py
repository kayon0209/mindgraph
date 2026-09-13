"""契约测试的内存替身：与 SqliteTaskQueue 语义一致的纯内存 TaskQueue。

价值：契约测试同时跑「SQLite 真实现」与「内存替身」，语义一致才能证明
Protocol 边界真的可替换（后续 PostgreSQL/外部队列同理——先在替身上
验证语义，再接真实后端）。生产代码不得 import 本模块。
"""

from __future__ import annotations

import time
from typing import Any
import uuid


class InMemoryTaskQueue:
    """语义对齐 SqliteTaskQueue 的最小内存实现。"""

    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, Any]] = {}
        self._idempotency: dict[tuple[str, str], str] = {}

    def submit(
        self,
        *,
        principal_id: str,
        idempotency_key: str,
        task_type: str,
        constraints: dict[str, Any],
        workspace: str | None = None,
        department: str | None = None,
        conversation_id: str | None = None,
    ) -> str:
        key = (principal_id, idempotency_key)
        if key in self._idempotency:
            return self._idempotency[key]
        task_id = f"task-{uuid.uuid4().hex[:16]}"
        self._idempotency[key] = task_id
        self._tasks[task_id] = {
            "task_id": task_id, "principal_id": principal_id,
            "task_type": task_type, "constraints": dict(constraints),
            "status": "queued", "lease_owner": None, "lease_expires_at": None,
            "attempt_count": 0, "error_message": None, "result_state": None,
        }
        return task_id

    def claim_next(self, *, lease_seconds: float, owner: str) -> dict[str, Any] | None:
        now = time.time()
        for task in self._tasks.values():
            expired = (
                task["status"] == "running"
                and task["lease_expires_at"] is not None
                and task["lease_expires_at"] < now
            )
            if task["status"] == "queued" or expired:
                task["status"] = "running"
                task["lease_owner"] = owner
                task["lease_expires_at"] = now + lease_seconds
                task["attempt_count"] = task["attempt_count"] + 1
                return dict(task)
        return None

    def renew_lease(self, task_id: str, *, owner: str, lease_seconds: float) -> bool:
        task = self._tasks.get(task_id)
        if task is None or task["status"] != "running" or task["lease_owner"] != owner:
            return False
        task["lease_expires_at"] = time.time() + lease_seconds
        return True

    def complete(self, task_id: str, *, result: dict[str, Any]) -> None:
        task = self._tasks.get(task_id)
        if task is not None and task["status"] == "running":
            task["status"] = "completed"
            task["result_state"] = "completed"
            task["lease_owner"] = None
            task["lease_expires_at"] = None

    def fail(self, task_id: str, *, reason: str) -> None:
        task = self._tasks.get(task_id)
        if task is not None:
            task["error_message"] = reason[:500]
            task["attempt_count"] = task["attempt_count"] + 1
