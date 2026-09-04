"""Agent 任务域模型（M4-A，ADR-004）。

task_type 两个（ADR-004）：batch_policy_check（批量制度核对→证据包）与
directory_delta_sync（任务 C：增量对比→版本变化摘要 artifact，constraints.since 必填）。

状态机：queued → running → completed | completed_with_conflicts |
completed_empty | failed | cancelled。取消是协作式（cancel_requested_at），
已启动步骤跑完；恢复时同样生效。
"""

from __future__ import annotations

from enum import Enum


class TaskStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    completed_with_conflicts = "completed_with_conflicts"
    completed_empty = "completed_empty"
    failed = "failed"
    cancelled = "cancelled"


TERMINAL_STATUSES = frozenset(
    {item.value for item in TaskStatus if item.value not in {"queued", "running"}}
)

# 提交时可指定的约束白名单（结构化，非自由文本 prompt——威胁模型要求）
ALLOWED_CONSTRAINT_KEYS = frozenset({"top_k", "include_historical", "as_of", "document_query", "vault_paths", "since"})
MAX_CONSTRAINT_TOP_K = 50
MAX_CONSTRAINT_VAULT_PATHS = 20
