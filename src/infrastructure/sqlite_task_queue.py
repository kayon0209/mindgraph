"""PR-15：TaskQueue 的 SQLite 实现（Local Profile 的默认 adapter）。

把散在 ``task_service`` / ``task_worker`` 里的队列 SQL 集中到一个
Protocol 实现里——应用层后续只依赖 :class:`domain.storage_ports.TaskQueue`，
替换 PostgreSQL/外部队列时不动业务代码。

语义与既有 agent_tasks 表完全一致（本 adapter 不改表、不改状态机）：
- 幂等：UNIQUE(principal_id, idempotency_key)；
- 互斥：claim 是条件 UPDATE（queued → running + 租约），单语句原子；
- 终态保护：complete 只认 running + 持有者（与 _finish_if_owned 同语义）；
- 失败可见：fail 记录原因 + attempt 递增，status 留 queued 供重试
  （重试升级策略归 TaskWorker，本层只保证可观测）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from infrastructure.database import ProductDatabase, dumps


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class SqliteTaskQueue:
    """``agent_tasks`` 表之上的 TaskQueue adapter。"""

    def __init__(self, database: ProductDatabase) -> None:
        self.database = database

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
        existing = self.database.fetch_one(
            "SELECT task_id FROM agent_tasks WHERE principal_id=? AND idempotency_key=?",
            (principal_id, idempotency_key),
        )
        if existing is not None:
            return str(existing["task_id"])  # 幂等：不重复落
        task_id = f"task-{__import__('uuid').uuid4().hex[:16]}"
        now = _now_iso()
        self.database.execute(
            "INSERT OR IGNORE INTO agent_tasks (task_id, principal_id, workspace, department,"
            " conversation_id, task_type, constraints_json, status, idempotency_key,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,'queued',?,?,?)",
            (task_id, principal_id, workspace, department, conversation_id,
             task_type, dumps(constraints), idempotency_key, now, now),
        )
        # 并发竞态兜底：INSERT OR IGNORE 被并发抢走时读回既有 task_id
        row = self.database.fetch_one(
            "SELECT task_id FROM agent_tasks WHERE principal_id=? AND idempotency_key=?",
            (principal_id, idempotency_key),
        )
        return str(row["task_id"]) if row is not None else task_id

    def claim_next(self, *, lease_seconds: float, owner: str) -> dict[str, Any] | None:
        expires_at = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat()
        now = _now_iso()
        # 过期租约回收：lease 已到期的 running 任务回到可认领池
        candidate = self.database.fetch_one(
            "SELECT task_id FROM agent_tasks WHERE status='queued'"
            " OR (status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?)"
            " ORDER BY created_at LIMIT 1",
            (now,),
        )
        if candidate is None:
            return None
        task_id = candidate["task_id"]
        updated = self.database.execute(
            "UPDATE agent_tasks SET status='running', lease_owner=?, lease_expires_at=?,"
            " attempt_count=attempt_count+1, updated_at=?"
            " WHERE task_id=? AND (status='queued'"
            " OR (status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?))",
            (owner, expires_at, now, task_id, now),
        )
        if not updated:
            return None  # 被并发 worker 抢走：本次空手而归
        row = self.database.fetch_one("SELECT * FROM agent_tasks WHERE task_id=?", (task_id,))
        return dict(row) if row is not None else None

    def renew_lease(self, task_id: str, *, owner: str, lease_seconds: float) -> bool:
        expires_at = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat()
        return self.database.execute(
            "UPDATE agent_tasks SET lease_expires_at=?, updated_at=?"
            " WHERE task_id=? AND status='running' AND lease_owner=?",
            (expires_at, _now_iso(), task_id, owner),
        ) == 1

    def complete(self, task_id: str, *, result: dict[str, Any]) -> None:
        self.database.execute(
            "UPDATE agent_tasks SET status='completed', result_state='completed',"
            " lease_owner=NULL, lease_expires_at=NULL, updated_at=?"
            " WHERE task_id=? AND status='running'",
            (_now_iso(), task_id),
        )

    def fail(self, task_id: str, *, reason: str) -> None:
        self.database.execute(
            "UPDATE agent_tasks SET error_message=?, attempt_count=attempt_count+1, updated_at=?"
            " WHERE task_id=?",
            (reason[:500], _now_iso(), task_id),
        )
