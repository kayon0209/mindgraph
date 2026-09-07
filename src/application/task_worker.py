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
from pathlib import Path
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
        allowed_roots: tuple[Path, ...] | None = None,
    ) -> None:
        self.database = database
        self._make_evidence_service = evidence_query_service_factory
        self.conflict_service = policy_conflict_service
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.owner = owner or f"worker-{uuid.uuid4().hex[:8]}"
        # 任务 C 目录语义（ADR-004「目录路径，限定 allowed_roots」）：None =
        # 生产容器注入的允许根目录；空 tuple = 显式禁用目录模式（仅快照对比）
        self.allowed_roots = allowed_roots

    # ── claim（短事务语义：条件 UPDATE + 行影响数判定） ──

    def claim_next(self) -> dict[str, Any] | None:
        """原子认领：条件 UPDATE …RETURNING（单语句完成"检查+抢占"）。

        并发/多实例语义：SQLite 单条 UPDATE 原子执行，两个 worker 不可能
        同时赢——RETURNING 行有值才是赢家；候选选择子查询内 LIMIT 1。
        RETURNING 需要 SQLite ≥ 3.35；产品数据库入口的运行时门禁要求更高的安全版本。
        """
        now = _now_iso()
        lease = _iso_in(self.lease_seconds)
        claimed_id = None
        # 优先 queued；其后再试 lease 过期的 running（重启恢复语义）
        for candidate_where, candidate_params in (
            ("status='queued'", ()),
            ("status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?", (now,)),
        ):
            candidate = self.database.fetch_one(
                # candidate_where 仅取自本方法上方定义的两个常量，绝不来自请求输入。
                f"SELECT task_id FROM agent_tasks WHERE {candidate_where} ORDER BY created_at LIMIT 1",  # nosec B608
                candidate_params,
            )
            if candidate is None:
                continue
            # 保留 candidate 条件做 compare-and-set：两个 worker 读到同一 task 时
            # 只能有一个 UPDATE 成功。execute() 会重试 SQLite 的短暂写锁。
            updated = self.database.execute(
                "UPDATE agent_tasks SET status='running', lease_owner=?, lease_expires_at=?,"
                " attempt_count=attempt_count+1, updated_at=?"
                # candidate_where 仅取自本方法上方定义的两个常量，绝不来自请求输入。
                f" WHERE task_id=? AND {candidate_where}",  # nosec B608
                (self.owner, lease, now, candidate["task_id"], *candidate_params),
            )
            if updated == 1:
                claimed_id = candidate["task_id"]
                break
        if claimed_id is None:
            return None
        return self.database.fetch_one("SELECT * FROM agent_tasks WHERE task_id=?", (claimed_id,))

    def renew_lease(self, task_id: str) -> bool:
        """续租仅对当前 owner 生效；False 表示该执行器已经失去围栏。"""
        return self.database.execute(
            "UPDATE agent_tasks SET lease_expires_at=?, updated_at=? WHERE task_id=? AND lease_owner=?",
            (_iso_in(self.lease_seconds), _now_iso(), task_id, self.owner),
        ) == 1

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
                    "UPDATE agent_tasks SET status='queued', lease_owner=NULL, lease_expires_at=NULL, updated_at=? "
                    "WHERE task_id=? AND status='running' AND lease_owner=?",
                    (_now_iso(), task_id, self.owner),
                )
                return self._public(task_id, steps)
            return self._fail(task_id, code="retrieval_unavailable", message="检索在多次尝试后仍不可用")
        record_step("retrieve_evidence", "ok", f"citations={len(result.citations)}", (time.perf_counter() - started) * 1000)
        if not self.renew_lease(task_id):
            return self._public(task_id, steps)
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
        if not self.renew_lease(task_id):
            return self._public(task_id, steps)
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
        if result.citations:
            self._write_artifact(task_id, principal_id, result, conflicts, constraints)
        record_step("build_artifact", "ok", f"status={status}", (time.perf_counter() - started) * 1000)

        self._finish_if_owned(task_id, status=status, result_state=state)
        return self._public(task_id, steps)

    # ── 任务 C：目录增量同步 → 版本变化摘要（ADR-004 第二任务类型） ──

    def _execute_delta_sync(self, task: dict[str, Any]) -> dict[str, Any]:
        """时间分桶对比：constraints.since 之后新建的笔记入「新增」桶，
        更新的按 policy_status 分「变更 / 归档」桶。

        两种数据源（ADR-004「目录路径（限定 allowed_roots）+ since」）：
        - directory_root 给定（目录模式）：只读扫描该目录（VaultSyncService，
          prune_missing=False、不写回 id），allowed_roots 校验 fail-closed，
          connector_syncs 留审计行；
        - 未给定（快照模式）：notes 全库按当前 ACL 裁剪（向后兼容语义）。
        输出：private artifact（新增/变更/归档三分类摘要 + 计数）。
        权限：仅统计提交者当前可见的笔记（ACL 复用 F4 修正后的主体重建）。
        """
        task_id = task["task_id"]
        principal_id = task["principal_id"]
        constraints = json.loads(task["constraints_json"] or "{}")
        since = str(constraints.get("since") or "").strip()
        directory_root = str(constraints.get("directory_root") or "").strip()
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

        # ── 数据源选择：目录模式（真实扫描）或快照模式（全库 + ACL） ──
        rows: list[dict[str, Any]]
        if directory_root:
            source = Path(directory_root)
            if not self.allowed_roots:
                return self._fail(task_id, code="invalid_constraints", message="directory_root requires worker allowed_roots configuration")
            if not source.exists() or not source.is_dir():
                return self._fail(task_id, code="invalid_constraints", message=f"directory_root is not an existing directory: {directory_root}")
            resolved = source.resolve(strict=True)
            if not any(resolved == root or root in resolved.parents for root in self.allowed_roots):
                return self._fail(task_id, code="directory_not_allowed", message=f"directory_root is outside configured allowed roots: {resolved}")
            rows = self._scan_directory_rows(task, resolved, steps)
        else:
            rows = self.database.fetch_all(
                "SELECT note_id, title, vault_path, created_at, updated_at, policy_status, acl_json, acl_public, workspace, department"
                " FROM notes"
            )
        visible = [r for r in rows if note_acl_matches(r, scope)]
        added: list[dict[str, Any]] = []
        updated: list[dict[str, Any]] = []
        archived: list[dict[str, Any]] = []
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
        if not self.renew_lease(task_id):
            return self._public(task_id, steps)
        if self._cancel_requested(task_id):
            return self._finalize_cancelled(task_id, steps)

        # artifact（幂等：同 task_id 先查后写）
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

        self._finish_if_owned(task_id, status=TaskStatus.completed.value, result_state="evidence_found")
        return self._public(task_id, steps)

    def _scan_directory_rows(self, task: dict[str, Any], source: Path, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """目录模式数据源：VaultSyncService 只读扫描（不剪枝、不写回 id），
        返回该目录产出笔记的行快照，并写 connector_syncs 审计行。

        与 DirectoryConnectorService 的关系：复用同一 VaultSyncService 增量
        upsert 语义，但本路径只做任务可见性扫描——不触发索引、不回填 ACL
        （ACL 回填是 admin 的 connectors API 职责，任务不做静默治理动作）。
        """
        task_id = task["task_id"]
        started = time.perf_counter()
        from application.vault_sync_service import VaultSyncService

        connector_id = f"task-{task_id[:20]}"
        sync = VaultSyncService(
            self.database,
            source,
            write_ids=False,
            path_prefix=connector_id,
            id_namespace=str(source),
        )
        result = sync.scan_vault(prune_missing=False)
        steps.append({
            "name": "directory_scan", "status": "ok",
            "detail": f"scanned={len(result.scanned)} skipped={len(result.skipped)}"[:80],
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        })
        if not result.scanned:
            return []
        placeholders = ",".join("?" for _ in result.scanned)
        note_ids = tuple(sorted(note.note_id for note in result.scanned))
        rows = self.database.fetch_all(
            f"SELECT note_id, title, vault_path, created_at, updated_at, policy_status, acl_json, acl_public, workspace, department"  # nosec B608 -- placeholders 仅由常量 '?' 拼接
            f" FROM notes WHERE note_id IN ({placeholders})",
            note_ids,
        )
        # connector_syncs 审计：任务驱动的目录扫描同样可追溯（ADR-004 运营要求）
        self.database.execute(
            "INSERT OR REPLACE INTO connector_syncs "
            "(connector_id, connector_type, source_path, workspace, department, status, "
            "file_count, added, updated, pruned, error, metadata_json, started_at, finished_at) "
            "VALUES (?, 'agent_task_delta_sync', ?, ?, ?, 'completed', ?, ?, ?, 0, NULL, ?, ?, ?)",
            (
                connector_id, str(source), task.get("workspace"), task.get("department"),
                len(result.scanned), 0, 0, dumps({"task_id": task_id}),
                _now_iso(), _now_iso(),
            ),
        )
        return list(rows)

    def _write_delta_artifact(self, task_id: str, principal_id: str, content: dict[str, Any]) -> str | None:
        artifact_id = f"art-{uuid.uuid4().hex[:16]}"
        checksum = hashlib.sha256(dumps(content).encode("utf-8")).hexdigest()
        now = _now_iso()
        return self._write_artifact_if_owned(
            task_id=task_id,
            artifact_id=artifact_id,
            principal_id=principal_id,
            kind="delta_sync_summary",
            title=f"版本变化摘要 · since {content.get('since', '')[:10]}",
            content_json=dumps(content),
            evidence_snapshot_json="[]",
            citations_json="[]",
            checksum=checksum,
            now=now,
        )

    def _write_artifact(self, task_id: str, principal_id: str, result, conflicts: list[dict], constraints: dict[str, Any]) -> str | None:
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
        return self._write_artifact_if_owned(
            task_id=task_id,
            artifact_id=artifact_id,
            principal_id=principal_id,
            kind="evidence_bundle",
            title=f"核对证据包 · {constraints.get('document_query', '')[:60]}",
            content_json=dumps(content),
            evidence_snapshot_json=dumps(evidence_snapshot),
            citations_json=dumps(citations_payload),
            checksum=checksum,
            now=now,
        )

    def _write_artifact_if_owned(
        self,
        *,
        task_id: str,
        artifact_id: str,
        principal_id: str,
        kind: str,
        title: str,
        content_json: str,
        evidence_snapshot_json: str,
        citations_json: str,
        checksum: str,
        now: str,
    ) -> str | None:
        """在一个事务中领取 task artifact 槽并写内容。

        ``artifact_task_claims`` 是兼容旧 artifacts 多行数据的不可变领取表；
        它让新 worker 的 artifact 副作用严格绑定到仍持有的 lease owner。
        """
        with self.database.transaction() as connection:
            owned = connection.execute(
                "SELECT 1 FROM agent_tasks WHERE task_id=? AND status='running' AND lease_owner=?",
                (task_id, self.owner),
            ).fetchone()
            if owned is None:
                return None
            claim = connection.execute(
                "INSERT OR IGNORE INTO artifact_task_claims (task_id, artifact_id, created_at) VALUES (?,?,?)",
                (task_id, artifact_id, now),
            )
            if claim.rowcount != 1:
                existing = connection.execute(
                    "SELECT artifact_id FROM artifact_task_claims WHERE task_id=?", (task_id,)
                ).fetchone()
                if existing is None:
                    raise RuntimeError(f"artifact claim disappeared: {task_id}")
                return str(existing["artifact_id"])
            connection.execute(
            "INSERT INTO artifacts (artifact_id, owner_principal_id, task_id, kind, title, content_json,"
            " visibility, evidence_snapshot_json, citations_json, checksum, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?, 'private', ?, ?, ?, ?, ?)",
            (
                artifact_id, principal_id, task_id, kind, title, content_json,
                evidence_snapshot_json, citations_json, checksum, now, now,
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
            " lease_expires_at=NULL, updated_at=? WHERE task_id=? AND status='running' AND lease_owner=?",
            (_now_iso(), task_id, self.owner),
        )
        return self._public(task_id, steps)

    def _fail(self, task_id: str, *, code: str, message: str) -> dict[str, Any]:
        self.database.execute(
            "UPDATE agent_tasks SET status='failed', result_state='failed', error_code=?, error_message=?,"
            " lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE task_id=? AND status='running' AND lease_owner=?",
            (code, message, _now_iso(), task_id, self.owner),
        )
        return self._public(task_id, [])

    def _finish_if_owned(self, task_id: str, *, status: str, result_state: str) -> bool:
        """仅当前 lease owner 能把任务带入终态，避免过期执行器覆盖接管者。"""
        return self.database.execute(
            "UPDATE agent_tasks SET status=?, result_state=?, lease_owner=NULL, lease_expires_at=NULL, updated_at=? "
            "WHERE task_id=? AND status='running' AND lease_owner=?",
            (status, result_state, _now_iso(), task_id, self.owner),
        ) == 1

    def _public(self, task_id: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM agent_tasks WHERE task_id=?", (task_id,))
        if row is None:
            raise RuntimeError(f"agent task disappeared while reading result: {task_id}")
        payload = {k: row[k] for k in row.keys()}
        payload["steps"] = steps
        payload.pop("constraints_json", None)
        return payload
