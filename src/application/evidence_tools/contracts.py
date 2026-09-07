"""Evidence Tool 契约（M1，实施方案 §5.1）。

每个对外工具声明 name、mode、risk、allowed contexts、timeout、输入上限、
审批要求与脱敏策略；Registry 统一执行 principal→build_access_scope→参数
校验→deadline→handler→结果裁剪→审计→脱敏轨迹。MCP 只保留 JSON-RPC
envelope 映射，不再承载业务 if-chain。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class ToolSpec:
    """一个受治理工具的声明式规格。"""

    name: str
    description: str
    mode: str  # "read" | "write"（M1 只有 read；write 见 M5）
    risk: str = "low"  # "low" | "medium" | "high"
    allowed_in: frozenset[str] = frozenset({"external_mcp"})  # 通道白名单
    timeout_seconds: float = 5.0
    max_input_bytes: int = 8192
    requires_approval: bool = False
    # 轨迹脱敏：这些参数键不进入 tool_call_log/审计 metadata 的明文
    redact_fields: frozenset[str] = frozenset()
    # JSON Schema（MCP inputSchema 直接消费）
    input_schema: dict[str, Any] = field(default_factory=dict)


# Registry 执行器的统一签名：handler(principal, scope, arguments, deadline) -> dict
ToolHandler = Callable[[dict[str, Any], dict[str, Any] | None, dict[str, Any], float | None], dict[str, Any]]

# 通道白名单值域（M5-A 起 write 工具仅 external_mcp）
TOOL_CONTEXTS = ("external_mcp", "assist", "task")
