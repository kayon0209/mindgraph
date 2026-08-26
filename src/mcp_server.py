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

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

from api.dependencies import get_container
from application.access_control import (
    build_access_scope,
    note_acl_matches,
    record_access_audit,
)

logger = logging.getLogger("mindgraph.mcp")

JSONRPC_VERSION = "2.0"
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "mindgraph-mcp"
SERVER_VERSION = "3.1.0"
MAX_TOOL_CALLS_PER_BATCH = 20
MAX_LIST_LIMIT = 200
MAX_SEARCH_TOP_K = 20


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tools() -> list[dict[str, Any]]:
    return [
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
            },
        },
        {
            "name": "mindgraph_get_note",
            "description": "获取单篇笔记详情（含 confirmed 关系）。越权访问返回 not_found。",
            "inputSchema": {
                "type": "object",
                "properties": {"note_id": {"type": "string"}},
                "required": ["note_id"],
            },
        },
        {
            "name": "mindgraph_search",
            "description": "语义检索（只读）：返回命中的制度证据片段与引用，不生成答案。按 ACL 裁剪。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "default": 5, "minimum": 1, "maximum": MAX_SEARCH_TOP_K},
                    "strategy": {"type": "string", "enum": ["dense", "bm25", "hybrid", "hybrid_rerank"]},
                },
                "required": ["query"],
            },
        },
        {
            "name": "mindgraph_evaluation_overview",
            "description": "返回评测运行概览与最近结果（只读）。",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "mindgraph_list_relations",
            "description": "列出双端都可见的 confirmed 关系（只读）。",
            "inputSchema": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": MAX_LIST_LIMIT}},
            },
        },
    ]


def _call_tool(name: str, arguments: dict[str, Any], principal: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute a named tool with ACL-aware scope and audit."""
    container = get_container()
    database = container.database
    scope = build_access_scope(principal) if principal else None
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
        rows = database.fetch_all(
            "SELECT note_id, vault_path, title, ai_access_level, chunk_count, index_status, "
            "workspace, department, acl_json, acl_public, updated_at "
            "FROM notes ORDER BY updated_at DESC"
        )
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
            "relations": [],
        }
        _audit("mcp_get_note", f"notes/{note_id}", "allow", {"title": row.get("title")})
        return body

    if name == "mindgraph_search":
        query = arguments.get("query") or ""
        strategy = arguments.get("strategy") or "hybrid"
        top_k = min(max(int(arguments.get("top_k", 5)), 1), MAX_SEARCH_TOP_K)
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
        _audit("mcp_search", "search", "allow", {"query": query[:60], "top_k": top_k, "strategy": strategy})
        return {"query": query, "strategy": strategy, "citations": citations, "graph_enabled": False}

    if name == "mindgraph_evaluation_overview":
        rows = database.fetch_all("SELECT run_id, status, dataset_name, dataset_version, retrieval_strategy, finished_at, summary_metrics_json FROM evaluation_runs ORDER BY finished_at DESC LIMIT 20")
        _audit("mcp_evaluation_overview", "evaluation", "allow", {"runs": len(rows)})
        return {"runs": rows}

    if name == "mindgraph_list_relations":
        limit = min(max(int(arguments.get("limit", 50)), 1), MAX_LIST_LIMIT)
        rows = database.fetch_all(
            "SELECT relation_id, source_note_id, target_note_id, relation_type, confidence "
            "FROM note_relations WHERE status='confirmed' ORDER BY confidence DESC LIMIT ?",
            (limit,),
        )
        note_ids = [r["source_note_id"] for r in rows] + [r["target_note_id"] for r in rows]
        note_rows = {}
        if note_ids:
            placeholders = ",".join("?" for _ in note_ids)
            fetched = database.fetch_all(
                f"SELECT note_id, title, workspace, department, acl_json, acl_public FROM notes WHERE note_id IN ({placeholders})",
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
        _audit("mcp_list_relations", "note_relations/confirmed", "allow", {"count": len(items)})
        return {"relations": items}

    raise ValueError(f"Unknown tool: {name}")


def handle_jsonrpc(message: dict[str, Any], principal: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """处理单条 JSON-RPC 2.0 请求，返回响应 dict（通知返回 None）。"""
    method = message.get("method")
    msg_id = message.get("id")

    if method == "initialize":
        return {
            "jsonrpc": JSONRPC_VERSION,
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            },
        }

    if method == "tools/list":
        return {"jsonrpc": JSONRPC_VERSION, "id": msg_id, "result": {"tools": _tools()}}

    if method == "tools/call":
        tool_name = (message.get("params") or {}).get("name")
        arguments = (message.get("params") or {}).get("arguments") or {}
        try:
            result = _call_tool(tool_name, arguments, principal)
            return {
                "jsonrpc": JSONRPC_VERSION,
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}]},
            }
        except Exception as exc:
            logger.exception("mcp_tool_call_failed", extra={"tool": tool_name})
            return {
                "jsonrpc": JSONRPC_VERSION,
                "id": msg_id,
                "error": {"code": -32603, "message": f"tool execution failed: {exc}"},
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
    principal = {"name": env_principal, "authenticated": bool(env_principal)} if env_principal else None
    run_stdio(principal=principal)
