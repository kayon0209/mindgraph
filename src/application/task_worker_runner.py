"""TaskWorkerRunner：TASK_WORKER_ENABLED 的消费方（缺口修复，ADR-004 部署语义）。

API 进程 lifespan 拉起的单实例后台轮询线程：
- 每 TASK_POLL_INTERVAL_SECONDS 轮询 claim_next（空转休眠）；
- 单实例防重：同进程二次启动直接拒绝（模块级 guard）；SQLite 阶段
  明确不跨进程防重——多实例部署必须先升级持久层（ADR-004 硬约束）；
- daemon 线程 + stop event：进程退出不阻塞；每轮循环检查 stop；
- worker 的 lease/attempt 参数从 settings 注入（不再硬编码）。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger("mindgraph.task_runner")


class TaskWorkerRunner:
    def __init__(self, container: Any, *, poll_interval_seconds: float = 2.0, lease_seconds: float = 120.0, max_attempts: int = 3, retention_callback=None) -> None:
        from application.task_worker import TaskWorker

        self._container = container
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._poll_interval = max(0.2, float(poll_interval_seconds))
        # 保留期执行钩子（M3-E 缺口修复）：conversation_service 存在时由
        # maybe_start_task_worker 注入 enforce_retention；独立注入便于测试
        self._retention = retention_callback
        self._worker = TaskWorker(
            container.database,
            evidence_query_service_factory=lambda: _build_evidence_service(container),
            policy_conflict_service=container.task_worker.conflict_service,
            lease_seconds=lease_seconds,
            max_attempts=max_attempts,
            owner="in-process-runner",
        )

    @property
    def worker(self):
        return self._worker

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("task worker runner already running (single-instance constraint)")
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="task-worker", daemon=True)
        self._thread.start()
        logger.info("task_worker_started", extra={"poll_interval_s": self._poll_interval})

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("task_worker_stopped")

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                processed = 0
                # 每轮排空当前可认领任务（有任务时连续处理）
                while not self._stop.is_set():
                    result = self._worker.run_once()
                    if result is None:
                        break
                    processed += 1
                if processed:
                    logger.info("task_worker_batch", extra={"processed": processed})
                # 保留期执行（M3-E 缺口修复）：同线程顺带执行，到期会话归档
                if self._retention is not None:
                    self._retention()
            except Exception:
                logger.exception("task_worker_loop_failed")
            self._stop.wait(self._poll_interval)


def _build_evidence_service(container: Any):
    from application.evidence_query_service import EvidenceQueryService

    return EvidenceQueryService(container.mindgraph_chat)


def maybe_start_task_worker(container: Any) -> TaskWorkerRunner | None:
    """lifespan 入口：TASK_WORKER_ENABLED=false 返回 None（零行为变化）；
    开启则启动单实例轮询线程。容器需已装配 task_worker 与 mindgraph_chat。"""
    from infrastructure.settings import get_settings

    settings = get_settings()
    if not settings.TASK_WORKER_ENABLED:
        return None
    if not getattr(container, "task_worker", None) or not getattr(container, "mindgraph_chat", None):
        logger.warning("task_worker_runner_skipped", extra={"reason": "container missing task_worker/mindgraph_chat"})
        return None
    runner = TaskWorkerRunner(
        container,
        poll_interval_seconds=settings.TASK_POLL_INTERVAL_SECONDS,
        lease_seconds=settings.TASK_LEASE_SECONDS,
        max_attempts=settings.TASK_MAX_ATTEMPTS,
        retention_callback=(
            container.conversation_service.enforce_retention
            if getattr(container, "conversation_service", None) is not None
            else None
        ),
    )
    runner.start()
    return runner
