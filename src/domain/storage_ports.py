"""PR-15 存储与队列边界 Protocol：Enterprise Profile 的可替换边界。

## 与既有 Protocol 的关系（现场核对修正 2：补缺，不是从零建）

已有接口**不重复定义**：
- 检索面：``retrieval.types`` 的 ``EmbeddingProvider`` / ``DenseRetriever`` /
  ``SparseRetriever`` / ``FusionStrategy`` / ``Reranker``；
- 生成面：``domain.interfaces.ChatProvider``；
- 解析面：``infrastructure.parsers.base.DocumentParser``。

本模块补的是**存储与队列**缺口：
- :class:`TaskQueue` —— 任务队列（submit/claim/renew/complete/fail），
  幂等键 ``(principal_id, idempotency_key)``，租约互斥；
- :class:`DocumentStore` / :class:`MetadataStore` / :class:`VectorIndex` /
  :class:`SparseIndex` —— 存储边界（Local Profile 实现分别是本地文件 /
  SQLite / FAISS / BM25；见 ADR-006 的替换触发阈值）。

## 语义承诺（adapter 与替身都必须满足，契约测试锁定）

- 幂等：同幂等键重复 submit 返回同 task_id，不产生第二条；
- 互斥：claim 后他人 claim 不到同一任务；complete 后任务永不被再 claim；
- 租约：持有者才能续租；
- 失败可观测：fail 记录原因并递增 attempt。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TaskQueue(Protocol):
    """任务队列边界。Local 实现：``agent_tasks`` 表（SqliteTaskQueue）。"""

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
        """提交任务；同 (principal_id, idempotency_key) 幂等返回原 task_id。"""
        ...

    def claim_next(self, *, lease_seconds: float, owner: str) -> dict[str, Any] | None:
        """互斥认领一个 queued 任务（queued → running + 租约）；空队列返回 None。"""
        ...

    def renew_lease(self, task_id: str, *, owner: str, lease_seconds: float) -> bool:
        """续租：仅当前持有者成功。"""
        ...

    def complete(self, task_id: str, *, result: dict[str, Any]) -> None:
        """终态完成；此后任务永不被再 claim（不重复副作用）。"""
        ...

    def fail(self, task_id: str, *, reason: str) -> None:
        """记录失败并递增 attempt（重试/升级语义由调用方决定）。"""
        ...


@runtime_checkable
class DocumentStore(Protocol):
    """文档正文的读写边界。Local 实现：版本化目录（document_versions 源文件）。"""

    def read(self, document_id: str) -> bytes | None: ...

    def write(self, document_id: str, content: bytes) -> None: ...


@runtime_checkable
class MetadataStore(Protocol):
    """结构化元数据边界。Local 实现：SQLite（ProductDatabase）。"""

    def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None: ...

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]: ...

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int: ...


@runtime_checkable
class VectorIndex(Protocol):
    """稠密向量索引边界（与 retrieval.types.DenseRetriever 语义对齐）。

    Local 实现：FAISS（FAISSDenseRetriever——它已结构性满足本 Protocol，
    因此这里不重复定义 search 签名，边界仅用于 Enterprise 侧替换判定）。
    """


@runtime_checkable
class SparseIndex(Protocol):
    """稀疏索引边界（与 retrieval.types.SparseRetriever 语义对齐）。"""
