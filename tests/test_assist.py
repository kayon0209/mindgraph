"""M1：Assist（受治理的 agent 交付面）测试。

覆盖：
- 默认关闭：/api/v1/assist 未挂载（404）；MCP 工具列表不含 mindgraph_assist；
- 开启后：REST 同步返回带 verdict 的信封并写 assist 审计；
- 开启后：SSE 流式与 /chat 同源（completed 终态）；
- 开启后：MCP mindgraph_assist 复用同一应用服务，返回 verdict 并写审计；
- 多面复用契约：REST Chat / Assist / MCP 全部经 ServiceContainer 应用服务，
  且 ACL+审计 fail-closed。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dependencies import override_container
from api.main import app as real_app
from api.routes.assist import router as assist_router
from application.chat_service import ChatService
from infrastructure.database import ProductDatabase
from infrastructure.settings import get_settings
from mcp_server import _tools, handle_jsonrpc
from retrieval.types import Chunk, RetrievalCandidate, RetrievalTrace


class _FakeProvider:
    provider_name = "fake"
    model_name = "fake-model"
    available = True

    def complete(self, _messages):
        return ("报销应在 10 个工作日内提交。[citation-1]", {"total_tokens": 10})

    def stream(self, _messages):
        yield {"delta": "报销应在 10 个工作日内提交。[citation-1]"}
        yield {"usage": {"total_tokens": 10}}


class _StubPipeline:
    def __init__(self) -> None:
        chunk = Chunk(
            chunk_id="policy.md::0",
            text="报销应在 10 个工作日内提交。",
            document_id="policy.md",
            chunk_index=0,
            section_path="时限",
            metadata={
                "document_title": "差旅费报销管理办法",
                "title": "差旅费报销管理办法",
                "vault_path": "policies/travel.md",
                "document_version": "v2",
                "effective_from": "2026-01-01",
                "policy_key": "travel.meal",
                "policy_status": "active",
                "owner": "财务部",
            },
        )
        self.trace = RetrievalTrace(
            query="报销时限",
            requested_strategy="hybrid",
            actual_strategy="hybrid",
            candidate_counts={"dense": 1, "final": 1},
            final_selected_chunks=[RetrievalCandidate(chunk=chunk, final_rank=1, dense_score=0.9)],
            latency_ms={"total_retrieval_ms": 2.0},
            index_version="idx-1",
            applied_filters={},
            warnings=["query_understanding:none:no_query_understanding_required"],
        )

    def retrieve(
        self,
        query,
        strategy,
        query_date=None,
        categories=None,
        include_historical=False,
        graph_enabled=False,
        graph_hops=1,
        access_scope=None,
    ):
        return self.trace


def _build_service(tmp_path: Path) -> tuple[ChatService, ProductDatabase]:
    database = ProductDatabase(tmp_path / "assist.sqlite3")
    database.initialize()
    service = ChatService(database, lambda top_k: _StubPipeline(), _FakeProvider(), privacy_log_questions=False)
    return service, database


def _mini_assist_app() -> FastAPI:
    mini = FastAPI()
    mini.include_router(assist_router, prefix="/api/v1")
    return mini


def test_assist_route_not_mounted_when_flag_off():
    """默认关闭：真实 app 上没有 /api/v1/assist 路由，请求 404。"""
    openapi_paths = set(real_app.openapi().get("paths", {}))
    assert "/api/v1/assist" not in openapi_paths
    assert "/api/v1/assist/stream" not in openapi_paths
    client = TestClient(real_app, raise_server_exceptions=False)
    try:
        resp = client.post("/api/v1/assist", json={"question": "报销时限？"})
        assert resp.status_code == 404
    finally:
        client.close()


def test_mcp_tool_hidden_when_flag_off():
    tool_names = {tool["name"] for tool in _tools()}
    assert "mindgraph_assist" not in tool_names


def _enable_flag(monkeypatch, name: str) -> None:
    monkeypatch.setenv(name, "true")
    get_settings.cache_clear()


def test_assist_sync_returns_verdict_envelope_and_audits(tmp_path: Path, monkeypatch):
    service, database = _build_service(tmp_path)
    calls: list[str] = []
    original_answer = service.answer
    service.answer = lambda request, access_scope=None: (calls.append("answer"), original_answer(request, access_scope=access_scope))[1]  # type: ignore[method-assign]
    override_container(SimpleNamespace(database=database, mindgraph_chat=service, privacy_log=False))
    client = TestClient(_mini_assist_app(), raise_server_exceptions=False)
    try:
        resp = client.post("/api/v1/assist", json={"question": "报销时限是多少天？", "retrieval_strategy": "hybrid"})
        assert resp.status_code == 200
        body = resp.json()
        # 信封契约：机器可判定 verdict + 完整答案字段
        assert body["verdict"] == "answered"
        assert body["result_state"] == "answered"
        assert body["error_code"] == "answered"
        assert body["citation_fidelity"] is True
        assert body["citations"][0]["final_rank"] == 1
        assert "citation-1" in body["answer"]
        # 复用同一应用服务（进程内调用，非 HTTP/MCP 自调用）
        assert calls == ["answer"]
        # 审计落库：action='assist'
        audit = database.fetch_all("SELECT action, resource, decision FROM access_audit WHERE action='assist'")
        assert len(audit) == 1
        assert audit[0]["resource"] == "assist"
        assert audit[0]["decision"] == "allow"
    finally:
        client.close()
        override_container(None)


def test_assist_sync_returns_timeout_verdict_when_service_exceeds_deadline(tmp_path: Path, monkeypatch):
    """通道级超时护栏：应用服务跑超 ASSIST_TIMEOUT_SECONDS 时返回 verdict=timeout
    信封（不抛 5xx）；后台任务继续跑完并落库，但结果不回传客户端。"""
    import threading
    import time as time_module

    service, database = _build_service(tmp_path)
    original_answer = service.answer
    done = threading.Event()

    def slow_answer(request, access_scope=None):
        try:
            time_module.sleep(0.3)
            return original_answer(request, access_scope=access_scope)
        finally:
            done.set()

    service.answer = slow_answer  # type: ignore[method-assign]
    override_container(SimpleNamespace(database=database, mindgraph_chat=service, privacy_log=False))

    monkeypatch.setenv("ASSIST_TIMEOUT_SECONDS", "0.05")
    get_settings.cache_clear()
    client = TestClient(_mini_assist_app(), raise_server_exceptions=False)
    try:
        resp = client.post("/api/v1/assist", json={"question": "报销时限是多少天？", "retrieval_strategy": "hybrid"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["verdict"] == "timeout"
        assert body["error_code"] == "timeout"
        assert body["result_state"] == "system_error"
        assert body["degradation_reason"] == "assist_timeout:0.05s"
        # 后台任务最终完成（避免 tmp 库位于线程写库窗口被清理）
        assert done.wait(timeout=2.0)
    finally:
        client.close()
        override_container(None)
        get_settings.cache_clear()


def test_assist_stream_reuses_chat_events_and_terminates_with_completed(tmp_path: Path):
    service, database = _build_service(tmp_path)
    override_container(SimpleNamespace(database=database, mindgraph_chat=service, privacy_log=False))
    client = TestClient(_mini_assist_app(), raise_server_exceptions=False)
    try:
        with client.stream(
            "POST",
            "/api/v1/assist/stream",
            json={"question": "报销时限是多少天？", "retrieval_strategy": "hybrid"},
        ) as response:
            assert response.status_code == 200
            events = []
            for line in response.iter_lines():
                if line.startswith("event: "):
                    events.append(line[len("event: ") :])
        assert events[0] == "request_started"
        assert events[-1] == "completed"
        assert "answer_delta" in events
        assert "citations" in events
    finally:
        client.close()
        override_container(None)


def test_mcp_assist_tool_flag_gated_and_reuses_service(tmp_path: Path, monkeypatch):
    service, database = _build_service(tmp_path)
    monkeypatch.setattr(
        "mcp_server.get_container",
        lambda: SimpleNamespace(
            database=database,
            mindgraph_chat=service,
        ),
    )
    # 关闭时：工具不存在，直呼返回未知工具（fail-closed）
    get_settings.cache_clear()
    tool_names = {tool["name"] for tool in _tools()}
    assert "mindgraph_assist" not in tool_names

    # 开启时：tools/list 暴露；tools/call 返回 verdict 并审计
    _enable_flag(monkeypatch, "ASSIST_MCP_ENABLED")
    tool_names = {tool["name"] for tool in _tools()}
    assert "mindgraph_assist" in tool_names

    principal = {"authenticated": True, "name": "assist_agent", "roles": ["read"], "departments": ["finance"]}
    response = handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": "a1",
            "method": "tools/call",
            "params": {"name": "mindgraph_assist", "arguments": {"question": "报销时限是多少天？", "retrieval_strategy": "hybrid"}},
        },
        principal=principal,
    )
    assert response is not None
    assert "error" not in response
    text = response["result"]["content"][0]["text"]
    body = json.loads(text)
    assert body["verdict"] == "answered"
    assert body["result_state"] == "answered"
    assert body["citations"]

    audit = database.fetch_all("SELECT action, decision FROM access_audit WHERE action='mcp_assist'")
    assert len(audit) == 1
    assert audit[0]["decision"] == "allow"
    get_settings.cache_clear()  # 还原默认，避免污染同进程后续用例


def test_mcp_assist_respects_acl_via_service_scope(tmp_path: Path, monkeypatch):
    """Assist 复用 ChatService.answer(access_scope=...)：scope=None（AUTH off）
    时不过滤；显式传入受限 scope 时检索管线按 ACL 裁剪（fail-closed 由
    _filter_by_access 承担，此处验证 scope 确实被传入）。"""
    service, database = _build_service(tmp_path)
    received_scopes: list = []
    original_answer = service.answer
    service.answer = lambda request, access_scope=None: (
        received_scopes.append(access_scope),
        original_answer(request, access_scope=access_scope),
    )[1]  # type: ignore[method-assign]
    monkeypatch.setattr("mcp_server.get_container", lambda: SimpleNamespace(database=database, mindgraph_chat=service))
    _enable_flag(monkeypatch, "ASSIST_MCP_ENABLED")

    handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": "a2",
            "method": "tools/call",
            "params": {"name": "mindgraph_assist", "arguments": {"question": "报销时限是多少天？"}},
        },
        principal={"authenticated": True, "name": "agent", "roles": ["read"], "departments": ["finance"]},
    )
    assert received_scopes and received_scopes[0] is not None
    assert "department:finance" in received_scopes[0]["allow"]
    get_settings.cache_clear()


def test_multi_surface_reuse_dispatch_and_audit_fail_closed(tmp_path: Path):
    """REST Chat 与 Assist 都经 ServiceContainer 的同一 mindgraph_chat 服务，
    且两通道都写 access_audit（action 不同，便于 agent 通道独立对账）。"""
    service, database = _build_service(tmp_path)
    calls: list[str] = []
    original_answer = service.answer
    service.answer = lambda request, access_scope=None: (
        calls.append("mindgraph_chat.answer"),
        original_answer(request, access_scope=access_scope),
    )[1]  # type: ignore[method-assign]

    container = SimpleNamespace(
        database=database,
        mindgraph_chat=service,
        chat=service,
        privacy_log=False,
    )
    override_container(container)

    # REST /mindgraph/chat（真实 app 上的既有通道）
    chat_client = TestClient(real_app, raise_server_exceptions=False)
    assist_client = TestClient(_mini_assist_app(), raise_server_exceptions=False)
    try:
        resp = chat_client.post(
            "/api/v1/mindgraph/chat",
            json={"question": "报销时限是多少天？", "retrieval_strategy": "hybrid"},
        )
        assert resp.status_code == 200

        resp = assist_client.post(
            "/api/v1/assist",
            json={"question": "报销时限是多少天？", "retrieval_strategy": "hybrid"},
        )
        assert resp.status_code == 200
        assert resp.json()["verdict"] == "answered"

        # 两个通道都调用同一个应用服务（进程内，无 HTTP/MCP 自调用）
        assert calls == ["mindgraph_chat.answer", "mindgraph_chat.answer"]
        actions = {row["action"] for row in database.fetch_all("SELECT DISTINCT action FROM access_audit")}
        assert {"chat", "assist"} <= actions
    finally:
        chat_client.close()
        assist_client.close()
        override_container(None)


def test_agent_stream_disabled_by_default_and_enabled_returns_events(tmp_path: Path, monkeypatch):
    """M2 通道门控：AGENT_ASSIST_ENABLED 默认关闭 → /assist/agent/stream 404；
    开启后返回 agent 事件序列（plan/tool/integrity/completed）。"""
    from api.schemas.assist import AssistRequest
    from application.agent_service import AgentService

    service, database = _build_service(tmp_path)
    agent = AgentService(service)
    override_container(SimpleNamespace(database=database, mindgraph_chat=service, agent_service=agent, privacy_log=False))
    client = TestClient(_mini_assist_app(), raise_server_exceptions=False)
    try:
        # 默认关闭：404（flag 未开）
        with client.stream(
            "POST", "/api/v1/assist/agent/stream",
            json={"question": "报销时限是多少天？", "retrieval_strategy": "hybrid"},
        ) as response:
            assert response.status_code == 404

        # 开启：事件序列包含 M2 新事件且以 completed 收尾
        monkeypatch.setenv("AGENT_ASSIST_ENABLED", "true")
        get_settings.cache_clear()
        with client.stream(
            "POST", "/api/v1/assist/agent/stream",
            json={"question": "报销时限是多少天？", "retrieval_strategy": "hybrid"},
        ) as response:
            assert response.status_code == 200
            events = [line[len("event: "):] for line in response.iter_lines() if line.startswith("event: ")]
        assert "plan_created" in events
        assert "tool_call_started" in events
        assert "citation_integrity_checked" in events
        assert events[-1] == "completed"
    finally:
        client.close()
        override_container(None)
        monkeypatch.delenv("AGENT_ASSIST_ENABLED", raising=False)
        get_settings.cache_clear()
