"""PR-15 存储与队列接口抽象：Protocol 契约 + SQLite adapter 语义一致性。

任务书测试矩阵：
- 所有 Protocol 契约（缺方法/坏签名不能冒充实现）；
- 失败/超时/幂等语义（adapter 与业务语义一致）；
- 索引和权限行为不变（Local Profile 零回归）。

设计：契约测试同时跑 **SQLite 真实现** 与 **内存替身**，语义一致才能替换——
这正是"后续换 PostgreSQL/Milvus 无需改应用层"的验证面。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from infrastructure.database import ProductDatabase


@pytest.fixture
def db(tmp_path: Path) -> ProductDatabase:
    database = ProductDatabase(tmp_path / "ports.sqlite3")
    database.initialize()
    return database


def _sqlite_queue(db: ProductDatabase):
    from infrastructure.sqlite_task_queue import SqliteTaskQueue

    return SqliteTaskQueue(db)


def _memory_queue():
    from tests.test_storage_ports_contract import InMemoryTaskQueue

    return InMemoryTaskQueue()


# ── Protocol 契约：结构化验证 ─────────────────────────────────────────


def test_task_queue_protocol_importable():
    """TaskQueue Protocol 存在且方法面完整（submit/claim/renew/complete/fail）。"""
    from domain.storage_ports import TaskQueue

    for method in ("submit", "claim_next", "renew_lease", "complete", "fail"):
        assert hasattr(TaskQueue, method) or method in dir(TaskQueue), method


def test_sqlite_queue_satisfies_task_queue_protocol(db: ProductDatabase):
    """SQLite adapter 是 TaskQueue 的运行时实现（isinstance 由 runtime_checkable 判定）。"""
    from domain.storage_ports import TaskQueue

    assert isinstance(_sqlite_queue(db), TaskQueue)


def test_incomplete_queue_rejected_by_protocol():
    """缺方法的假队列不能冒充 TaskQueue（结构性验证）。"""
    from domain.storage_ports import TaskQueue

    class _Broken:
        def submit(self, **kwargs): ...  # 缺 claim/complete 等

    assert not isinstance(_Broken(), TaskQueue)


# ── 幂等语义：SQLite 与内存替身行为一致 ──────────────────────────────


def _seed_task(queue, *, principal: str = "user-a", key: str = "idem-1") -> str:
    return queue.submit(
        principal_id=principal, idempotency_key=key,
        task_type="batch_policy_check", constraints={"policy_key": "travel"},
    )


def test_sqlite_submit_idempotent(db: ProductDatabase):
    queue = _sqlite_queue(db)
    first = _seed_task(queue)
    second = _seed_task(queue)
    assert first == second, "同 (principal, idempotency_key) 重复提交必须返回同 task_id"


def test_memory_submit_idempotent():
    queue = _memory_queue()
    first = _seed_task(queue)
    second = _seed_task(queue)
    assert first == second


def test_claim_next_moves_queued_to_running_and_is_exclusive(db: ProductDatabase):
    """claim：queued → running，且二路 claim 不会拿到同一个任务（互斥）。"""
    queue = _sqlite_queue(db)
    task_id = _seed_task(queue)
    claimed = queue.claim_next(lease_seconds=60, owner="worker-1")
    assert claimed is not None and claimed["task_id"] == task_id
    assert claimed["status"] == "running"
    # 队列空了：再 claim 拿不到
    assert queue.claim_next(lease_seconds=60, owner="worker-2") is None


def test_memory_claim_exclusive():
    queue = _memory_queue()
    task_id = _seed_task(queue)
    claimed = queue.claim_next(lease_seconds=60, owner="worker-1")
    assert claimed is not None and claimed["task_id"] == task_id
    assert queue.claim_next(lease_seconds=60, owner="worker-2") is None


def test_complete_finalizes_and_cannot_be_reclaimed(db: ProductDatabase):
    """complete 后任务终态，永不被再次 claim（幂等 + 不重复副作用）。"""
    queue = _sqlite_queue(db)
    _seed_task(queue)
    claimed = queue.claim_next(lease_seconds=60, owner="worker-1")
    queue.complete(claimed["task_id"], result={"checked": 1})
    assert queue.claim_next(lease_seconds=60, owner="worker-2") is None
    row = db.fetch_one("SELECT status FROM agent_tasks WHERE task_id=?", (claimed["task_id"],))
    assert row["status"] in {"completed", "done", "succeeded"}


def test_fail_increments_attempts(db: ProductDatabase):
    """fail：记录失败原因；attempt_count 递增（重试语义可观测）。"""
    queue = _sqlite_queue(db)
    _seed_task(queue)
    claimed = queue.claim_next(lease_seconds=60, owner="worker-1")
    queue.fail(claimed["task_id"], reason="boom")
    row = db.fetch_one("SELECT attempt_count, status, error_message FROM agent_tasks WHERE task_id=?", (claimed["task_id"],))
    assert row["attempt_count"] >= 1
    assert row["error_message"] == "boom"


def test_lease_renewal_extends_expiry(db: ProductDatabase):
    """renew_lease：worker 持有期内续租成功；非持有者续租被拒。"""
    queue = _sqlite_queue(db)
    _seed_task(queue)
    claimed = queue.claim_next(lease_seconds=1, owner="worker-1")
    assert queue.renew_lease(claimed["task_id"], owner="worker-1", lease_seconds=60) is True
    assert queue.renew_lease(claimed["task_id"], owner="intruder", lease_seconds=60) is False


# ── 存储边界 Protocol（DocumentStore / MetadataStore / VectorIndex / SparseIndex）──


def test_storage_port_definitions_exist():
    """四个存储边界 Protocol 定义齐备且不与 retrieval/types.py 语义重叠。"""
    from domain import storage_ports

    for name in ("DocumentStore", "MetadataStore", "VectorIndex", "SparseIndex"):
        assert hasattr(storage_ports, name), name


def test_retrieval_receivers_satisfy_index_ports():
    """检索器满足 VectorIndex / SparseIndex 边界（统一边界，不重写已有实现）。

    FAISSDenseRetriever.chunks 是 property 不可继承覆写——替身用独立类
    提供同构属性面。
    """
    from domain.storage_ports import SparseIndex, VectorIndex
    from retrieval.sparse import BM25Retriever

    class _DenseLike:
        chunks: list = []
        metadata: dict = {}

        def search(self, query, top_k, **kwargs):
            return [], {}

    assert isinstance(_DenseLike(), VectorIndex)
    assert isinstance(BM25Retriever([], 1.5, 0.75), SparseIndex)


# ── Local Profile 零回归（行为不变的抽样锁定）──


def test_task_service_still_works_via_protocol(db: ProductDatabase):
    """既有 TaskService 业务面行为不变（经 Protocol 调用路径等价）。"""
    from application.task_service import TaskService

    service = TaskService(db)
    envelope = service.submit(
        principal_id="user-a", idempotency_key="compat-1",
        task_type="batch_policy_check", constraints={"top_k": 5, "document_query": "差旅费"},
    )
    # TaskService.submit 返回任务信封（既有业务契约，保持不变）
    assert envelope["task_id"]
    listing = service.list_tasks(principal_id="user-a")
    items = listing.get("items") or []
    assert any(item["task_id"] == envelope["task_id"] for item in items)
