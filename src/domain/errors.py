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


class GovernanceUnavailableError(ProductError):
    code = "governance_unavailable"
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


class GovernanceConflictError(ConflictError):
    code = "governance_conflict"
