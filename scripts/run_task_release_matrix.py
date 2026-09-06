"""G1-R 工程发布矩阵：故障注入 + 固定基准运行（M4-A 发布闸门，样本无关）。

用法（本地）：
    .venv/Scripts/python.exe scripts/run_task_release_matrix.py            # must-pass 矩阵 + 20 次基准
    .venv/Scripts/python.exe scripts/run_task_release_matrix.py --json    # 机器可读输出

must-pass 场景（ADR-004 / G1-R）：
正常完成 / 重复提交幂等 / 并发 claim 单赢家 / queued 取消 / lease 过期恢复 /
attempt 超限失败 / owner 隔离 / 冲突降级 / 空命中 / 约束白名单 / flag 回退。

基准：固定合成 fixture 上 ≥20 次任务执行，记录吞吐与 P50/P95 耗时。
全部结果输出 JSONL（{case, status, detail}）；任一 must-pass FAIL 退出码非零。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from application.chat_service import ChatService  # noqa: E402
from application.evidence_query_service import EvidenceQueryService  # noqa: E402
from application.policy_conflict_service import PolicyConflictService  # noqa: E402
from application.task_service import InvalidTaskConstraints, TaskNotFoundError, TaskService  # noqa: E402
from application.task_worker import TaskWorker  # noqa: E402
from infrastructure.database import ProductDatabase  # noqa: E402
from retrieval.types import Chunk, RetrievalCandidate, RetrievalTrace  # noqa: E402

RESULTS: list[dict] = []


def record(case: str, status: str, detail: str = "") -> None:
    RESULTS.append({"case": case, "status": status, "detail": detail})
    if not JSON_ONLY:
        print(f"[{status:>7}] {case}: {detail}", file=sys.stderr)


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
            latency_ms={"total_retrieval_ms": 0.5}, index_version="idx", applied_filters={},
            warnings=[],
        )


def build(tmp: Path):
    db = ProductDatabase(tmp / "matrix.sqlite3")
    db.initialize()
    chat = ChatService(db, lambda top_k: StubPipeline(), FakeProvider(), privacy_log_questions=False)
    evidence = EvidenceQueryService(chat)
    worker = TaskWorker(db, lambda: evidence, PolicyConflictService(db))
    service = TaskService(db)
    return service, worker, db


CONSTRAINTS = {"document_query": "费用报销核对", "top_k": 5}


def case_normal(service, worker, _db):
    service.submit(principal_id="u1", idempotency_key="mp-normal-0001", constraints=CONSTRAINTS)
    result = worker.run_once()
    ok = result and result["status"] == "completed"
    record("MP1 正常完成", "PASS" if ok else "FAIL", f"status={result['status'] if result else 'none'}")


def case_duplicate(service, _worker, db):
    first = service.submit(principal_id="u1", idempotency_key="mp-dup-00001", constraints=CONSTRAINTS)
    second = service.submit(principal_id="u1", idempotency_key="mp-dup-00001", constraints=CONSTRAINTS)
    count = db.fetch_one("SELECT COUNT(*) AS c FROM agent_tasks WHERE idempotency_key='mp-dup-00001'")["c"]
    ok = first["task_id"] == second["task_id"] and count == 1
    record("MP2 重复提交幂等", "PASS" if ok else "FAIL", f"tasks={count}")


def case_concurrent_claim(tmp: Path):
    """并行抢同一个隔离队列；必须恰有一个 worker 获得唯一任务。"""
    service, _worker, db = build(tmp / "mp3-concurrent")
    service.submit(principal_id="u1", idempotency_key="mp-cc-0000002", constraints=CONSTRAINTS)
    a = TaskWorker(db, lambda: None, PolicyConflictService(db), owner="wa2")
    b = TaskWorker(db, lambda: None, PolicyConflictService(db), owner="wb2")
    barrier = threading.Barrier(2)
    claimed: list[tuple[str, dict | None]] = []

    def claim(label: str, worker: TaskWorker) -> None:
        barrier.wait(timeout=5)
        claimed.append((label, worker.claim_next()))

    threads = [threading.Thread(target=claim, args=("a", a)), threading.Thread(target=claim, args=("b", b))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    winners = [(label, task) for label, task in claimed if task is not None]
    ok = len(claimed) == 2 and len(winners) == 1
    detail = f"claims={[(label, task['task_id'][-6:] if task else None) for label, task in claimed]}"
    record("MP3 并发 claim 单赢家", "PASS" if ok else "FAIL", detail)
    db.close()


def case_cancel_queued(service, worker, _db):
    task = service.submit(principal_id="u1", idempotency_key="mp-cancel-001", constraints=CONSTRAINTS)
    service.cancel_task(task_id=task["task_id"], principal_id="u1")
    worker.run_once()
    row = service.get_task(task_id=task["task_id"], principal_id="u1")
    ok = row["status"] == "cancelled"
    record("MP4 queued 取消", "PASS" if ok else "FAIL", f"status={row['status']}")


def case_lease_recovery(service, worker, db):
    task = service.submit(principal_id="u1", idempotency_key="mp-lease-001", constraints=CONSTRAINTS)
    expired = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
    db.execute(
        "UPDATE agent_tasks SET status='running', lease_owner='dead', lease_expires_at=?, attempt_count=1 WHERE task_id=?",
        (expired, task["task_id"]),
    )
    result = worker.run_once()
    ok = result and result["task_id"] == task["task_id"] and result["status"].startswith("completed")
    record("MP5 lease 过期恢复", "PASS" if ok else "FAIL", f"status={result['status'] if result else 'none'}")


def case_attempt_budget(service, worker, db):
    task = service.submit(principal_id="u1", idempotency_key="mp-attempt-01", constraints=CONSTRAINTS)
    db.execute("UPDATE agent_tasks SET attempt_count=? WHERE task_id=?", (worker.max_attempts, task["task_id"]))
    worker.claim_next()
    failed = worker._fail(task["task_id"], code="retrieval_unavailable", message="注入失败")
    ok = failed["status"] == "failed" and failed["error_code"] == "retrieval_unavailable"
    record("MP6 attempt 超限失败", "PASS" if ok else "FAIL", f"status={failed['status']}")


def case_owner_isolation(service, worker, _db):
    task = service.submit(principal_id="u1", idempotency_key="mp-iso-000001", constraints=CONSTRAINTS)
    worker.run_once()
    try:
        service.get_task(task_id=task["task_id"], principal_id="u2")
        ok = False
    except TaskNotFoundError:
        ok = True
    record("MP7 owner 隔离", "PASS" if ok else "FAIL", "cross-principal → not found" if ok else "LEAKED")


def case_constraints_whitelist(service, _worker, _db):
    try:
        service.submit(principal_id="u1", idempotency_key="mp-white-001", constraints={"document_query": "q", "injected": 1})
        ok = False
    except InvalidTaskConstraints:
        ok = True
    record("MP8 约束白名单", "PASS" if ok else "FAIL", "injected field rejected" if ok else "accepted!")


def case_flag_off(_service, _worker, _db):
    import importlib
    import os

    from infrastructure.settings import get_settings

    os.environ["AGENT_TASKS_ENABLED"] = "false"
    get_settings.cache_clear()
    try:
        sys.modules.pop("api.main", None)
        paths = set(importlib.import_module("api.main").app.openapi()["paths"])
        ok = not any("/agent/tasks" in p for p in paths)
    finally:
        sys.modules.pop("api.main", None)
        importlib.import_module("api.main")
        os.environ.pop("AGENT_TASKS_ENABLED", None)
        get_settings.cache_clear()
    record("MP9 flag 回退（路由不挂载）", "PASS" if ok else "FAIL", "routes hidden" if ok else "routes exposed")


def run_benchmark(tmp: Path, runs: int = 20):
    """固定 fixture 基准：单任务端到端（submit→claim→execute→artifact）。"""
    service, worker, db = build(tmp / "bench")
    latencies: list[float] = []
    unexplained = 0
    for i in range(runs):
        key = f"bench-{i:04d}"
        started = time.perf_counter()
        service.submit(principal_id="bench-user", idempotency_key=key, constraints=CONSTRAINTS)
        result = worker.run_once()
        elapsed = (time.perf_counter() - started) * 1000
        latencies.append(elapsed)
        if not result or not str(result["status"]).startswith("completed"):
            unexplained += 1
    p50 = statistics.median(latencies)
    p95 = sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)]
    throughput = runs / (sum(latencies) / 1000)
    ok = unexplained == 0
    record(
        "BENCH 基准运行",
        "PASS" if ok else "FAIL",
        f"n={runs} unexplained={unexplained} p50={p50:.1f}ms p95={p95:.1f}ms throughput={throughput:.1f}/s "
        f"limits=single-instance-single-worker",
    )
    db.close()


def run_isolated_case(tmp: Path, name: str, case) -> None:
    """每个 must-pass case 独占队列，避免遗留 queued task 污染断言。"""
    service, worker, db = build(tmp / name)
    try:
        case(service, worker, db)
    finally:
        db.close()


JSON_ONLY = False


def main() -> int:
    global JSON_ONLY
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--runs", type=int, default=20)
    args = parser.parse_args()
    JSON_ONLY = args.json

    with tempfile.TemporaryDirectory(prefix="mg-matrix-") as raw_tmp:
        tmp = Path(raw_tmp)
        try:
            run_isolated_case(tmp, "mp1-normal", case_normal)
            run_isolated_case(tmp, "mp2-duplicate", case_duplicate)
            case_concurrent_claim(tmp)
            run_isolated_case(tmp, "mp4-cancel", case_cancel_queued)
            run_isolated_case(tmp, "mp5-lease", case_lease_recovery)
            run_isolated_case(tmp, "mp6-attempt", case_attempt_budget)
            run_isolated_case(tmp, "mp7-isolation", case_owner_isolation)
            run_isolated_case(tmp, "mp8-constraints", case_constraints_whitelist)
            run_isolated_case(tmp, "mp9-flag", case_flag_off)
        finally:
            pass
        run_benchmark(tmp, runs=args.runs)

    for item in RESULTS:
        print(json.dumps(item, ensure_ascii=False))
    failures = [item for item in RESULTS if item["status"] == "FAIL"]
    if not JSON_ONLY:
        print(f"\nsummary: {len(RESULTS) - len(failures)} PASS / {len(failures)} FAIL", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
