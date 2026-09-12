"""PR-13 可恢复澄清协议：clarification_requests 的持久化与幂等 resume。

任务书测试矩阵：正常恢复、重复提交、过期、伪造 hash、跨主体、会话归档。

语义边界（现场核对报告已定）：
- expires_at 是唯一有效期判据（服务端 UTC 时钟）；
- consumed_at 是幂等键：重复 resume 返回原语义，不重复副作用；
- 过期 → expired 业务态；跨主体 → 与"不存在"不可区分（not_found）；
- resume 校验必须跨进程稳定：MINDGRAPH_CLARIFICATION_SALT 缺失时
  context_hash 校验按 fail-closed 处理（宁拒勿伪造）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from infrastructure.database import ProductDatabase


@pytest.fixture
def db(tmp_path: Path) -> ProductDatabase:
    database = ProductDatabase(tmp_path / "clarify.sqlite3")
    database.initialize()
    return database


@pytest.fixture
def service(db: ProductDatabase):
    from application.clarification_service import ClarificationService

    return ClarificationService(db)


@pytest.fixture
def stable_salt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINDGRAPH_CLARIFICATION_SALT", "test-salt-fixed")


def _create_request(
    db: ProductDatabase, *,
    principal_id: str = "user-a",
    clarification_id: str = "clar-1",
    conversation_id: str | None = "conv-1",
    questions: list[str] | None = None,
    context_hash: str | None = None,
    original: str = "差旅费怎么报",
    expires_in_minutes: int = 30,
):
    """直接落一条待恢复请求（模拟 stream_assist 澄清路由写入后的状态）。

    original_request_hash 与 ClarificationService.record 同口径
    （sha256(原问句)[:16]）——resume 校验用它作为签名语境，口径不一致
    会让所有正常用例假性 not_found。
    """
    from datetime import UTC, datetime, timedelta
    import hashlib

    questions = questions or ["是哪个城市的差旅？", "涉及金额区间？"]
    original_hash = hashlib.sha256(original.encode("utf-8")).hexdigest()[:16]
    db.execute(
        "INSERT INTO clarification_requests (clarification_id, principal_id, conversation_id,"
        " original_request_hash, questions_json, context_hash, expires_at, consumed_at, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (
            clarification_id, principal_id, conversation_id,
            original_hash, json.dumps({"questions": questions, "budget": {"final_top_k": 5, "graph_enabled": False}}, ensure_ascii=False),
            context_hash or "pending-compute",
            (datetime.now(UTC) + timedelta(minutes=expires_in_minutes)).isoformat(),
            None, datetime.now(UTC).isoformat(),
        ),
    )
    return clarification_id


# ── 正常恢复 ──────────────────────────────────────────────────────────


def test_normal_resume_returns_original_questions(db: ProductDatabase, service, stable_salt):
    """owner 在有效期内 resume：返回原问题集与原检索预算（不重新生成）。"""
    from application.agent_service import make_clarification_token

    original = "差旅费怎么报"
    questions = ["是哪个城市的差旅？"]
    import hashlib
    question_hash = hashlib.sha256(original.encode('utf-8')).hexdigest()[:16]
    clarification_id, context_hash, expires_at = make_clarification_token(question_hash, questions)
    _create_request(db, clarification_id=clarification_id, questions=questions,
                    context_hash=context_hash)
    db.execute("UPDATE clarification_requests SET expires_at=? WHERE clarification_id=?", (expires_at, clarification_id))

    resume = service.resume(
        clarification_id=clarification_id, principal_id="user-a",
        answers=["上海"], conversation_id="conv-1",
    )
    assert resume.state == "resumed"
    assert resume.questions == questions
    # original_request_hash 与 record() 同口径（sha256(原问句)[:16]）
    import hashlib
    assert resume.original_request_hash == hashlib.sha256(original.encode("utf-8")).hexdigest()[:16]
    # 消费即落账：一次性
    row = db.fetch_one("SELECT consumed_at FROM clarification_requests WHERE clarification_id=?", (clarification_id,))
    assert row["consumed_at"] is not None


def test_resume_original_budget_preserved(db: ProductDatabase, service, stable_salt):
    """恢复后沿用原 retrieval budget：resume 结果携带原 budget 字段。"""
    from application.agent_service import make_clarification_token

    original = "差旅怎么报"
    questions = ["哪个城市？"]
    import hashlib
    question_hash = hashlib.sha256(original.encode('utf-8')).hexdigest()[:16]
    clarification_id, context_hash, expires_at = make_clarification_token(question_hash, questions)
    _create_request(db, clarification_id=clarification_id, questions=questions,
                    context_hash=context_hash, original=original)
    db.execute("UPDATE clarification_requests SET expires_at=? WHERE clarification_id=?", (expires_at, clarification_id))
    resume = service.resume(
        clarification_id=clarification_id, principal_id="user-a",
        answers=["北京"], conversation_id=None,
    )
    assert resume.state == "resumed"
    assert resume.retrieval_budget == {"final_top_k": 5, "graph_enabled": False}


# ── 重复提交（幂等） ──────────────────────────────────────────────────


def test_repeat_resume_idempotent(db: ProductDatabase, service, stable_salt):
    """已 consumed 的重复 resume：返回 already_consumed + 原问题集，不重复副作用。"""
    from application.agent_service import make_clarification_token

    original = "差旅费怎么报"
    questions = ["哪个城市？"]
    import hashlib
    question_hash = hashlib.sha256(original.encode('utf-8')).hexdigest()[:16]
    clarification_id, context_hash, expires_at = make_clarification_token(question_hash, questions)
    _create_request(db, clarification_id=clarification_id, questions=questions, context_hash=context_hash)
    db.execute("UPDATE clarification_requests SET expires_at=? WHERE clarification_id=?", (expires_at, clarification_id))

    first = service.resume(clarification_id=clarification_id, principal_id="user-a", answers=["上海"])
    second = service.resume(clarification_id=clarification_id, principal_id="user-a", answers=["上海"])
    assert first.state == "resumed"
    assert second.state == "already_consumed"
    assert second.questions == first.questions
    # consumed_at 不被二次改写
    row = db.fetch_one("SELECT consumed_at FROM clarification_requests WHERE clarification_id=?", (clarification_id,))
    consumed_once = row["consumed_at"]
    service.resume(clarification_id=clarification_id, principal_id="user-a", answers=["再试"])
    assert db.fetch_one("SELECT consumed_at FROM clarification_requests WHERE clarification_id=?", (clarification_id,))["consumed_at"] == consumed_once


# ── 过期 ──────────────────────────────────────────────────────────────


def test_expired_resume_rejected(db: ProductDatabase, service):
    """过期 → expired 业务态；不返回原问题内容。"""
    _create_request(db, clarification_id="clar-old", expires_in_minutes=-1)
    resume = service.resume(clarification_id="clar-old", principal_id="user-a", answers=["x"])
    assert resume.state == "expired"
    assert resume.questions == []


# ── 伪造 hash / 主体不符 ──────────────────────────────────────────────


def test_forged_clarification_id_indistinguishable(db: ProductDatabase, service, stable_salt):
    """不存在的 id 与跨主体 id 返回同一语义（not_found，不暴露存在性）。"""
    _create_request(db, clarification_id="clar-mine", principal_id="user-a")
    stranger = service.resume(clarification_id="clar-mine", principal_id="user-b", answers=["x"])
    missing = service.resume(clarification_id="clar-none", principal_id="user-b", answers=["x"])
    assert stranger.state == "not_found"
    assert missing.state == "not_found"
    assert stranger.questions == missing.questions == []


def test_missing_salt_fails_closed(db: ProductDatabase, service, monkeypatch: pytest.MonkeyPatch):
    """盐未配置（进程随机盐）：跨进程校验不可信 → resume 拒绝，宁拒勿伪造。"""
    monkeypatch.delenv("MINDGRAPH_CLARIFICATION_SALT", raising=False)
    # salt._random 属于另一进程概念；这里清掉模块级缓存模拟"盐不稳定"
    from application import agent_service

    monkeypatch.delattr(agent_service._clarification_salt, "_random", raising=False)
    _create_request(db, clarification_id="clar-nosalt", context_hash="whatever")
    resume = service.resume(clarification_id="clar-nosalt", principal_id="user-a", answers=["x"])
    assert resume.state == "not_found"  # 校验失败与不存在同语义（不暴露内部原因）
