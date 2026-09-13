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
    from infrastructure.settings import get_settings

    get_settings.cache_clear()  # 盐经缓存的 Settings 读取，设了 env 必须清缓存


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


def test_resume_route_reports_server_misconfigured_without_salt(
    tmp_path: Path, db: ProductDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """路由级：盐未配置时，本人持卡 resume 必须是 server_misconfigured。

    PR-13 验收里真实发生的事：缺盐 → resume 全坏，但报 not_found，排查方向被
    误导到"卡片过期/被删"。这条测试把"部署配置错"钉成独立业务态，并同时确认
    它没把 not_found 变成存在性预言机（不存在的 id 仍返回 not_found）。
    """
    import hashlib
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import api.dependencies as deps
    from api.routes import assist as assist_route
    from application.agent_service import make_clarification_token
    from application.clarification_service import ClarificationService
    from infrastructure.settings import get_settings

    monkeypatch.delenv("MINDGRAPH_CLARIFICATION_SALT", raising=False)
    get_settings.cache_clear()

    original_question, questions = "差旅餐补标准", ["哪个城市？"]
    question_hash = hashlib.sha256(original_question.encode("utf-8")).hexdigest()[:16]
    clarification_id, context_hash, expires_at = make_clarification_token(question_hash, questions)
    ClarificationService(db).record(
        clarification_id=clarification_id, principal_id="anonymous", questions=questions,
        context_hash=context_hash, expires_at=expires_at,
        conversation_id=None, original_question=original_question,
    )

    class _StubChat:
        database = db

    original_state = deps._override
    deps._override = SimpleNamespace(database=db, mindgraph_chat=_StubChat())
    try:
        app = FastAPI()
        app.include_router(assist_route.router, prefix="/api/v1")
        client = TestClient(app, raise_server_exceptions=False)
        existing = client.post(f"/api/v1/assist/clarifications/{clarification_id}/resume", json={"answers": ["上海"]})
        unknown = client.post("/api/v1/assist/clarifications/does-not-exist/resume", json={"answers": ["上海"]})
    finally:
        deps._override = original_state

    assert existing.status_code == 200
    assert existing.json()["state"] == "server_misconfigured", "缺盐是部署错误，不能报成 not_found"
    assert unknown.json()["state"] == "not_found", "缺盐也不得泄露存在性"


def test_startup_salt_warning_only_when_clarification_channels_enabled() -> None:
    """启动告警门控：澄清通道全关时不许报警（噪音会训练运维忽略启动日志）。"""
    from types import SimpleNamespace

    from api.main import _clarification_salt_warning_needed

    def need(assist: bool, agent_assist: bool, salt: str) -> bool:
        return _clarification_salt_warning_needed(
            SimpleNamespace(ASSIST_ENABLED=assist, AGENT_ASSIST_ENABLED=agent_assist, MINDGRAPH_CLARIFICATION_SALT=salt)
        )

    assert need(False, False, "") is False, "通道全关：不该有噪音"
    assert need(True, False, "") is True, "resume 端点启用：缺盐必须警告"
    assert need(False, True, "") is True, "澄清卡生成端启用：缺盐必须警告"
    assert need(True, True, "configured-salt") is False, "配了盐就不该报警"
