"""M0-1：对外契约表面快照测试（冻结，只做加法）。

冻结对象：SSE 事件名/信封键/stream_mode、ResultState/ErrorCode 取值、
MCP JSON-RPC 错误码、access_audit 的 decision/action 面。

守则：新增能力必须显式扩展本文件期望；删除/重命名任何既有值 = 破坏性
变更，契约测试立即失败——这是 REST/SSE/MCP/Assist 多通道共用语义的护栏。
"""

from __future__ import annotations

from domain.contracts import (
    ACCESS_AUDIT_ACTIONS,
    ACCESS_AUDIT_DECISIONS,
    MCP_JSONRPC_ERROR_CODES,
    SSE_ENVELOPE_KEYS,
    SSE_EVENT_NAMES,
    STREAM_MODE_VALUES,
    error_event_data,
)
from domain.models import ErrorCode, ResultState, error_code_for_result_state


def test_sse_event_names_are_frozen():
    # 既有 14 个事件 + M2 agent-assist 6 个新事件（flag 门控，默认关闭不产出；
    # 旧客户端按契约忽略未知事件名）
    assert SSE_EVENT_NAMES == (
        "request_started",
        "scope_check_completed",
        "retrieval_routed",
        "retrieval_started",
        "retrieval_completed",
        "rerank_completed",
        "degraded",
        "policy_conflict_detected",
        "generation_started",
        "answer_delta",
        "citations",
        "usage",
        "completed",
        "error",
        "plan_created",
        "tool_call_started",
        "tool_call_finished",
        "clarification_required",
        "loop_fell_back",
        "citation_integrity_checked",
    )
    assert len(SSE_EVENT_NAMES) == 20
    assert len(set(SSE_EVENT_NAMES)) == len(SSE_EVENT_NAMES)


def test_sse_event_names_split_by_generation():
    """基线 14 事件与 M2 新 6 事件的分界冻结：assist 事件只增不改。"""
    assert SSE_EVENT_NAMES[:14] == (
        "request_started",
        "scope_check_completed",
        "retrieval_routed",
        "retrieval_started",
        "retrieval_completed",
        "rerank_completed",
        "degraded",
        "policy_conflict_detected",
        "generation_started",
        "answer_delta",
        "citations",
        "usage",
        "completed",
        "error",
    )
    assert set(SSE_EVENT_NAMES[14:]) == {
        "plan_created",
        "tool_call_started",
        "tool_call_finished",
        "clarification_required",
        "loop_fell_back",
        "citation_integrity_checked",
    }


def test_sse_envelope_keys_are_frozen():
    assert SSE_ENVELOPE_KEYS == ("request_id", "event", "timestamp", "data")


def test_stream_mode_values_are_frozen():
    assert STREAM_MODE_VALUES == {"deterministic", "provider_native", "deterministic_fallback"}


def test_result_state_values_are_frozen():
    assert {item.value for item in ResultState} == {
        "answered",
        "insufficient_evidence",
        "permission_denied",
        "conflicting_evidence",
        "out_of_scope",
        "model_unavailable",
        "retrieval_unavailable",
        "system_error",
    }


def test_error_code_is_superset_of_result_states_with_exact_mapping():
    # 每个 ResultState 都有对应 ErrorCode，且映射是一一、无漂移的
    state_values = {item.value for item in ResultState}
    for state in ResultState:
        code = error_code_for_result_state(state)
        assert code.value == state.value
        assert code in ErrorCode
    # ErrorCode 至少覆盖全部终态；新增终态若忘登记会在此失败
    assert state_values <= {item.value for item in ErrorCode}


def test_error_code_transport_layer_values_are_frozen():
    transport = {item.value for item in ErrorCode} - {item.value for item in ResultState}
    assert transport == {
        "aborted",
        "stream_error",
        "provider_error",
        "provider_unavailable",
        "quota_exhausted",
        "rate_limited",
        "authentication_failed",
        "model_not_found",
        "invalid_request",
        "timeout",
    }


def test_mcp_jsonrpc_error_codes_are_frozen():
    # JSON-RPC 规范码 + MindGraph 扩展码（mcp_server.handle_jsonrpc / mcp.py）
    assert MCP_JSONRPC_ERROR_CODES == {-32700, -32600, -32601, -32602, -32603, -32000, -32001}


def test_access_audit_decision_surface_is_frozen():
    assert ACCESS_AUDIT_DECISIONS == {"allow", "deny"}


def test_access_audit_action_surface_is_frozen():
    assert ACCESS_AUDIT_ACTIONS == {
        "chat",
        "assist",
        "assist_stream",
        "get_note",
        "list_notes",
        "mcp_search",
        "mcp_get_note",
        "mcp_list_notes",
        "mcp_list_relations",
        "mcp_evaluation_overview",
        "mcp_assist",
        "mcp_call",
        "mcp_batch",
    }


def test_error_event_data_dual_writes_code_and_error_code():
    payload = error_event_data("retrieval_unavailable", "检索服务暂不可用。", detail="boom")
    assert payload["code"] == "retrieval_unavailable"
    assert payload["error_code"] == "retrieval_unavailable"
    assert payload["message"] == "检索服务暂不可用。"
    assert payload["detail"] == "boom"

    minimal = error_event_data("stream_error", "Stream failed.")
    assert "detail" not in minimal
