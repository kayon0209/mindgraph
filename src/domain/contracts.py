"""对外契约表面（M0：治理与契约基线）。

本模块集中定义“机器可判定”的通道契约常量，供契约快照测试冻结、供
REST/SSE/MCP/Assist 各通道复用同一套语义。约定：

- 只做加法：任何新事件/新状态/新错误码必须先在 `SSE_EVENT_NAMES`、
  `ErrorCode`（见 domain.models）中显式登记，再由 `tests/test_contract_surface.py`
  冻结比对；
- 绝不重命名/删除既有值，否则契约测试立即失败。

背景：ARCH-REVIEW（2026-08-28）要求 agent 面向的交付面必须有机器可判定
的判定/错误契约，而不是靠人读自然语言消息。
"""

from __future__ import annotations

from typing import Any, Final

# ── SSE 事件名（按 ChatService.stream 的产出顺序） ──
# M2 起新增 agent-assist 事件（AGENTS assist 模式，flag 门控；旧客户端必须忽略未知事件）：
# plan_created / tool_call_started / tool_call_finished / clarification_required /
# loop_fell_back / citation_integrity_checked。默认关闭时不产生。
SSE_EVENT_NAMES: Final[tuple[str, ...]] = (
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
    # ── M2 agent-assist 事件（AGENT_ASSIST_ENABLED 才会产出） ──
    "plan_created",
    "tool_call_started",
    "tool_call_finished",
    "clarification_required",
    "loop_fell_back",
    "citation_integrity_checked",
)

# SSE 信封外层键（ChatService.stream 的事件包装，各通道共用）
SSE_ENVELOPE_KEYS: Final[tuple[str, ...]] = ("request_id", "event", "timestamp", "data")

# answer_delta 的 stream_mode 取值
STREAM_MODE_VALUES: Final[frozenset[str]] = frozenset({"deterministic", "provider_native", "deterministic_fallback"})

# MCP JSON-RPC 错误码（mcp_server.handle_jsonrpc 与 api/routes/mcp.py 共用）
MCP_JSONRPC_ERROR_CODES: Final[frozenset[int]] = frozenset({-32700, -32600, -32601, -32602, -32603, -32000, -32001})

# access_audit.decision 的取值面（各通道审计共用）
ACCESS_AUDIT_DECISIONS: Final[frozenset[str]] = frozenset({"allow", "deny"})

# access_audit.action 的高层取值（REST/MCP/Assist 通道审计动作）
ACCESS_AUDIT_ACTIONS: Final[frozenset[str]] = frozenset(
    {
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
)


def error_event_data(code: str, message: str, detail: str | None = None) -> dict[str, Any]:
    """构造 SSE error 事件的 data 载荷。

    code 双写为 error_code，便于 agent 侧只读 error_code 一个字段即可判定。
    """
    payload: dict[str, Any] = {"code": code, "error_code": code, "message": message}
    if detail:
        payload["detail"] = detail
    return payload
