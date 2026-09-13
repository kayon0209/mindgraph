"""生产级错误类型定义。所有业务异常继承 ProductError，统一错误码与 HTTP 状态码。"""
from __future__ import annotations

from typing import Any


class ProductError(Exception):
    """项目根异常，所有业务异常基类。"""
    code: str = "product_error"
    status_code: int = 400
    detail: dict[str, Any] | None = None

    def __init__(self, message: str = "", detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.detail = detail


# ── 404 资源不存在 ──

class NotFoundError(ProductError):
    code = "not_found"
    status_code = 404


class DocumentNotFoundError(NotFoundError):
    code = "document_not_found"


class IndexVersionNotFoundError(NotFoundError):
    code = "index_version_not_found"


# ── 400 业务校验错误 ──

class ValidationError(ProductError):
    """请求参数校验失败。"""
    code = "validation_error"
    status_code = 422


class InvalidDocumentFormatError(ValidationError):
    code = "invalid_document_format"


class DuplicateDocumentError(ValidationError):
    code = "duplicate_document"


class InvalidStateTransitionError(ValidationError):
    """文档/索引状态机不允许的转换。"""
    code = "invalid_state_transition"
    status_code = 409


class IndexShrinkageError(InvalidStateTransitionError):
    """索引重建会导致活跃索引丢失文档 —— 拒绝在无人知晓的情况下变小。

    2026-09-09 真实事故：一次 ``/knowledge/index/rebuild`` 把 25 篇索引换成 4 篇，
    而 ``notes`` 表仍显示全部 ready。守卫默认 fail-closed，需显式 ``force`` 才放行。
    """

    code = "index_shrinkage_blocked"


class IndexConsistencyError(InvalidStateTransitionError):
    """候选索引与活跃索引的切分口径/文档覆盖不一致 —— 拒绝在无人知晓的情况下换口径。

    2026-09-11 实测：``data/retrieval_indexes/`` 里 69 chunks（``document_loader``
    扁平切分）与 98 chunks（``StructuredChunker``）两个版本并存，``CURRENT`` 在它们
    之间被切换过而**没有任何机制阻止**。两者的 ``chunk_size/overlap`` 数值相同
    （500/50），差异在**构建入口**——切换等于换掉全部 chunk_id，已公布的检索指标
    只对应其中一套。

    门禁默认 fail-closed；产品确认要换口径时显式放行（``allow_chunking_change``）。
    与 :class:`IndexShrinkageError` 同族（都是"索引状态转换不合法"），但针对的是
    **口径一致性**而非规模缩水。
    """

    # 与 IndexShrinkageError.index_shrinkage_blocked 对称：门禁类错误必须有自己的
    # code——否则调用方只能靠 HTTP 409 + 文案去区分"缩水拦截"和"口径拦截"，
    # 而这两种拦截的重试指引完全不同（一个补文档，一个 force 换口径）。
    code = "index_consistency_blocked"


class ChunkingError(ValidationError):
    code = "chunking_error"


class EmbeddingError(ValidationError):
    code = "embedding_error"


# ── 401 / 403 认证鉴权 ──

class AuthenticationError(ProductError):
    code = "authentication_error"
    status_code = 401


class AuthorizationError(ProductError):
    code = "authorization_error"
    status_code = 403


class RateLimitError(ProductError):
    code = "rate_limit_exceeded"
    status_code = 429


# ── 503 外部依赖不可用 ──

class ProviderUnavailableError(ProductError):
    code = "provider_unavailable"
    status_code = 503
    detail: dict[str, Any] | None = None

    def __init__(self, message: str = "", provider_name: str = "", detail: dict[str, Any] | None = None) -> None:
        super().__init__(message, detail=detail)
        self.provider_name = provider_name


class RetrievalUnavailableError(ProductError):
    code = "retrieval_unavailable"
    status_code = 503


class IndexUnavailableError(ProductError):
    code = "index_unavailable"
    status_code = 503


class EmbeddingProviderError(ProductError):
    code = "embedding_provider_error"
    status_code = 503


# ── 500 内部错误 ──

class InternalError(ProductError):
    code = "internal_error"
    status_code = 500


class DatabaseError(InternalError):
    code = "database_error"


class ConfigurationError(InternalError):
    code = "configuration_error"


# ── 409 冲突 ──

class ConflictError(ProductError):
    code = "conflict"
    status_code = 409
