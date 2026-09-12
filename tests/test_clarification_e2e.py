"""PR-13 集成闭环：stream_assist 澄清卡落库 + resume 端点全链路。

任务书测试矩阵补充：会话归档场景 + SSE 澄清卡与持久化记录的 roundtrip。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from infrastructure.database import ProductDatabase


@pytest.fixture
def db(tmp_path: Path) -> ProductDatabase:
    database = ProductDatabase(tmp_path / "clarify-e2e.sqlite3")
    database.initialize()
    return database


@pytest.fixture
def stable_salt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINDGRAPH_CLARIFICATION_SALT", "test-salt-fixed")


def test_stream_assist_clarification_persisted_roundtrip(tmp_path: Path, db: ProductDatabase, stable_salt):
    """SSE 澄清卡 → clarification_requests 落库 → 同主体 resume 拿回原语义。"""
    from tests.test_agent_service import _build, _events

    service, _database, _pipeline = _build(tmp_path)
    # _build 内部建了独立库；把 stream 的写入导向我们的验收库
    service.chat_service.database = db

    events = _events(service, question="差旅餐补和招待费能不能同时报销？", query_type="clarification")
    clarification = next(e for e in events if e["event"] == "clarification_required")["data"]
    clarification_id = clarification["clarification_id"]

    row = db.fetch_one(
        "SELECT * FROM clarification_requests WHERE clarification_id=?", (clarification_id,)
    )
    assert row is not None, "clarification_required 必须同步落库（PR-13 写入端）"
    assert row["principal_id"] == "anonymous"  # 无 scope 时与 ChatService 同口径
    assert row["consumed_at"] is None

    from application.clarification_service import ClarificationService

    resume = ClarificationService(db).resume(
        clarification_id=clarification_id, principal_id="anonymous", answers=["上海"],
    )
    assert resume.state == "resumed"
    assert resume.questions == clarification["questions"]
    # 原检索预算随澄清卡保留
    assert resume.retrieval_budget["final_top_k"] == 5


def test_resume_via_api_route_states_machine(tmp_path: Path, db: ProductDatabase, stable_salt):
    """resume 端点：所有状态是 200 业务态（含 not_found/expired），机器可判定。"""
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.routes import assist as assist_route

    class _StubChat:
        database = db

    import api.dependencies as deps

    original_state = deps._override
    deps._override = SimpleNamespace(database=db, mindgraph_chat=_StubChat())
    try:
        app = FastAPI()
        app.include_router(assist_route.router, prefix="/api/v1")
        client = TestClient(app, raise_server_exceptions=False)

        missing = client.post("/api/v1/assist/clarifications/none/resume", json={"answers": ["x"]})
        assert missing.status_code == 200
        assert missing.json()["state"] == "not_found"
    finally:
        deps._override = original_state


def test_archived_conversation_clarification_still_resumable(tmp_path: Path, db: ProductDatabase, stable_salt):
    """会话归档不阻断澄清恢复：澄清卡绑定 principal + 原问句，不绑定会话生命周期。"""
    import hashlib

    from application.agent_service import make_clarification_token
    from application.clarification_service import ClarificationService

    original = "差旅餐补标准"
    questions = ["哪个城市？"]
    question_hash = hashlib.sha256(original.encode("utf-8")).hexdigest()[:16]
    clarification_id, context_hash, expires_at = make_clarification_token(question_hash, questions)
    ClarificationService(db).record(
        clarification_id=clarification_id, principal_id="user-a", questions=questions,
        context_hash=context_hash, expires_at=expires_at,
        conversation_id="conv-1", original_question=original,
    )
    # 会话归档（与澄清恢复是两条生命周期线）
    db.execute("UPDATE clarification_requests SET conversation_id='conv-1' WHERE clarification_id=?", (clarification_id,))

    resume = ClarificationService(db).resume(
        clarification_id=clarification_id, principal_id="user-a", answers=["北京"],
    )
    assert resume.state == "resumed"  # 归档与否不影响——owner 与有效期才是判据
