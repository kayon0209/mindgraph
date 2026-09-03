"""TaskWorkerRunner 测试：TASK_WORKER_ENABLED 消费方（缺口修复）。

覆盖：
- flag 关闭：maybe_start_task_worker 返回 None（零行为）；
- flag 开启：后台线程认领并完成 queued 任务（端到端线程验证）；
- 单实例防重：重复 start 抛错；
- stop 后线程退出且可重启。
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from application.chat_service import ChatService
from application.evidence_query_service import EvidenceQueryService
from application.policy_conflict_service import PolicyConflictService
from application.task_service import TaskService
from application.task_worker import TaskWorker
from application.task_worker_runner import TaskWorkerRunner, maybe_start_task_worker
from infrastructure.database import ProductDatabase
from infrastructure.settings import get_settings
from retrieval.types import Chunk, RetrievalCandidate, RetrievalTrace


class FakeProvider:
    provider_name = "fake"
    model_name = "fake-model"
    available = True

    def complete(self, _m):
        return ("ok", {"total_tokens": 1})

    def stream(self, _m):
        yield {"delta": "ok"}


class StubPipeline:
    def retrieve(self, *_a, **_k):
        chunk = Chunk(
            "p.md::0", "报销应在 30 日内提交。", "p.md", 0, "时限",
            {"document_title": "费用报销管理办法", "vault_path": "policies/expense.md",
             "document_version": "v2", "effective_from": "2026-01-01", "policy_key": "expense.general",
             "policy_status": "active", "owner": "财务部"},
        )
        return RetrievalTrace(
            query="q", requested_strategy="hybrid", actual_strategy="hybrid",
            candidate_counts={"final": 1},
            final_selected_chunks=[RetrievalCandidate(chunk=chunk, final_rank=1, dense_score=0.9)],
            latency_ms={"total_retrieval_ms": 0.5}, index_version="idx", applied_filters={}, warnings=[],
        )


def _container(tmp_path: Path):
    database = ProductDatabase(tmp_path / "runner.sqlite3")
    database.initialize()
    chat = ChatService(database, lambda top_k: StubPipeline(), FakeProvider(), privacy_log_questions=False)
    worker = TaskWorker(
        database,
        evidence_query_service_factory=lambda: EvidenceQueryService(chat),
        policy_conflict_service=PolicyConflictService(database),
    )
    container = SimpleNamespace(database=database, mindgraph_chat=chat, task_worker=worker)
    return container, TaskService(database)


def test_flag_off_returns_none(tmp_path: Path, monkeypatch):
    _container_local = None  # noqa: F841
    monkeypatch.delenv("TASK_WORKER_ENABLED", raising=False)
    import os

    os.environ["TASK_WORKER_ENABLED"] = "false"
    get_settings.cache_clear()
    try:
        container, _svc = _container(tmp_path)
        assert maybe_start_task_worker(container) is None
    finally:
        os.environ.pop("TASK_WORKER_ENABLED", None)
        get_settings.cache_clear()


def test_runner_processes_queued_task(tmp_path: Path, monkeypatch):
    """端到端：runner 线程把 queued 任务跑到终态（含 artifact）。"""
    import os

    monkeypatch.setenv("TASK_WORKER_ENABLED", "true")
    get_settings.cache_clear()
    try:
        container, service = _container(tmp_path)
        service.submit(
            principal_id="user-a", idempotency_key="runner-0001",
            constraints={"document_query": "费用报销核对", "top_k": 5},
        )
        runner = TaskWorkerRunner(container, poll_interval_seconds=0.1, lease_seconds=30.0)
        runner.start()
        try:
            deadline = time.monotonic() + 10
            status = None
            while time.monotonic() < deadline:
                tasks = service.list_tasks(principal_id="user-a")["items"]
                status = tasks[0]["status"]
                if status != "queued" and status != "running":
                    break
                time.sleep(0.1)
            assert status == "completed", f"unexpected status: {status}"
            detail = service.get_task(task_id=tasks[0]["task_id"], principal_id="user-a")
            assert len(detail["artifacts"]) == 1
        finally:
            runner.stop()
    finally:
        monkeypatch.delenv("TASK_WORKER_ENABLED", raising=False)
        get_settings.cache_clear()


def test_single_instance_guard(tmp_path: Path):
    container, _svc = _container(tmp_path)
    runner = TaskWorkerRunner(container, poll_interval_seconds=0.1)
    runner.start()
    try:
        try:
            runner.start()
            raise AssertionError("double start must be rejected")
        except RuntimeError:
            pass
    finally:
        runner.stop()


def test_stop_then_restart(tmp_path: Path):
    container, _svc = _container(tmp_path)
    runner = TaskWorkerRunner(container, poll_interval_seconds=0.1)
    runner.start()
    runner.stop(timeout=3)
    assert runner._thread is None
    runner.start()  # 重启合法
    runner.stop(timeout=3)
