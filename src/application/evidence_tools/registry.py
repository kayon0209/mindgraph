"""Evidence Tool Registry（M1，实施方案 §5.1）。

统一执行面：principal→build_access_scope→参数校验→deadline→handler→
审计→脱敏轨迹。所有通道（MCP/后续 Assist/Task）都经 registry 调 handler，
禁止绕过 registry 直呼业务函数（保证 ACL 与审计不旁路）。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from application.access_control import build_access_scope, record_access_audit
from application.evidence_tools.contracts import ToolHandler, ToolSpec
from infrastructure.database import ProductDatabase

logger = logging.getLogger("mindgraph.evidence_tools")

# 已知 JSON-RPC 校验失败沿用 -32602 语义；deadline 超限沿用 -32000
class ToolValidationFailed(ValueError):
    pass


class ToolDeadlineExceeded(TimeoutError):
    pass


class UnknownToolError(ValueError):
    pass


class ToolExecutionRejected(ValueError):
    """工具被治理层拒绝（flag 关闭、审批未过、幂等冲突等）——业务级 fail-closed，
    与参数校验失败(-32602)区分，映射为工具级错误结果。"""


def _deadline_remaining(deadline: float | None) -> float:
    if deadline is None:
        return float("inf")
    return deadline - time.monotonic()


class EvidenceToolRegistry:
    """受治理工具的注册与执行。"""

    def __init__(self, database: ProductDatabase) -> None:
        self.database = database
        self._specs: dict[str, ToolSpec] = {}
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        if spec.name in self._specs:
            raise ValueError(f"tool already registered: {spec.name}")
        self._specs[spec.name] = spec
        self._handlers[spec.name] = handler

    def specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def spec_for(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def mcp_tool_manifest(self, *, context: str = "external_mcp") -> list[dict[str, Any]]:
        """输出 MCP tools/list 形态（只含 allowed_in 含 context 的工具）。"""
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "inputSchema": spec.input_schema,
            }
            for spec in self._specs.values()
            if context in spec.allowed_in
        ]

    def validate_arguments(self, name: str, arguments: dict[str, Any]) -> None:
        """JSON Schema 子集校验（与 mcp_server 既有语义对齐：required/类型/枚举/上限）。"""
        spec = self._specs.get(name)
        if spec is None:
            raise UnknownToolError(name)
        if not isinstance(arguments, dict):
            raise ToolValidationFailed("arguments must be an object")
        raw = json.dumps(arguments, ensure_ascii=False, default=str)
        if len(raw.encode("utf-8")) > spec.max_input_bytes:
            raise ToolValidationFailed("arguments exceed size limit")
        properties = spec.input_schema.get("properties", {})
        for key in spec.input_schema.get("required", []):
            if key not in arguments:
                raise ToolValidationFailed(f"missing required field: {key}")
        if spec.input_schema.get("additionalProperties") is False:
            for key in arguments:
                if key not in properties:
                    raise ToolValidationFailed(f"unknown field: {key}")
        for key, value in arguments.items():
            rule = properties.get(key, {})
            expected = rule.get("type")
            if expected == "string":
                if not isinstance(value, str):
                    raise ToolValidationFailed(f"{key} must be a string")
                if rule.get("minLength") and len(value.strip()) < int(rule["minLength"]):
                    raise ToolValidationFailed(f"{key} too short")
                if "maxLength" in rule and len(value) > int(rule["maxLength"]):
                    raise ToolValidationFailed(f"{key} too long")
            elif expected == "integer":
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ToolValidationFailed(f"{key} must be an integer")
                if "minimum" in rule and value < int(rule["minimum"]):
                    raise ToolValidationFailed(f"{key} below minimum")
                if "maximum" in rule and value > int(rule["maximum"]):
                    raise ToolValidationFailed(f"{key} above maximum")
            elif expected == "boolean":
                if not isinstance(value, bool):
                    raise ToolValidationFailed(f"{key} must be a boolean")
            if "enum" in rule and value not in rule["enum"]:
                raise ToolValidationFailed(f"{key} not in enum")

    def redact_arguments(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        spec = self._specs.get(name)
        if spec is None:
            return dict(arguments)
        return {key: ("[REDACTED]" if key in spec.redact_fields else value) for key, value in arguments.items()}

    def call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        principal: dict[str, Any],
        context: str,
        deadline: float | None = None,
        audit_action: str | None = None,
    ) -> dict[str, Any]:
        """统一执行入口。

        context 是通道标识（external_mcp/assist/task）；不在 allowed_in 白名单
        内直接拒绝（fail-closed）。审计 action 默认取 ``mcp_{name 短名}``，
        与 mcp_server 既有审计动作面兼容。
        """
        spec = self._specs.get(name)
        if spec is None:
            raise UnknownToolError(name)
        if context not in spec.allowed_in:
            raise ToolValidationFailed(f"tool not allowed in context: {context}")
        if _deadline_remaining(deadline) <= 0:
            raise ToolDeadlineExceeded(name)
        self.validate_arguments(name, arguments)
        if _deadline_remaining(deadline) <= 0:
            raise ToolDeadlineExceeded(name)
        scope = build_access_scope(principal)
        actor = (principal or {}).get("name") or (principal or {}).get("username") or "anonymous"
        started = time.perf_counter()
        # 轨迹脱敏：参数键集保留、敏感值替换 [REDACTED]（redact_fields）
        redacted_arguments = self.redact_arguments(name, arguments)
        try:
            result = self._handlers[name](principal, scope, arguments, deadline)
        except ToolDeadlineExceeded:
            self._record(spec, actor, "deny", error="deadline_exceeded", context=context, audit_action=audit_action, arguments=redacted_arguments)
            raise
        except ToolExecutionRejected as exc:
            # 业务级 fail-closed（flag 关闭/幂等冲突/审批未过）：记 deny 审计后
            # 原样上抛——通道层映射为工具错误结果，不与参数错误(-32602)混淆
            self._record(spec, actor, "deny", error=f"rejected:{str(exc)[:60]}", context=context, audit_action=audit_action, arguments=redacted_arguments)
            raise
        except Exception:
            self._record(spec, actor, "deny", error="handler_error", context=context, audit_action=audit_action, arguments=redacted_arguments)
            raise
        self._record(
            spec, actor, "allow", context=context, audit_action=audit_action,
            arguments=redacted_arguments,
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
        )
        return result

    def _record(
        self,
        spec: ToolSpec,
        actor: str,
        decision: str,
        *,
        context: str,
        audit_action: str | None,
        error: str | None = None,
        latency_ms: float | None = None,
        arguments: dict[str, Any] | None = None,
    ) -> None:
        action = audit_action or f"mcp_{spec.name.removeprefix('mindgraph_')}"
        metadata: dict[str, Any] = {"context": context}
        if latency_ms is not None:
            metadata["latency_ms"] = latency_ms
        if error:
            metadata["error"] = error
        if arguments is not None:
            # 只落参数键与脱敏值（小审计面）；明文长参数一律不进审计
            metadata["arguments"] = {
                key: (value if isinstance(value, (int, float, bool)) else str(value)[:64])
                for key, value in arguments.items()
            }
        try:
            record_access_audit(self.database, actor=actor, action=action, resource=f"tool/{spec.name}", decision=decision, metadata=metadata)
        except Exception:
            # 审计写入失败不吞业务结果，但必须可见
            logger.exception("tool_audit_write_failed", extra={"tool": spec.name})
