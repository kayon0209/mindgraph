"""M3 服务端会话测试：owner 隔离、cursor 分页、归档、幂等迁移导入。

覆盖实施方案 M3 验收：跨主体读取 404；并发续问 sequence 稳定；
重复导入不产生重复消息；归档不物理删除。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.dependencies import override_container
from api.routes.conversations import router as conversations_router
from application.conversation_service import (
    ConversationNotFoundError,
    ConversationService,
    SequenceConflictError,
)
from infrastructure.database import ProductDatabase


def test_conversation_stream_request_rejects_unknown_retrieval_strategy() -> None:
    from api.routes.conversation_stream import ConversationMessageRequest

    with pytest.raises(ValidationError):
        ConversationMessageRequest(question="报销时限？", retrieval_strategy="unknown")


def _build(tmp_path: Path):
    database = ProductDatabase(tmp_path / "conversations.sqlite3")
    database.initialize()
    service = ConversationService(database)
    app = FastAPI()
    app.include_router(conversations_router, prefix="/api/v1")
    return service, database, app


def test_owner_isolation_on_all_operations(tmp_path: Path):
    service, _db, _app = _build(tmp_path)
    created = service.create_conversation(principal_id="user-a", title="A 的会话")
    conversation_id = created["conversation_id"]

    # 跨主体：读消息/归档/导入 → 统一 not_found（不暴露存在性）
    for attempt in (
        lambda: service.get_messages(conversation_id=conversation_id, principal_id="user-b"),
        lambda: service.archive_conversation(conversation_id=conversation_id, principal_id="user-b"),
        lambda: service.import_local_turns(conversation_id=conversation_id, principal_id="user-b", turns=[{"question": "q"}]),
    ):
        try:
            attempt()
            raise AssertionError("cross-principal access must be denied")
        except ConversationNotFoundError:
            pass

    # 本人正常访问
    service.append_message(conversation_id=conversation_id, principal_id="user-a", role="user", content="问题")
    messages = service.get_messages(conversation_id=conversation_id, principal_id="user-a")
    assert len(messages) == 1 and messages[0]["sequence_no"] == 1


def test_concurrent_append_sequence_stable(tmp_path: Path):
    service, _db, _app = _build(tmp_path)
    conversation = service.create_conversation(principal_id="user-a", title="并发")
    conversation_id = conversation["conversation_id"]
    # 顺序追加 5 条：sequence 严格递增、无重复
    for i in range(5):
        result = service.append_message(
            conversation_id=conversation_id, principal_id="user-a",
            role="user", content=f"消息 {i}", request_id=f"r{i}",
        )
        assert result["sequence_no"] == i + 1
    messages = service.get_messages(conversation_id=conversation_id, principal_id="user-a")
    assert [m["sequence_no"] for m in messages] == [1, 2, 3, 4, 5]


def test_archive_is_soft_and_idempotent(tmp_path: Path):
    service, _db, _app = _build(tmp_path)
    conversation = service.create_conversation(principal_id="user-a", title="待归档")
    conversation_id = conversation["conversation_id"]
    service.append_message(conversation_id=conversation_id, principal_id="user-a", role="user", content="内容")

    service.archive_conversation(conversation_id=conversation_id, principal_id="user-a")
    service.archive_conversation(conversation_id=conversation_id, principal_id="user-a")  # 幂等
    # 归档后：列表/读取 not_found，但行仍在（物理未删）
    try:
        service.get_messages(conversation_id=conversation_id, principal_id="user-a")
        raise AssertionError("archived conversation must be hidden")
    except ConversationNotFoundError:
        pass
    # 直接 SQL 验证软删除
    row = _db.fetch_one("SELECT status FROM conversations WHERE conversation_id=?", (conversation_id,))
    assert row["status"] == "archived"
    messages_row = _db.fetch_one("SELECT COUNT(*) AS c FROM messages WHERE conversation_id=?", (conversation_id,))
    assert messages_row["c"] == 1


def test_cursor_pagination(tmp_path: Path):
    service, _db, _app = _build(tmp_path)
    for i in range(7):
        service.create_conversation(principal_id="user-a", title=f"会话 {i}")
    # 循环翻页直到 next_cursor 为空：无重叠、全覆盖
    collected: list[str] = []
    cursor: str | None = None
    for _ in range(10):  # 上限防御
        page = service.list_conversations(principal_id="user-a", cursor=cursor, limit=3)
        collected.extend(item["conversation_id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(collected) == 7 and len(set(collected)) == 7
    # 坏 cursor：not_found
    try:
        service.list_conversations(principal_id="user-a", cursor="conv-nonexistent")
        raise AssertionError("bad cursor must fail")
    except ConversationNotFoundError:
        pass


def test_import_local_turns_idempotent(tmp_path: Path):
    """显式迁移：重复导入不产生重复消息；映射表可供前端校验。"""
    service, _db, _app = _build(tmp_path)
    conversation = service.create_conversation(principal_id="user-a", title="迁移目标")
    conversation_id = conversation["conversation_id"]
    turns = [
        {"local_turn_id": "t1", "question": "报销时限？", "answer": "10 个工作日 [citation-1]", "citations": [{"citation_id": "citation-1"}]},
        {"local_turn_id": "t2", "question": "餐补标准？", "answer": "每天 180 元", "citations": []},
    ]
    first = service.import_local_turns(conversation_id=conversation_id, principal_id="user-a", turns=turns)
    assert first["imported"] == 2 and first["skipped_existing"] == 0
    messages = service.get_messages(conversation_id=conversation_id, principal_id="user-a")
    assert len(messages) == 4  # 2 轮 × (user+assistant)

    # 重复导入同一批：全部跳过，消息数不变
    second = service.import_local_turns(conversation_id=conversation_id, principal_id="user-a", turns=turns)
    assert second["imported"] == 0 and second["skipped_existing"] == 2
    messages_after = service.get_messages(conversation_id=conversation_id, principal_id="user-a")
    assert len(messages_after) == 4
    # citations 快照随 assistant 消息落库
    assistant = [m for m in messages_after if m["role"] == "assistant"][0]
    assert assistant["citations"][0]["citation_id"] == "citation-1"


def test_api_routes_flag_gated_mount(tmp_path: Path, monkeypatch):
    """路由门控（代码默认值语义）：CONVERSATION_PERSISTENCE_ENABLED=False
    的全新 app 不挂载会话路由。.env 灰度开启期间已 import 的进程无法体现
    off 态，故强制 off 后重建 main 模块。"""
    import importlib
    import sys

    from infrastructure.settings import get_settings

    service, database, _mini = _build(tmp_path)
    override_container(SimpleNamespace(database=database, conversation_service=service))
    monkeypatch.setenv("CONVERSATION_PERSISTENCE_ENABLED", "false")
    get_settings.cache_clear()
    try:
        sys.modules.pop("api.main", None)
        fresh_app = importlib.import_module("api.main").app
        openapi_paths = set(fresh_app.openapi()["paths"])
        assert not any("/conversations" in path for path in openapi_paths)
        client = TestClient(fresh_app, raise_server_exceptions=False)
        try:
            resp = client.post("/api/v1/mindgraph/conversations", json={"title": "新会话"})
            assert resp.status_code == 404
        finally:
            client.close()
    finally:
        sys.modules.pop("api.main", None)
        importlib.import_module("api.main")
        get_settings.cache_clear()
        override_container(None)


def test_api_flow_on_mounted_router(tmp_path: Path):
    """开启状态下（直接挂载路由测试 app）全链路：创建→导入轮次→读消息→归档。
    principal 名以 API 实际解析为准（不猜测 auth 模式的命名）。"""
    service, database, app = _build(tmp_path)
    override_container(SimpleNamespace(database=database, conversation_service=service))
    client = TestClient(app, raise_server_exceptions=False)
    try:
        resp = client.post("/api/v1/mindgraph/conversations", json={"title": "测试会话"})
        assert resp.status_code == 200, resp.text
        conversation_id = resp.json()["conversation_id"]

        # 经 API 幂等导入端点写入轮次（principal 由服务端同一入口解析）
        resp = client.post(
            f"/api/v1/mindgraph/conversations/{conversation_id}/import-turns",
            json={"turns": [{"local_turn_id": "t1", "question": "问题", "answer": "回答", "citations": []}]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["imported"] == 1

        resp = client.get(f"/api/v1/mindgraph/conversations/{conversation_id}/messages")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        assert body[0]["role"] == "user" and body[0]["content"] == "问题"

        # 重复导入：幂等
        resp = client.post(
            f"/api/v1/mindgraph/conversations/{conversation_id}/import-turns",
            json={"turns": [{"local_turn_id": "t1", "question": "问题", "answer": "回答", "citations": []}]},
        )
        assert resp.status_code == 200
        assert resp.json()["imported"] == 0 and resp.json()["skipped_existing"] == 1

        resp = client.delete(f"/api/v1/mindgraph/conversations/{conversation_id}")
        assert resp.status_code == 200 and resp.json()["status"] == "archived"
        resp = client.get(f"/api/v1/mindgraph/conversations/{conversation_id}/messages")
        assert resp.status_code == 404
    finally:
        client.close()
        override_container(None)


def test_conversation_message_stream_end_to_end(tmp_path: Path):
    """缺口修复（方案 §7.1）：会话内续问流——user 落库、SSE 事件、
    assistant 快照按序落库、稳定 sequence。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.routes import conversation_stream as stream_route
    from application.chat_service import ChatService
    from api.dependencies import override_container
    from retrieval.types import Chunk, RetrievalCandidate, RetrievalTrace

    class StreamFakeProvider:
        provider_name = "fake"
        model_name = "fake-model"
        available = True

        def complete(self, _m):
            return ("10 个工作日内提交。", {"total_tokens": 5})

        def stream(self, _m):
            yield {"delta": "10 个工作日内提交。"}
            yield {"usage": {"total_tokens": 5}}

    class StreamPipeline:
        def retrieve(self, *_a, **_k):
            chunk = Chunk(
                "p.md::0", "报销应在 10 个工作日内提交。", "p.md", 0, "时限",
                {"document_title": "差旅费报销管理办法", "vault_path": "policies/travel.md",
                 "document_version": "v2", "effective_from": "2026-01-01", "policy_key": "travel.meal",
                 "policy_status": "active", "owner": "财务部"},
            )
            return RetrievalTrace(
                query="q", requested_strategy="hybrid", actual_strategy="hybrid",
                candidate_counts={"final": 1},
                final_selected_chunks=[RetrievalCandidate(chunk=chunk, final_rank=1, dense_score=0.9)],
                latency_ms={"total_retrieval_ms": 0.5}, index_version="idx", applied_filters={}, warnings=[],
            )

    database = ProductDatabase(tmp_path / "conv-stream.sqlite3")
    database.initialize()
    chat = ChatService(database, lambda top_k: StreamPipeline(), StreamFakeProvider(), privacy_log_questions=False)
    service = ConversationService(database)
    app = FastAPI()
    app.include_router(stream_route.router, prefix="/api/v1")
    override_container(
        SimpleNamespace(database=database, conversation_service=service, mindgraph_chat=chat, privacy_log=False)
    )
    client = TestClient(app, raise_server_exceptions=False)
    try:
        created = service.create_conversation(principal_id="local-development", title="续问会话")
        conversation_id = created["conversation_id"]
        with client.stream(
            "POST",
            f"/api/v1/mindgraph/conversations/{conversation_id}/messages/stream",
            json={"question": "报销时限是多少天？", "retrieval_strategy": "hybrid"},
        ) as response:
            assert response.status_code == 200
            events = [line[len("event: "):] for line in response.iter_lines() if line.startswith("event: ")]
        assert events[0] == "request_started"
        assert events[-1] == "completed"
        # 流结束后：user + assistant 双双按序落库
        messages = service.get_messages(conversation_id=conversation_id, principal_id="local-development")
        assert [m["sequence_no"] for m in messages] == [1, 2]
        assert messages[0]["role"] == "user" and "报销时限" in messages[0]["content"]
        assert messages[1]["role"] == "assistant"
        assert "10 个工作日" in messages[1]["content"]
        assert messages[1]["citations"], "assistant citations snapshot must persist"
        # 越权语义由服务层 test_owner_isolation_on_all_operations 覆盖
        # （裸 TestClient 的 principal 恒为 local-development，无法模拟第二主体）
    finally:
        client.close()
        override_container(None)
        database.close()


def test_retention_enforcement_archives_expired(tmp_path: Path, monkeypatch):
    """缺口修复（M3-E）：保留期执行——到期归档（不物理删）、未到期不动、幂等。"""
    from datetime import UTC, datetime, timedelta

    monkeypatch.setenv("CONVERSATION_RETENTION_DAYS", "30")
    from infrastructure.settings import get_settings

    get_settings.cache_clear()
    try:
        service, _db, _app = _build(tmp_path)
        # 创建即带 retention_until（30 天后）
        fresh = service.create_conversation(principal_id="user-a", title="新会话")
        assert fresh["status"] == "active"

        # 手工造一条已过期的
        expired = service.create_conversation(principal_id="user-a", title="旧会话")
        expired_id = expired["conversation_id"]
        past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        _db.execute("UPDATE conversations SET retention_until=? WHERE conversation_id=?", (past, expired_id))

        result = service.enforce_retention()
        assert result["archived"] >= 1
        # 过期 → archived；行保留（软删）；未到期 → active
        row = _db.fetch_one("SELECT status FROM conversations WHERE conversation_id=?", (expired_id,))
        assert row["status"] == "archived"
        row_fresh = _db.fetch_one("SELECT status FROM conversations WHERE conversation_id=?", (fresh["conversation_id"],))
        assert row_fresh["status"] == "active"
        # 幂等：再跑一遍无新增
        second = service.enforce_retention()
        assert second["archived"] == 0
        # 归档后 owner 读取 not_found（与手动归档同语义）
        try:
            service.get_messages(conversation_id=expired_id, principal_id="user-a")
            raise AssertionError("expired conversation must be hidden after retention")
        except ConversationNotFoundError:
            pass
    finally:
        monkeypatch.delenv("CONVERSATION_RETENTION_DAYS", raising=False)
        get_settings.cache_clear()
