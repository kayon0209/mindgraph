"""MindGraph MCP HTTP 传输（Phase 5-3）。

企业版只读 MCP：
- POST /api/v1/mcp：JSON-RPC 2.0 请求；
- GET /api/v1/mcp/tools：工具清单；
- GET /api/v1/mcp/health：健康检查。

认证与权限：复用 API Key / Bearer 认证，按请求主体生成 access_scope。
审计：所有工具调用写入 access_audit。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

import api.auth as auth
from api.dependencies import get_container
from application.access_control import record_access_audit
from mcp_server import handle_jsonrpc

logger = logging.getLogger("mindgraph.api.mcp")
router = APIRouter(prefix="/mcp", tags=["mcp"])


async def _run_mcp_with_timeout(payload: dict[str, Any], principal: dict[str, Any] | None, container: Any):
    timeout = float(getattr(getattr(container, "settings", None), "MCP_TIMEOUT_SECONDS", 15.0))
    # 协作式 deadline：asyncio.wait_for 只能放弃等待、无法取消线程池里的调用，
    # 因此把绝对截止时刻传给工具侧，在重活前主动检查（见 mcp_server.MCPToolDeadlineExceeded）。
    deadline = time.monotonic() + timeout
    return await asyncio.wait_for(
        run_in_threadpool(handle_jsonrpc, payload, principal, deadline),
        timeout=timeout,
    )


@router.get("/health")
def mcp_health():
    return {"status": "ok", "transport": "http", "server": "mindgraph-mcp"}


@router.get("/tools")
def mcp_tools(request: Request):
    # tools/list 由 JSON-RPC 处理，这里给前端/运维一个便捷只读视图
    _ = auth.resolve_access_scope(request)
    principal = auth.get_optional_principal(request)
    response = handle_jsonrpc({"jsonrpc": "2.0", "id": "tools-list", "method": "tools/list"}, principal=principal)
    return response["result"] if response else {"tools": []}


@router.post("")
async def mcp_rpc(request: Request):
    container = get_container()
    principal = auth.get_optional_principal(request)
    scope = auth.resolve_access_scope(request)
    actor = auth.current_actor(request)
    body = await request.body()
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}, status_code=400)

    if not isinstance(payload, (dict, list)):
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}, status_code=400)

    if isinstance(payload, list):
        settings = getattr(container, "settings", None)
        max_items = int(getattr(settings, "MCP_MAX_BATCH_ITEMS", 20))
        if len(payload) > max_items:
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "batch limit exceeded"}}, status_code=400)
        results: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                results.append({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}})
                continue
            try:
                response = await _run_mcp_with_timeout(item, principal, container)
            except asyncio.TimeoutError:
                response = {"jsonrpc": "2.0", "id": item.get("id"), "error": {"code": -32000, "message": "tool timeout"}}
            if response is not None:
                results.append(response)
        record_access_audit(container.database, actor=actor, action="mcp_batch", resource="mcp", decision="allow", metadata={"count": len(results), "scope_user": (scope or {}).get("user")})
        return JSONResponse(results)

    if not isinstance(payload, dict):
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}, status_code=400)
    try:
        response = await _run_mcp_with_timeout(payload, principal, container)
    except asyncio.TimeoutError:
        response = {"jsonrpc": "2.0", "id": payload.get("id"), "error": {"code": -32000, "message": "tool timeout"}}
    if payload.get("method") == "tools/call":
        tool_name = (payload.get("params") or {}).get("name")
        record_access_audit(
            container.database,
            actor=actor,
            action="mcp_call",
            resource=f"mcp/{tool_name or 'unknown'}",
            decision="allow" if not response.get("error") else "deny",
            metadata={"scope_user": (scope or {}).get("user"), "tool": tool_name},
        )
    if response is None:
        return JSONResponse({"jsonrpc": "2.0", "id": payload.get("id"), "result": None})
    # JSON-RPC over HTTP：错误也应以 200 返回（error 语义在 JSON-RPC body 内），
    # 400 会让部分 MCP 客户端把协议错误误判为传输失败。
    return JSONResponse(response)
