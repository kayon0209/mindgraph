"""M0-4：日志脱敏审计测试。

验证 StructuredFormatter（生产 JSON 日志）：
- 敏感键（含大小写/分隔符变体）整值遮蔽；
- 疑似密钥形态的值/消息片段（sk-…、Bearer …、id.secret、超长 token）遮蔽；
- 非敏感字段（request_id/decision/duration/policy_key 等审计价值字段）保留；
- 递归脱敏嵌套 dict/list。
"""

from __future__ import annotations

import json
import logging

from infrastructure.logging_config import StructuredFormatter


def _format_record(message: str = "log", extra: dict | None = None) -> dict:
    record = logging.LogRecord(
        name="mindgraph.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in (extra or {}).items():
        record.__dict__[key] = value
    return json.loads(StructuredFormatter().format(record))


def test_api_key_extra_is_redacted_by_exact_and_variant_keys():
    for key in ("api_key", "API_KEY", "client_secret", "Authorization", "accessToken"):
        payload = _format_record(extra={key: "sk-test-secret-value-123456"})
        assert payload[key] == "[REDACTED]"


def test_secret_shaped_value_is_redacted_even_under_innocent_key():
    for value in (
        "sk-abcdefghijklmnop123456",
        "sk-ant-api03-abcdefghijklmnopqrstuvwxyz123456",
        "Bearer eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature",
        "1234567890abcdefghij.ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",  # zhipu id.secret
    ):
        payload = _format_record(extra={"detail": value})
        assert payload["detail"] == "[REDACTED]"


def test_secret_span_in_message_is_redacted_but_text_kept():
    payload = _format_record(message="调用 provider 失败：sk-abcdefghijklmnop123456，请重试")
    assert "sk-abcdefghijklmnop123456" not in payload["message"]
    assert "[REDACTED]" in payload["message"]
    assert "请重试" in payload["message"]


def test_nested_dict_and_list_values_are_recursively_redacted():
    payload = _format_record(
        extra={
            "metadata": {
                "scope_user": "finance_user",
                "headers": {"authorization": "Bearer tok1234567890abcdef"},
                "tags": ["public", "sk-another-secret-value-123456"],
            }
        }
    )
    nested = payload["metadata"]
    assert nested["scope_user"] == "finance_user"
    assert nested["headers"]["authorization"] == "[REDACTED]"
    assert nested["tags"] == ["public", "[REDACTED]"]


def test_audit_value_fields_are_retained():
    payload = _format_record(
        extra={
            "request_id": "req-1234",
            "decision": "allow",
            "resource": "notes/finance",
            "duration_ms": 12.5,
            "policy_key": "expense.general",  # 非密钥：normalized 不在敏感集合
            "result_state": "answered",
        }
    )
    assert payload["request_id"] == "req-1234"
    assert payload["decision"] == "allow"
    assert payload["resource"] == "notes/finance"
    assert payload["duration_ms"] == 12.5
    assert payload["policy_key"] == "expense.general"
    assert payload["result_state"] == "answered"


def test_short_hex_request_id_and_uuid_are_not_redacted():
    # uuid4().hex 是 32 位 hex（MCP request_id 形态）——不应被误伤
    payload = _format_record(extra={"request_id": "a" * 32})
    assert payload["request_id"] == "a" * 32


def test_exception_message_is_redacted():
    try:
        raise RuntimeError("provider call failed with sk-abcdefghijklmnop123456")
    except RuntimeError as exc:
        record = logging.LogRecord(
            name="mindgraph.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="boom",
            args=(),
            exc_info=(RuntimeError, exc, exc.__traceback__),
        )
    payload = json.loads(StructuredFormatter().format(record))
    assert "sk-abcdefghijklmnop123456" not in payload["exception"]["message"]
    assert "[REDACTED]" in payload["exception"]["message"]
