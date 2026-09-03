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


def test_trace_persistence_strips_access_scope(tmp_path):
    """权限侧信道回归锁定（审查发现）：trace 持久化面不得携带主体的
    allow/deny ACL 规则（applied_filters.access_scope），只留 acl_applied
    布尔标记——query_logs 可经只读端点回放，完整规则构成侧信道。"""
    import json as _json

    from application.chat_service import ChatService
    from domain.models import ChatRequest
    from infrastructure.database import ProductDatabase
    from retrieval.types import Chunk, RetrievalCandidate, RetrievalTrace

    class P:
        provider_name = "fake"
        model_name = "m"
        available = True

        def complete(self, _m):
            return ("依据 [citation-1]。", {"total_tokens": 1})

        def stream(self, _m):
            yield {"delta": "x"}

    class Pipeline:
        def retrieve(self, *_a, **_k):
            chunk = Chunk("p.md::0", "内容", "p.md", 0, "s",
                         {"document_title": "制度", "policy_key": "k", "policy_status": "active",
                          "document_version": "v1", "effective_from": "2026-01-01"})
            return RetrievalTrace(
                query="q", requested_strategy="hybrid", actual_strategy="hybrid",
                candidate_counts={"final": 1},
                final_selected_chunks=[RetrievalCandidate(chunk=chunk, final_rank=1, dense_score=0.9)],
                latency_ms={"total_retrieval_ms": 0.1}, index_version="idx",
                applied_filters={"query_date": None, "access_scope": {"allow": ["department:finance"], "deny": [], "user": "attacker-observed"}},
                warnings=[],
            )

    database = ProductDatabase(tmp_path / "scope-strip.sqlite3")
    database.initialize()
    service = ChatService(database, lambda top_k: Pipeline(), P(), privacy_log_questions=False)

    # 携带受限 scope 的请求（模拟真实 ACL 用户）
    scope = {"user": "finance-user", "allow": ["department:finance"], "deny": [], "roles": ["read"]}
    result = service.answer(ChatRequest(question="报销时限？", retrieval_strategy="hybrid"), access_scope=scope)

    row = database.fetch_one("SELECT trace_json FROM query_logs ORDER BY created_at DESC LIMIT 1")
    persisted = _json.loads(row["trace_json"])
    af = persisted.get("apied_filters", persisted.get("applied_filters", {}))
    assert "access_scope" not in af, "ACL 规则泄漏进持久化 trace"
    assert af.get("acl_applied") is True, "布尔标记应保留"
    assert "department:finance" not in row["trace_json"], "allow 规则明文泄漏"
    database.close()
