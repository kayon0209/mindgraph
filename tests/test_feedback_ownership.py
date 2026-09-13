"""PR-02 反馈归属安全：REST /feedback 的跨主体写入修复。

任务书测试矩阵：
1. owner 提交成功；
2. 其他 principal 对已存在和不存在 request_id 获得相同外部语义（404，不暴露存在性）；
3. 重复反馈仍幂等/冲突（409）；
4. 日志不输出问题正文。

现场核对补充：
- assist 渠道（agent_service._persist_assist）必须补写 principal_id，
  否则 assist 回答的反馈会被 fail-closed 全拒（写入端/查询端不对称）；
- AUTH_MODE=off 下问答写入端与反馈端使用同一 actor 口径（统一经路由传递）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from api.dependencies import override_container
from api.routes import feedback as feedback_route
from application.feedback_service import FeedbackService
from infrastructure.database import ProductDatabase


def _seed_query_log(
    db: ProductDatabase, request_id: str, owner: str, *, answer: str = "30 个自然日内提交。"
) -> None:
    db.execute(
        "INSERT INTO query_logs (request_id, question, question_hash, answer, result_state, requested_strategy,"
        " actual_strategy, trace_json, citations_json, timing_json, usage_json, created_at, principal_id)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (request_id, "报销时限是多少天？", "hash-1", answer, "answered", "hybrid", "hybrid",
         "{}", "[]", "{}", "{}", "2026-09-03T00:00:00", owner),
    )


def _seed_null_owner(db: ProductDatabase, request_id: str) -> None:
    """存量 NULL 归属记录（agent_service 修复前写入的 assist 轮）。"""
    db.execute(
        "INSERT INTO query_logs (request_id, question, question_hash, answer, result_state, requested_strategy,"
        " actual_strategy, trace_json, citations_json, timing_json, usage_json, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (request_id, "报销时限是多少天？", "hash-2", "旧回答", "answered", "hybrid", "hybrid",
         "{}", "[]", "{}", "{}", "2026-09-01T00:00:00"),
    )


@pytest.fixture
def make_client(monkeypatch: pytest.MonkeyPatch):
    """最小反馈路由 client 工厂；actor 经 monkeypatch 注入（自动回滚）。

    两个必须遵守的注入事实（PR-02 任务书修正 4）：
    - 路由用 ``from api.auth import current_actor`` 名字级导入，patch 目标
      必须是路由模块自身的绑定，patch 源模块无效；
    - 不能在 try/finally 里 return client——finally 会先于调用方使用就撤销
      patch，导致请求期间用的是真实 actor（假通过陷阱）。
    生产错误语义由主应用 exception handlers 承载，mini app 必须注册同一
    handler，否则 NotFoundError 会变成 500 而非 404。
    """
    from api.exception_handlers import product_error_handler
    from domain.errors import ProductError

    def _make(db: ProductDatabase, actor: str = "user-a") -> TestClient:
        override_container(SimpleNamespace(database=db, feedback=FeedbackService(db)))
        monkeypatch.setattr(feedback_route, "current_actor", lambda request: actor)
        app = FastAPI()
        app.add_exception_handler(ProductError, product_error_handler)  # type: ignore[arg-type]
        app.include_router(feedback_route.router, prefix="/api/v1")
        return TestClient(app, raise_server_exceptions=False)

    return _make


@pytest.fixture
def cleanup_container():
    """确保 override_container 在每个用例结束时复位（避免泄漏到其他测试）。"""
    yield
    override_container(None)


def _payload(request_id: str) -> dict:
    return {"request_id": request_id, "rating": "helpful", "reason_codes": ["correct"]}


def test_owner_can_submit_feedback(tmp_path: Path, make_client, cleanup_container):
    db = ProductDatabase(tmp_path / "fb-owner.sqlite3")
    db.initialize()
    _seed_query_log(db, "req-own-1", "user-a")
    client = make_client(db, actor="user-a")
    response = client.post("/api/v1/feedback", json=_payload("req-own-1"))
    assert response.status_code == 201
    assert response.json()["request_id"] == "req-own-1"


def test_other_principal_gets_uniform_not_found(tmp_path: Path, make_client, cleanup_container):
    """跨主体：已存在和不存在 request_id 必须返回同一外部语义（404）。"""
    db = ProductDatabase(tmp_path / "fb-cross.sqlite3")
    db.initialize()
    _seed_query_log(db, "req-own-2", "someone-else")
    client = make_client(db, actor="user-a")

    existing = client.post("/api/v1/feedback", json=_payload("req-own-2"))
    missing = client.post("/api/v1/feedback", json=_payload("req-does-not-exist"))
    assert existing.status_code == 404
    assert missing.status_code == 404
    # 外部语义一致：同一错误码 + 消息体不可区分
    assert existing.json() == missing.json()
    # 未写入任何反馈
    assert db.fetch_one("SELECT COUNT(*) AS c FROM feedback")["c"] == 0


def test_duplicate_feedback_remains_conflict(tmp_path: Path, make_client, cleanup_container):
    db = ProductDatabase(tmp_path / "fb-dup.sqlite3")
    db.initialize()
    _seed_query_log(db, "req-own-3", "user-a")
    client = make_client(db, actor="user-a")
    assert client.post("/api/v1/feedback", json=_payload("req-own-3")).status_code == 201
    assert client.post("/api/v1/feedback", json=_payload("req-own-3")).status_code == 409


def test_null_owner_row_fail_closed(tmp_path: Path, make_client, cleanup_container):
    """存量 NULL 归属：fail-closed，任何主体（含 anonymous）都不能写。"""
    db = ProductDatabase(tmp_path / "fb-null.sqlite3")
    db.initialize()
    _seed_null_owner(db, "req-null-1")
    for actor in ("user-a", "anonymous", "admin"):
        client = make_client(db, actor=actor)
        response = client.post("/api/v1/feedback", json=_payload("req-null-1"))
        assert response.status_code == 404, actor
    assert db.fetch_one("SELECT COUNT(*) AS c FROM feedback")["c"] == 0


def test_not_helpful_still_creates_bad_case_for_owner(tmp_path: Path, make_client, cleanup_container):
    """owner 的 not_helpful 反馈仍进 bad_cases（既有产品语义保持兼容）。"""
    db = ProductDatabase(tmp_path / "fb-bad.sqlite3")
    db.initialize()
    _seed_query_log(db, "req-own-4", "user-a")
    client = make_client(db, actor="user-a")
    response = client.post("/api/v1/feedback", json={
        "request_id": "req-own-4", "rating": "not_helpful", "reason_codes": ["wrong_answer"],
    })
    assert response.status_code == 201
    assert db.fetch_one("SELECT COUNT(*) AS c FROM bad_cases")["c"] == 1


def test_service_logs_do_not_leak_question_text(tmp_path: Path, caplog):
    """反馈链路日志不得输出问题正文（安全红线：敏感内容不进日志）。"""
    from domain.models import FeedbackCreate

    db = ProductDatabase(tmp_path / "fb-log.sqlite3")
    db.initialize()
    _seed_query_log(db, "req-own-5", "user-a", answer="secret-answer-body")
    service = FeedbackService(db)
    with caplog.at_level(logging.DEBUG, logger="mindgraph.feedback"):
        service.create_feedback(
            FeedbackCreate(**_payload("req-own-5")), principal_id="user-a",
        )
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "secret-answer-body" not in joined
    assert "报销时限" not in joined


def test_agent_service_persist_writes_principal_id(tmp_path: Path):
    """修正 2：assist 轮的 query_logs 必须带 principal_id（与 chat 渠道对称）。"""
    from domain.models import ChatRequest
    from tests.test_agent_service import _build

    service, database, _pipeline = _build(tmp_path)
    events = list(service.stream_assist(
        ChatRequest(question="报销时限是多少天？", retrieval_strategy="hybrid"),
        access_scope={"user": "user-a"},
    ))
    assert events[-1]["event"] == "completed"
    rows = database.fetch_all(
        "SELECT principal_id FROM query_logs WHERE prompt_version='assist-agent-v1'"
    )
    assert len(rows) == 1
    assert rows[0]["principal_id"] == "user-a"


def test_agent_service_persist_defaults_to_anonymous(tmp_path: Path):
    """无 access_scope（off/匿名）时 assist 落 anonymous，与 ChatService 口径一致。"""
    from domain.models import ChatRequest
    from tests.test_agent_service import _build

    service, database, _pipeline = _build(tmp_path)
    list(service.stream_assist(ChatRequest(question="报销时限是多少天？", retrieval_strategy="hybrid")))
    rows = database.fetch_all(
        "SELECT principal_id FROM query_logs WHERE prompt_version='assist-agent-v1'"
    )
    assert len(rows) == 1
    assert rows[0]["principal_id"] == "anonymous"
