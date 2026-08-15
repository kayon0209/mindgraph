"""领域错误类型的单元测试。"""
from __future__ import annotations

import pytest

from domain.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    DocumentNotFoundError,
    InternalError,
    InvalidDocumentFormatError,
    InvalidStateTransitionError,
    NotFoundError,
    ProductError,
    ProviderUnavailableError,
    RateLimitError,
    RetrievalUnavailableError,
    ValidationError,
)


class TestProductError:
    """测试基础异常类。"""

    def test_default_code_and_status(self):
        exc = ProductError()
        assert exc.code == "product_error"
        assert exc.status_code == 400

    def test_with_message(self):
        exc = ProductError("Something went wrong")
        assert str(exc) == "Something went wrong"

    def test_with_detail(self):
        exc = ProductError("Bad request", detail={"field": "email", "reason": "invalid format"})
        assert exc.detail == {"field": "email", "reason": "invalid format"}


class TestNotFoundErrors:
    def test_not_found_default(self):
        exc = NotFoundError("Resource missing")
        assert exc.code == "not_found"
        assert exc.status_code == 404

    def test_document_not_found(self):
        exc = DocumentNotFoundError("doc-123")
        assert exc.code == "document_not_found"
        assert exc.status_code == 404


class TestValidationErrors:
    def test_validation_error(self):
        exc = ValidationError("Invalid input")
        assert exc.code == "validation_error"
        assert exc.status_code == 422

    def test_invalid_document_format(self):
        exc = InvalidDocumentFormatError("Only Markdown supported")
        assert exc.code == "invalid_document_format"

    def test_invalid_state_transition_is_conflict(self):
        exc = InvalidStateTransitionError("Cannot transition from active to deleted")
        assert exc.status_code == 409
        assert exc.code == "invalid_state_transition"


class TestAuthErrors:
    def test_authentication_error(self):
        exc = AuthenticationError("Invalid API key")
        assert exc.code == "authentication_error"
        assert exc.status_code == 401

    def test_authorization_error(self):
        exc = AuthorizationError("Insufficient permissions")
        assert exc.code == "authorization_error"
        assert exc.status_code == 403

    def test_rate_limit_error(self):
        exc = RateLimitError("Too many requests")
        assert exc.code == "rate_limit_exceeded"
        assert exc.status_code == 429


class TestProviderErrors:
    def test_provider_unavailable_with_name(self):
        exc = ProviderUnavailableError("DeepSeek timeout", provider_name="deepseek")
        assert exc.provider_name == "deepseek"
        assert exc.status_code == 503

    def test_retrieval_unavailable(self):
        exc = RetrievalUnavailableError("FAISS index corrupted")
        assert exc.code == "retrieval_unavailable"
        assert exc.status_code == 503


class TestInternalErrors:
    def test_internal_error(self):
        exc = InternalError("Unexpected failure")
        assert exc.code == "internal_error"
        assert exc.status_code == 500


class TestErrorInheritance:
    """确保所有异常都是 ProductError 的子类。"""

    @pytest.mark.parametrize("exc_class", [
        NotFoundError, DocumentNotFoundError,
        ValidationError, InvalidDocumentFormatError, InvalidStateTransitionError,
        AuthenticationError, AuthorizationError, RateLimitError,
        ProviderUnavailableError, RetrievalUnavailableError,
        InternalError, ConflictError,
    ])
    def test_all_errors_extend_product_error(self, exc_class):
        assert issubclass(exc_class, ProductError)

    @pytest.mark.parametrize("exc_class", [
        NotFoundError, ValidationError, AuthenticationError,
        AuthorizationError, RateLimitError, ProviderUnavailableError,
        RetrievalUnavailableError, InternalError, ConflictError,
    ])
    def test_all_errors_are_exceptions(self, exc_class):
        assert issubclass(exc_class, Exception)
