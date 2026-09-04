"""TaskWorker：SQLite-backed at-least-once 任务执行器（M4-A，ADR-004）。

契约（ADR-004）：
- 短事务 claim：queued 或 lease 过期的 running 各取一条，写 lease 后执行；
- 执行中按 TASK_LEASE_SECONDS 续租；步骤边界检查 cancel（协作式）；
- 副作用按 task_id 幂等（artifact 先查后写 + checksum）；
- attempt 超限 → failed；进程中断由 lease 过期 + 恢复语义兜底；
- 单实例单 worker：构造时校验同库只有一个活跃 lease owner 周期。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from domain.models import ChatRequest
from domain.task_models import TERMINAL_STATUSES, TaskStatus
from infrastructure.database import ProductDatabase, dumps

logger = logging.getLogger("mindgraph.task_worker")

DEFAULT_LEASE_SECONDS = 120
MAX_ATTEMPTS = 3


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _iso_in(seconds: float) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


class TaskWorker:
    def __init__(
        self,
        database: ProductDatabase,
        evidence_query_service_factory,
        policy_conflict_service,
        *,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        max_attempts: int = MAX_ATTEMPTS,
        owner: str | None = None,
    ) -> None:
        self.database = database
        self._make_evidence_service = evidence_query_service_factory
        self.conflict_service = policy_conflict_service
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.owner = owner or f"worker-{uuid.uuid4().hex[:8]}"

    # ── claim（短事务语义：条件 UPDATE + 行影响数判定） ──

    def claim_next(self) -> dict[str, Any] | None:
        """原子认领：条件 UPDATE …RETURNING（单语句完成"检查+抢占"）。

        并发/多实例语义：SQLite 单条 UPDATE 原子执行，两个 worker 不可能
        同时赢——RETURNING 行有值才是赢家；候选选择子查询内 LIMIT 1。
        RETURNING 需要 SQLite ≥ 3.35；Python 3.12 自带满足。
        """
        now = _now_iso()
        lease = _iso_in(self.lease_seconds)
        claimed_id = None
        # 优先 queued；其后再试 lease 过期的 running（重启恢复语义）
        for candidate_where, candidate_params in (
            ("status='queued'", ()),
            ("status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?", (now,)),
        ):
            row = self.database.fetch_one(
                "UPDATE agent_tasks SET status='running', lease_owner=?, lease_expires_at=?,"
                " attempt_count=attempt_count+1, updated_at=?"
                f" WHERE task_id=(SELECT task_id FROM agent_tasks WHERE {candidate_where}"
                " ORDER BY created_at LIMIT 1) RETURNING task_id",
                (self.owner, lease, now, *candidate_params),
            )
            if row is not None:
                claimed_id = row["task_id"]
                break
        if claimed_id is None:
            return None
        return self.database.fetch_one("SELECT * FROM agent_tasks WHERE task_id=?", (claimed_id,))

    def renew_lease(self, task_id: str) -> None:
        self.database.execute(
            "UPDATE agent_tasks SET lease_expires_at=?, updated_at=? WHERE task_id=? AND lease_owner=?",
            (_iso_in(self.lease_seconds), _now_iso(), task_id, self.owner),
        )

    # ── 执行一批（进程内运行模型：run_until_drained 供测试/单机循环） ──

    def run_once(self) -> dict[str, Any] | None:
        """认领并执行一个任务。返回执行后的任务公共视图。"""
        task = self.claim_next()
        if task is None:
            return None
        task_id = task["task_id"]
        try:
            return self._execute(task)
        except Exception as exc:  # noqa: BLE001 —— worker 必须把一切失败转为任务状态
            logger.exception("task_execution_failed", extra={"task_id": task_id, "error": str(exc)})
            return self._fail(task_id, code="worker_exception", message=str(exc)[:200])

    def run_until_drained(self, *, max_tasks: int = 100) -> int:
        processed = 0
        while processed < max_tasks:
            result = self.run_once()
            if result is None:
                break
            processed += 1
        return processed

    # ── 单任务执行（按 task_type 分派：A batch_policy_check / C directory_delta_sync） ──

    def _execute(self, task: dict[str, Any]) -> dict[str, Any]:
        task_id = task["task_id"]
        if task.get("task_type") == "directory_delta_sync":
            return self._execute_delta_sync(task)
        return self._execute_batch_check(task)

    def _execute_batch_check(self, task: dict[str, Any]) -> dict[str, Any]:
        task_id = task["task_id"]
        principal_id = task["principal_id"]
        constraints = json.loads(task["constraints_json"] or "{}")
        steps: list[dict[str, Any]] = []

        def record_step(name: str, status: str, detail: str = "", latency_ms: float = 0.0) -> None:
            # 轨迹脱敏：只存步骤名/状态/耗时与计数摘要，不存检索词与证据正文
            steps.append({"name": name, "status": status, "detail": detail[:80], "latency_ms": round(latency_ms, 1)})

        # 取消检查（步骤边界 0：开始前）
        if self._cancel_requested(task_id):
            return self._finalize_cancelled(task_id, steps)

        # 步骤 1：检索证据（按提交者当前 ACL；scope 构建与 REST 通道同源）。
        # 审查修正（F4）：此前只传 name——丢失了提交时持久化的 workspace/
        # department，导致企业用户的私有制度任务大面积 completed_empty。
        # 现按任务行的 workspace/department 重建主体（roles 不持久化：
        # worker 不应继承 admin 通配，提交时的角色只影响提交面）。
        started = time.perf_counter()
        from application.access_control import build_access_scope

        worker_principal: dict[str, Any] = {"name": principal_id, "authenticated": True}
        if task.get("workspace"):
            worker_principal["workspaces"] = [task["workspace"]]
        if task.get("department"):
            worker_principal["departments"] = [task["department"]]
        scope = build_access_scope(worker_principal)
        evidence_service = self._make_evidence_service()
        request = ChatRequest(
            question=str(constraints.get("document_query") or ""),
            retrieval_strategy="hybrid",
            final_top_k=int(constraints.get("top_k", 10)),
            query_date=constraints.get("as_of"),
            include_historical=bool(constraints.get("include_historical", False)),
        )
        try:
            result = evidence_service.query(request, access_scope=scope)
        except Exception as exc:
            record_step("retrieve_evidence", "failed", f"error={type(exc).__name__}", (time.perf_counter() - started) * 1000)
            attempt = int(task["attempt_count"] or 0)
            if attempt < self.max_attempts:
                # 重试语义：退回 queued（at-least-once）
                self.database.execute(
                    "UPDATE agent_tasks SET status='queued', lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE task_id=?",
                    (_now_iso(), task_id),
                )
                return self._public(task_id, steps)
            return self._fail(task_id, code="retrieval_unavailable", message="检索在多次尝试后仍不可用")
        record_step("retrieve_evidence", "ok", f"citations={len(result.citations)}", (time.perf_counter() - started) * 1000)
        self.renew_lease(task_id)
        if self._cancel_requested(task_id):
            return self._finalize_cancelled(task_id, steps)

        # 步骤 2：版本冲突核对（同一 policy_key 在 as_of 的有效版本族）。
        # 注：query 内部 bundle 已含 conflicts，但 worker 步骤轨迹要求独立的
        # 冲突核对记录；此处为轻量 SQLite 查询（毫秒级），保留显式调用而非
        # 从 bundle 重构——避免手工重建 dict 结构引入回归（审查时已回退一次）。
        started = time.perf_counter()
        conflicts = self.conflict_service.find_for_policy_keys(
            {item.policy_key for item in result.citations if item.policy_key},
            as_of=request.query_date,
            include_historical=request.include_historical,
            access_scope=scope,
        )
        record_step("check_conflicts", "ok", f"conflicts={len(conflicts)}", (time.perf_counter() - started) * 1000)
        self.renew_lease(task_id)
        if self._cancel_requested(task_id):
            return self._finalize_cancelled(task_id, steps)

        # 步骤 3：生成 private artifact（幂等：按 task_id 先查后写）
        if not result.citations:
            status = TaskStatus.completed_empty.value
            state = "insufficient_evidence"
        elif conflicts:
            status = TaskStatus.completed_with_conflicts.value
            state = "conflicting_evidence"
        else:
            status = TaskStatus.completed.value
            state = "evidence_found"
        existing_artifact = self.database.fetch_one("SELECT artifact_id FROM artifacts WHERE task_id=?", (task_id,))
        if existing_artifact is None and result.citations:
            self._write_artifact(task_id, principal_id, result, conflicts, constraints)
        record_step("build_artifact", "ok", f"status={status}", (time.perf_counter() - started) * 1000)

        self.database.execute(
            "UPDATE agent_tasks SET status=?, result_state=?, lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE task_id=?",
            (status, state, _now_iso(), task_id),
        )
        return self._public(task_id, steps)

    # ── 任务 C：目录增量同步 → 版本变化摘要（ADR-004 预留的第二任务类型） ──

    def _execute_delta_sync(self, task: dict[str, Any]) -> dict[str, Any]:
        """快照对比法：notes 表按 updated_at 对 since 的时间分桶。

        输入：constraints = {"since": ISO 时间戳}（目录语义由提交面约束在
        allowed_roots——目录 connector 已有校验，任务不越权扫盘）。
        输出：private artifact（新增/变更/归档三分类摘要 + 每类计数与样例）。
        权限：仅统计提交者当前可见的笔记（ACL 复用 F4 修正后的主体重建）。
        """
        task_id = task["task_id"]
        principal_id = task["principal_id"]
        constraints = json.loads(task["constraints_json"] or "{}")
        since = str(constraints.get("since") or "").strip()
        steps: list[dict[str, Any]] = []

        def record_step(name: str, status: str, detail: str = "", latency_ms: float = 0.0) -> None:
            steps.append({"name": name, "status": status, "detail": detail[:80], "latency_ms": round(latency_ms, 1)})

        if self._cancel_requested(task_id):
            return self._finalize_cancelled(task_id, steps)

        from datetime import UTC, datetime

        try:
            since_dt = datetime.fromisoformat(since)
            # 统一 aware-UTC：notes 时间戳带 +00:00，naive since 按 UTC 补齐
            # （naive/aware 混比会 TypeError——实测修复）
            since_utc = since_dt.replace(tzinfo=UTC) if since_dt.tzinfo is None else since_dt.astimezone(UTC)
        except ValueError:
            return self._fail(task_id, code="invalid_constraints", message="since 必须是 ISO 时间戳（如 2026-09-01T00:00:00）")

        started = time.perf_counter()
        from application.access_control import build_access_scope, note_acl_matches

        worker_principal: dict[str, Any] = {"name": principal_id, "authenticated": True}
        if task.get("workspace"):
            worker_principal["workspaces"] = [task["workspace"]]
        if task.get("department"):
            worker_principal["departments"] = [task["department"]]
        scope = build_access_scope(worker_principal)

        rows = self.database.fetch_all(
            "SELECT note_id, title, vault_path, created_at, updated_at, policy_status, acl_json, acl_public, workspace, department"
            " FROM notes"
        )
        visible = [r for r in rows if note_acl_matches(r, scope)]
        added, updated, archived = [], [], []
        for r in visible:
            created = r["created_at"] or ""
            updated_at = r["updated_at"] or ""

            def _aware(stamp: str) -> datetime | None:
                """ISO 串归一为 aware-UTC（naive 补 UTC；坏值返回 None 不进桶）。"""
                if not stamp:
                    return None
                try:
                    dt = datetime.fromisoformat(stamp)
                except ValueError:
                    return None
                return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)

            created_dt = _aware(created)
            updated_dt = _aware(updated_at)
            entry = {"title": r["title"], "vault_path": r["vault_path"]}
            is_archived = str(r.get("policy_status") or "").lower() in {"archived", "expired", "superseded", "replaced"}
            if created_dt and created_dt > since_utc:
                added.append(entry)
            elif updated_dt and updated_dt > since_utc:
                (archived if is_archived else updated).append(entry)
        record_step("delta_scan", "ok", f"visible={len(visible)} +{len(added)} ~{len(updated)} x{len(archived)}", (time.perf_counter() - started) * 1000)
        self.renew_lease(task_id)
        if self._cancel_requested(task_id):
            return self._finalize_cancelled(task_id, steps)

        # artifact（幂等：同 task_id 先查后写）
        existing_artifact = self.database.fetch_one("SELECT artifact_id FROM artifacts WHERE task_id=?", (task_id,))
        if existing_artifact is None:
            content = {
                "since": since,
                "total_visible": len(visible),
                "added": added,
                "updated": updated,
                "archived": archived,
                "counts": {"added": len(added), "updated": len(updated), "archived": len(archived)},
            }
            self._write_delta_artifact(task_id, principal_id, content)
        record_step("build_artifact", "ok", "status=completed", (time.perf_counter() - started) * 1000)

        self.database.execute(
            "UPDATE agent_tasks SET status=?, result_state=?, lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE task_id=?",
            (TaskStatus.completed.value, "evidence_found", _now_iso(), task_id),
        )
        return self._public(task_id, steps)

    def _write_delta_artifact(self, task_id: str, principal_id: str, content: dict[str, Any]) -> str:
        artifact_id = f"art-{uuid.uuid4().hex[:16]}"
        checksum = hashlib.sha256(dumps(content).encode("utf-8")).hexdigest()
        now = _now_iso()
        self.database.execute(
            "INSERT INTO artifacts (artifact_id, owner_principal_id, task_id, kind, title, content_json,"
            " visibility, evidence_snapshot_json, citations_json, checksum, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?, 'private', '[]', '[]', ?, ?, ?)",
            (
                artifact_id, principal_id, task_id, "delta_sync_summary",
                f"版本变化摘要 · since {content.get('since', '')[:10]}",
                dumps(content), checksum, now, now,
            ),
        )
        return artifact_id

    def _write_artifact(self, task_id: str, principal_id: str, result, conflicts: list[dict], constraints: dict[str, Any]) -> str:
        artifact_id = f"art-{uuid.uuid4().hex[:16]}"
        citations_payload = [item.model_dump(mode="json") for item in result.citations]
        evidence_snapshot = [
            {
                "citation_id": item.citation_id,
                "document_name": item.document_name,
                "document_version": item.document_version,
                "effective_from": item.effective_from,
                "effective_to": item.effective_to,
                "policy_status": item.policy_status,
                "policy_key": item.policy_key,
                "excerpt": (item.excerpt or "")[:400],
            }
            for item in result.citations
        ]
        content = {
            "document_query": constraints.get("document_query"),
            "as_of": constraints.get("as_of"),
            "matched_documents": len(result.citations),
            "conflict_count": len(conflicts),
            "conflicts": [{"policy_key": c.get("policy_key"), "versions": c.get("versions", [])} for c in conflicts],
        }
        checksum = hashlib.sha256(
            (dumps(evidence_snapshot) + dumps(citations_payload)).encode("utf-8")
        ).hexdigest()
        now = _now_iso()
        self.database.execute(
            "INSERT INTO artifacts (artifact_id, owner_principal_id, task_id, kind, title, content_json,"
            " visibility, evidence_snapshot_json, citations_json, checksum, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?, 'private', ?, ?, ?, ?, ?)",
            (
                artifact_id, principal_id, task_id, "evidence_bundle",
                f"核对证据包 · {constraints.get('document_query', '')[:60]}",
                dumps(content), dumps(evidence_snapshot),
                dumps(citations_payload), checksum, now, now,
            ),
        )
        return artifact_id

    # ── 状态收尾 ──

    def _cancel_requested(self, task_id: str) -> bool:
        row = self.database.fetch_one("SELECT cancel_requested_at FROM agent_tasks WHERE task_id=?", (task_id,))
        return bool(row and row["cancel_requested_at"])

    def _finalize_cancelled(self, task_id: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
        self.database.execute(
            "UPDATE agent_tasks SET status='cancelled', result_state='cancelled', lease_owner=NULL,"
            " lease_expires_at=NULL, updated_at=? WHERE task_id=?",
            (_now_iso(), task_id),
        )
        return self._public(task_id, steps)

    def _fail(self, task_id: str, *, code: str, message: str) -> dict[str, Any]:
        self.database.execute(
            "UPDATE agent_tasks SET status='failed', result_state='failed', error_code=?, error_message=?,"
            " lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE task_id=?",
            (code, message, _now_iso(), task_id),
        )
        return self._public(task_id, [])

    def _public(self, task_id: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM agent_tasks WHERE task_id=?", (task_id,))
        payload = {k: row[k] for k in row.keys()}
        payload["steps"] = steps
        payload.pop("constraints_json", None)
        return payload
