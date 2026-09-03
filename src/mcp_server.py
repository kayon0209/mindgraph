"""MCP（Model Context Protocol）服务（Phase 5-3）。

分两层：
- 早期本地 stdio MCP：仅用于开发者本地调试，每个工具默认只读；
- 企业 HTTP MCP：挂载于 /api/v1/mcp，走 API Key 认证 + ACL + 审计 + 速率限制。

实现原则（对齐 Phase 5）：
- MCP 是 MindGraph 的交付通道，不是护城河；
- 企业护城河来自可靠的知识治理、证据链和权限正确性；
- 只读工具先行，写入工具待 ACL/审计完整后再开放。

采用自包含 JSON-RPC 2.0 实现（无 mcp SDK 依赖），保持本地优先 / 离线安全。
"""
from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
import os
import sys
import time
from typing import Any
import uuid

from api.dependencies import get_container
from application.access_control import (
    build_access_scope,
    note_acl_matches,
    record_access_audit,
)

logger = logging.getLogger("mindgraph.mcp")

JSONRPC_VERSION = "2.0"
# MCP 协议版本支持集（M6-1，ADR-005）：2024-11-05 起的三个 stdio 稳定修订。
# 2025-06-18 后的 "modern era" 修订改用 server/discover 握手、不经 initialize，
# 不在本支持集（TS SDK 明确 initialize 不接受/不回 modern 版本）。
MCP_SUPPORTED_VERSIONS: tuple[str, ...] = ("2024-11-05", "2025-03-26", "2025-06-18")
# 服务器回退版本：客户端请求的版本不在支持集时，回我们支持的最新修订
# （规范允许服务器回自己的版本；客户端不接受则断开）
PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "mindgraph-mcp"
SERVER_VERSION = "3.2.0"
MAX_TOOL_CALLS_PER_BATCH = 20
MAX_LIST_LIMIT = 200
MAX_SEARCH_TOP_K = 20


class InvalidToolArguments(ValueError):
    pass


class MCPAuthenticationRequired(PermissionError):
    pass


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat()


class MCPToolDeadlineExceeded(TimeoutError):
    """协作式超时：工具在执行重活前检查 deadline，超时即主动让出线程。

    背景：HTTP 传输层的 ``asyncio.wait_for`` 只能放弃等待，无法取消已经
    进入线程池的调用——反复超时会占满 anyio 线程池拖垮整个 API。工具侧
    主动检查 deadline 才能真正及时停手。
    """


def _deadline_remaining(deadline: float | None = None) -> float:
    if deadline is None:
        return float("inf")
    return deadline - time.monotonic()


def _assist_mcp_enabled() -> bool:
    """Assist MCP 工具开关（默认关；启动期读 .env，运行时经 get_settings 缓存）。"""
    from infrastructure.settings import get_settings

    return bool(get_settings().ASSIST_MCP_ENABLED)


def _evidence_registry():
    """获取容器内共享的 EvidenceToolRegistry（M1 起三个只读工具的统一执行面）。"""
    container = get_container()
    registry = getattr(container, "evidence_tool_registry", None)
    return registry


def _registry_tools() -> list[dict[str, Any]]:
    """来自共享 registry 的 MCP 工具清单（M1 三个只读治理工具；M5-A 写工具
    按各自独立开关暴露——tools/list 过滤与 handler 内 fail-closed 校验双保险）：
    save_artifact ← AGENT_WRITE_TOOLS_ENABLED；
    submit_evidence_feedback ← AGENT_FEEDBACK_TOOL_ENABLED。"""
    registry = _evidence_registry()
    if registry is None:
        return []
    from infrastructure.settings import get_settings

    settings = get_settings()
    write_enabled = bool(settings.AGENT_WRITE_TOOLS_ENABLED)
    feedback_enabled = bool(settings.AGENT_FEEDBACK_TOOL_ENABLED)
    propose_enabled = bool(settings.AGENT_PROPOSE_RELATION_TOOL_ENABLED)
    write_flags = {
        "mindgraph_save_artifact": write_enabled,
        "mindgraph_submit_evidence_feedback": feedback_enabled,
        "mindgraph_propose_relation": propose_enabled,
    }
    manifest: list[dict[str, Any]] = []
    for tool in registry.mcp_tool_manifest(context="external_mcp"):
        spec = registry.spec_for(tool["name"])
        if spec is None:
            continue
        if spec.mode == "write" and not write_flags.get(tool["name"], False):
            continue
        manifest.append(tool)
    return manifest


def _assist_max_top_k() -> int:
    """Assist 通道的 top_k 上限（单一数据源：settings.ASSIST_MAX_TOP_K）。"""
    from infrastructure.settings import get_settings

    value = get_settings().ASSIST_MAX_TOP_K
    return max(1, int(value))


def _tools() -> list[dict[str, Any]]:
    tools = [
        {
            "name": "mindgraph_list_notes",
            "description": "列出当前主体有权访问的笔记（台账）。按 workspace/department ACL 裁剪。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "q": {"type": "string", "description": "标题/路径关键词"},
                    "workspace": {"type": "string"},
                    "department": {"type": "string"},
                    "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": MAX_LIST_LIMIT},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "mindgraph_get_note",
            "description": "获取单篇笔记详情（含 confirmed 关系）。越权访问返回 not_found。",
            "inputSchema": {
                "type": "object",
                "properties": {"note_id": {"type": "string", "minLength": 1}},
                "required": ["note_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "mindgraph_search",
            "description": "语义检索（只读）：返回命中的制度证据片段与引用，不生成答案。按 ACL 裁剪。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "top_k": {"type": "integer", "default": 5, "minimum": 1, "maximum": MAX_SEARCH_TOP_K},
                    "strategy": {"type": "string", "enum": ["dense", "bm25", "hybrid", "hybrid_rerank"]},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "mindgraph_evaluation_overview",
            "description": "返回评测运行概览与最近结果（只读）。",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "mindgraph_list_relations",
            "description": "列出双端都可见的 confirmed 关系（只读）。",
            "inputSchema": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": MAX_LIST_LIMIT}},
                "additionalProperties": False,
            },
        },
    ]
    # M1：Assist 只读工具（默认关闭，见 settings.ASSIST_MCP_ENABLED）——
    # 复用同一应用服务，审计/ACL 与 REST Assist 一致。
    if _assist_mcp_enabled():
        tools.append({
            "name": "mindgraph_assist",
            "description": "受治理的只读问答（Assist）：复用与 /assist 相同的应用服务，返回机器可判定 verdict。不写回任何数据。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "retrieval_strategy": {
                        "type": "string", "default": "auto",
                        "enum": ["auto", "dense", "bm25", "hybrid", "hybrid_rerank"],
                    },
                    "final_top_k": {"type": "integer", "default": 5, "minimum": 1, "maximum": _assist_max_top_k()},
                    "query_date": {"type": "string", "description": "YYYY-MM-DD；缺省按今天判定版本时效"},
                    "include_historical": {"type": "boolean", "default": False},
                },
                "required": ["question"],
                "additionalProperties": False,
            },
        })
    # M1：共享 EvidenceToolRegistry 暴露的只读治理工具（policy 版本族/
    # 概念缺口/引用完整性）。旧 5 工具保持原样；新工具经 registry 统一
    # 执行（ACL/审计/deadline/脱敏），mcp_server 只做 envelope 映射。
    tools.extend(_registry_tools())
    return tools


def _validate_tool_arguments(name: object, arguments: object) -> tuple[str, dict[str, Any]]:
    if not isinstance(name, str):
        raise InvalidToolArguments
    tool = next((item for item in _tools() if item["name"] == name), None)
    if tool is None or not isinstance(arguments, dict):
        raise InvalidToolArguments

    schema = tool["inputSchema"]
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if any(key not in arguments for key in required):
        raise InvalidToolArguments
    if schema.get("additionalProperties") is False and any(key not in properties for key in arguments):
        raise InvalidToolArguments

    for key, value in arguments.items():
        rule = properties[key]
        expected_type = rule.get("type")
        if expected_type == "string":
            if not isinstance(value, str):
                raise InvalidToolArguments
            if rule.get("minLength") and len(value.strip()) < int(rule["minLength"]):
                raise InvalidToolArguments
            if "maxLength" in rule and len(value) > int(rule["maxLength"]):
                raise InvalidToolArguments
        elif expected_type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise InvalidToolArguments
            if "minimum" in rule and value < int(rule["minimum"]):
                raise InvalidToolArguments
            if "maximum" in rule and value > int(rule["maximum"]):
                raise InvalidToolArguments
        if "enum" in rule and value not in rule["enum"]:
            raise InvalidToolArguments
    return name, arguments


def _call_tool(
    name: str,
    arguments: dict[str, Any],
    principal: dict[str, Any] | None = None,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Execute a named tool with ACL-aware scope and audit.

    ``deadline`` 为 ``time.monotonic()`` 绝对时刻；工具在重活前主动检查，
    超时抛 MCPToolDeadlineExceeded（HTTP 层映射为 -32000 tool timeout）。
    """
    if principal is None:
        raise MCPAuthenticationRequired
    if _deadline_remaining(deadline) <= 0:
        raise MCPToolDeadlineExceeded
    container = get_container()
    database = container.database
    scope = build_access_scope(principal)
    actor = (principal or {}).get("name") or (principal or {}).get("username") or "anonymous"
    request_id = uuid.uuid4().hex

    def _audit(action: str, resource: str, decision: str, metadata: dict[str, Any] | None = None, reason: str | None = None) -> None:
        record_access_audit(
            database,
            actor=actor,
            action=action,
            resource=resource,
            decision=decision,
            reason=reason,
            metadata=metadata or {},
            request_id=request_id,
        )

    if name == "mindgraph_list_notes":
        limit = min(max(int(arguments.get("limit", 50)), 1), MAX_LIST_LIMIT)
        q = arguments.get("q")
        # 公共快路径：public_only 主体只能看到公开笔记，SQL 预过滤 acl_public。
        public_only = scope is not None and scope.get("public_only")
        sql = (
            "SELECT note_id, vault_path, title, ai_access_level, chunk_count, index_status, "
            "workspace, department, acl_json, acl_public, updated_at "
            "FROM notes"
        )
        if public_only:
            sql += " WHERE acl_public = 1"
        sql += " ORDER BY updated_at DESC"
        rows = database.fetch_all(sql)
        if q:
            rows = [r for r in rows if q.lower() in (r["title"] or "").lower() or q.lower() in r["vault_path"].lower()]
        visible = [r for r in rows if note_acl_matches(r, scope)]
        items = [
            {
                "id": r["note_id"],
                "title": r["title"],
                "vault_path": r["vault_path"],
                "workspace": r.get("workspace"),
                "department": r.get("department"),
                "status": r["index_status"],
                "chunk_count": r["chunk_count"],
            }
            for r in visible[:limit]
        ]
        _audit("mcp_list_notes", "notes", "allow", {"matched": len(items)})
        return {"items": items, "total": len(visible)}

    if name == "mindgraph_get_note":
        note_id = arguments.get("note_id")
        if not note_id:
            raise ValueError("note_id is required")
        row = database.fetch_one(
            "SELECT note_id, vault_path, title, ai_access_level, chunk_count, index_status, "
            "workspace, department, acl_json, acl_public, frontmatter_json "
            "FROM notes WHERE note_id=?",
            (note_id,),
        )
        if not row:
            _audit("mcp_get_note", f"notes/{note_id}", "deny", {"reason": "not_found"})
            return {"error": "note not found"}
        if not note_acl_matches(row, scope):
            _audit("mcp_get_note", f"notes/{note_id}", "deny", {"reason": "acl"})
            return {"error": "note not found"}
        relations: list[dict[str, Any]] = []
        body = {
            "note": row,
            "governance": {
                "policy_key": row.get("policy_key"),
                "owner": row.get("owner"),
                "version": row.get("document_version"),
                "effective_from": row.get("effective_from"),
                "effective_to": row.get("effective_to"),
                "policy_status": row.get("policy_status"),
            },
            "relations": relations,
        }
        # 工具描述承诺"含 confirmed 关系"——真正返回（ACL 双端校验 + 治理元数据），
        # 与 REST /mindgraph/notes/{id} 的行为对齐。
        store = container.mindgraph_graph_store
        outgoing = store.related_note_ids([note_id], status="confirmed", access_scope=scope)
        titles = store.note_titles([o["target_note_id"] for o in outgoing])
        body["relations"] = [
            {
                "target_id": o["target_note_id"],
                "target_title": titles.get(o["target_note_id"], o["target_note_id"]),
                "relation_type": o["relation_type"],
                "confidence": o["confidence"],
                "evidence_chunk_id": o.get("evidence_chunk_id"),
                "source_document_version": o.get("source_document_version"),
                "effective_from": o.get("effective_from"),
                "effective_to": o.get("effective_to"),
            }
            for o in outgoing
        ]
        _audit("mcp_get_note", f"notes/{note_id}", "allow", {"title": row.get("title")})
        return body

    if name == "mindgraph_search":
        query = arguments.get("query") or ""
        strategy = arguments.get("strategy") or "hybrid"
        top_k = min(max(int(arguments.get("top_k", 5)), 1), MAX_SEARCH_TOP_K)
        if _deadline_remaining(deadline) <= 0:
            raise MCPToolDeadlineExceeded
        # 审查收敛（红线 3，方案 §3.1 原始要求）：search 复用共享 EvidenceQueryService
        # 检索段，不再维护独立检索分支；响应形状经 citations 转换保持与旧契约
        # 逐字段一致（旧 5 工具兼容测试锁定）。容器缺 mindgraph_chat（mock/极简
        # 部署）时回退直接检索——行为等价，不作为"未知工具"失败。
        from application.evidence_query_service import EvidenceQueryService
        from domain.models import ChatRequest as _ChatRequest

        chat_service = getattr(container, "mindgraph_chat", None)
        if chat_service is not None:
            evidence_service = EvidenceQueryService(chat_service)
            request = _ChatRequest(question=query, retrieval_strategy=strategy, final_top_k=top_k)
            result = evidence_service.query(request, access_scope=scope, excerpt_limit=400)
            citations = [
                {
                    "citation_id": item.citation_id,
                    "document_id": item.document_id,
                    "document_name": item.document_name,
                    "chunk_id": item.chunk_id,
                    "section_path": item.section_path,
                    "excerpt": item.excerpt,
                    "final_rank": item.final_rank,
                    "retrieval_score": item.retrieval_score,
                    "document_version": item.document_version,
                    "owner": item.owner,
                    "effective_from": item.effective_from,
                    "effective_to": item.effective_to,
                    "policy_status": item.policy_status,
                    "policy_key": item.policy_key,
                    "authority_level": item.authority_level,
                    "vault_path": item.vault_path,
                }
                for item in result.citations[:top_k]
            ]
        else:
            pipeline = container.mindgraph_pipeline(top_k=top_k, graph_enabled=False)
            trace = pipeline.retrieve(query, strategy, access_scope=scope)
            citations = []
            for candidate in trace.final_selected_chunks[:top_k]:
                citations.append({
                    "citation_id": candidate.chunk.chunk_id,
                    "document_id": candidate.chunk.document_id,
                    "document_name": candidate.chunk.metadata.get("title") or candidate.chunk.document_id,
                    "chunk_id": candidate.chunk.chunk_id,
                    "section_path": candidate.chunk.section_path,
                    "excerpt": candidate.chunk.text[:400],
                    "final_rank": candidate.final_rank,
                    "retrieval_score": candidate.rrf_score,
                    "document_version": candidate.chunk.metadata.get("document_version"),
                    "owner": candidate.chunk.metadata.get("owner"),
                    "effective_from": candidate.chunk.metadata.get("effective_from"),
                    "effective_to": candidate.chunk.metadata.get("effective_to"),
                    "policy_status": candidate.chunk.metadata.get("policy_status"),
                    "policy_key": candidate.chunk.metadata.get("policy_key"),
                    "authority_level": candidate.chunk.metadata.get("ai_access_level"),
                    "vault_path": candidate.chunk.metadata.get("vault_path"),
                })
        _audit("mcp_search", "search", "allow", {"query_len": len(query), "top_k": top_k, "strategy": strategy})
        return {"query": query, "strategy": strategy, "citations": citations, "graph_enabled": False}

    if name == "mindgraph_evaluation_overview":
        rows = database.fetch_all("SELECT run_id, status, dataset_name, dataset_version, retrieval_strategy, finished_at, summary_metrics_json FROM evaluation_runs ORDER BY finished_at DESC LIMIT 20")
        _audit("mcp_evaluation_overview", "evaluation", "allow", {"runs": len(rows)})
        return {"runs": rows}

    if name == "mindgraph_list_relations":
        limit = min(max(int(arguments.get("limit", 50)), 1), MAX_LIST_LIMIT)
        rows = database.fetch_all(
            "SELECT relation_id, source_note_id, target_note_id, relation_type, confidence "
            "FROM note_relations WHERE status='confirmed' ORDER BY confidence DESC",
        )
        note_ids = sorted({r["source_note_id"] for r in rows} | {r["target_note_id"] for r in rows})
        note_rows = {}
        if note_ids:
            placeholders = ",".join("?" for _ in note_ids)
            fetched = database.fetch_all(
                f"SELECT note_id, title, workspace, department, acl_json, acl_public FROM notes WHERE note_id IN ({placeholders})",  # nosec B608 -- placeholders 仅由常量 '?' 拼接
                tuple(note_ids),
            )
            note_rows = {r["note_id"]: r for r in fetched}
        items = []
        for r in rows:
            s = note_rows.get(r["source_note_id"])
            t = note_rows.get(r["target_note_id"])
            if not s or not t or not note_acl_matches(s, scope) or not note_acl_matches(t, scope):
                continue
            items.append({
                "id": r["relation_id"],
                "source": s["title"],
                "target": t["title"],
                "type": r["relation_type"],
                "confidence": r["confidence"],
            })
            if len(items) >= limit:
                break
        _audit("mcp_list_relations", "note_relations/confirmed", "allow", {"count": len(items)})
        return {"relations": items}

    if name == "mindgraph_assist":
        # flag 双重校验：工具列表已按 ASSIST_MCP_ENABLED 过滤，调用侧再校验一次
        # （fail-closed：即使绕过 tools/list 直呼，未开启也拒绝执行）。
        if not _assist_mcp_enabled():
            raise ValueError(f"Unknown tool: {name}")
        if _deadline_remaining(deadline) <= 0:
            raise MCPToolDeadlineExceeded
        from domain.models import ChatRequest

        question = arguments.get("question") or ""
        if not question.strip():
            raise InvalidToolArguments
        request = ChatRequest(
            question=question,
            retrieval_strategy=arguments.get("retrieval_strategy") or "auto",
            final_top_k=min(max(int(arguments.get("final_top_k", 5)), 1), _assist_max_top_k()),
            query_date=arguments.get("query_date"),
            include_historical=bool(arguments.get("include_historical", False)),
        )
        container = get_container()
        chat_service = getattr(container, "mindgraph_chat", None)
        if chat_service is None:
            raise ValueError(f"Unknown tool: {name}")
        result = chat_service.answer(request, access_scope=scope)
        _audit(
            "mcp_assist",
            "assist",
            "allow",
            {
                "scope_user": (scope or {}).get("user"),
                "result_state": result.result_state.value,
                "verdict": result.error_code.value if result.error_code else result.result_state.value,
                "citations": len(result.citations),
            },
        )
        return {
            "request_id": result.request_id,
            "verdict": result.error_code.value if result.error_code else result.result_state.value,
            "result_state": result.result_state.value,
            "question": result.question,
            "answer": result.answer,
            "citations": [item.model_dump(mode="json") for item in result.citations],
            "degraded": result.degraded,
            "model": result.model,
            "actual_strategy": result.actual_strategy,
            "index_version": result.index_version,
        }

    # M1/M5-A：共享 EvidenceToolRegistry 的治理工具——统一执行面
    # （principal→ACL→参数校验→deadline→handler→审计→脱敏），本函数
    # 只做 MCP envelope 映射，不再写业务分支。
    registry = _evidence_registry()
    if registry is not None and registry.spec_for(name) is not None:
        from application.evidence_tools.registry import (
            ToolDeadlineExceeded,
            ToolExecutionRejected,
            ToolValidationFailed,
        )

        try:
            tool_result: dict[str, Any] = registry.call(
                name, arguments,
                principal=principal,
                context="external_mcp",
                deadline=deadline,
            )
            return tool_result
        except ToolValidationFailed as exc:
            raise InvalidToolArguments(str(exc)) from exc
        except ToolExecutionRejected:
            # 业务级 fail-closed（flag 关闭/幂等冲突/审批未过）：以工具级
            # 错误结果上抛（JSON-RPC -32603 通道），不与参数错误混淆
            raise
        except ToolDeadlineExceeded as exc:
            raise MCPToolDeadlineExceeded(name) from exc

    raise ValueError(f"Unknown tool: {name}")


def handle_jsonrpc(
    message: dict[str, Any],
    principal: dict[str, Any] | None = None,
    deadline: float | None = None,
) -> dict[str, Any] | None:
    """处理单条 JSON-RPC 2.0 请求，返回响应 dict（通知返回 None）。

    ``deadline``（time.monotonic 时刻）用于协作式超时：工具执行前检查剩余
    时间，超时返回 -32000，而不是让线程池里的调用无限期占线。
    """
    method = message.get("method")
    msg_id = message.get("id")

    if method == "initialize":
        # 版本协商（M6-1，ADR-005）：客户端在支持集内 → echo 其请求版本；
        # 否则回退到我们支持的最新修订。与官方 SDK 协商行为对齐。
        requested = str(((message.get("params") or {}).get("protocolVersion")) or "")
        negotiated = requested if requested in MCP_SUPPORTED_VERSIONS else PROTOCOL_VERSION
        return {
            "jsonrpc": JSONRPC_VERSION,
            "id": msg_id,
            "result": {
                "protocolVersion": negotiated,
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            },
        }

    if method == "tools/list":
        return {"jsonrpc": JSONRPC_VERSION, "id": msg_id, "result": {"tools": _tools()}}

    if method == "tools/call":
        params = message.get("params")
        try:
            if principal is None:
                raise MCPAuthenticationRequired
            if not isinstance(params, dict):
                raise InvalidToolArguments
            raw_arguments = params.get("arguments", {})
            tool_name, arguments = _validate_tool_arguments(params.get("name"), raw_arguments)
            result = _call_tool(tool_name, arguments, principal, deadline=deadline)
            return {
                "jsonrpc": JSONRPC_VERSION,
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}]},
            }
        except MCPToolDeadlineExceeded:
            tool_name = params.get("name") if isinstance(params, dict) else None
            logger.warning("mcp_tool_deadline_exceeded", extra={"tool": tool_name})
            return {
                "jsonrpc": JSONRPC_VERSION,
                "id": msg_id,
                "error": {"code": -32000, "message": "tool timeout"},
            }
        except MCPAuthenticationRequired:
            return {
                "jsonrpc": JSONRPC_VERSION,
                "id": msg_id,
                "error": {"code": -32001, "message": "authentication required"},
            }
        except InvalidToolArguments:
            return {
                "jsonrpc": JSONRPC_VERSION,
                "id": msg_id,
                "error": {"code": -32602, "message": "invalid tool arguments"},
            }
        except Exception:
            tool_name = params.get("name") if isinstance(params, dict) else None
            logger.exception("mcp_tool_call_failed", extra={"tool": tool_name})
            return {
                "jsonrpc": JSONRPC_VERSION,
                "id": msg_id,
                "error": {"code": -32603, "message": "tool execution failed"},
            }

    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": msg_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def run_stdio(principal: dict[str, Any] | None = None) -> None:
    """本地 stdio MCP 传输（开发者本地调试用）。

    从 stdin 逐行读取 JSON-RPC 请求，向 stdout 写出响应。
    principal 可通过环境变量 MCP_PRINCIPAL 注入（仅用于本地调试）。
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    logger.info("mindgraph_mcp_stdio_started", extra={"principal": (principal or {}).get("name")})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps({"jsonrpc": JSONRPC_VERSION, "id": None, "error": {"code": -32700, "message": "parse error"}}) + "\n")
            sys.stdout.flush()
            continue
        response = handle_jsonrpc(message, principal=principal)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    env_principal = os.getenv("MCP_PRINCIPAL")
    # 本地调试主体的角色注入（逗号分隔，如 "admin" 或 "read,finance"）：
    # 无角色主体的 allow/deny 均空 → build_access_scope 视为受限 scope
    # （私有内容不可见）；smoke/联调用 MCP_PRINCIPAL_ROLES 显式提权。
    # 加固（审查）：企业模式（AUTH_MODE≠off）下注入提权角色属于运维失误，
    # 显式告警（不阻断——环境变量可控性即本机信任边界，见 DEPLOYMENT-ops.md）。
    env_roles = [item.strip() for item in os.getenv("MCP_PRINCIPAL_ROLES", "").split(",") if item.strip()]
    if env_roles and os.getenv("AUTH_MODE", "demo") != "off":
        logging.getLogger("mindgraph.mcp").warning(
            "mcp_principal_roles_injected_under_auth",
            extra={"roles": env_roles, "hint": "MCP_PRINCIPAL_ROLES 只应用于本地调试；企业部署请移除"},
        )
    principal = None
    if env_principal:
        principal = {"name": env_principal, "authenticated": bool(env_principal)}
        if env_roles:
            principal["roles"] = env_roles
    run_stdio(principal=principal)
