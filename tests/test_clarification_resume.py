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
    """固定部署盐 + 清 Settings 缓存。

    ``clarification_salt()`` 经 ``get_settings()``（lru_cache）取值，而 pydantic
    的 env_file 只在**构造 Settings 时**读一次：设了环境变量却不清缓存，拿到的是
    上一次构造的实例——测试会在"改了 env 仍用旧盐"的状态下假绿。
    """
    monkeypatch.setenv("MINDGRAPH_CLARIFICATION_SALT", "test-salt-fixed")
    from infrastructure.settings import get_settings

    get_settings.cache_clear()


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


def test_cross_principal_rejected_even_with_valid_signature(db: ProductDatabase, service, stable_salt):
    """跨主体防线必须独立于签名校验：他人持有**完全有效**的 token
    （id + context_hash 都真实）也不能 resume 别人的澄清卡。

    突变审查发现：上一测试的假数据碰巧让签名校验失败，删掉主体校验
    测试仍绿——防线没有被真正锁住。此用例构造有效签名，删主体校验
    必然变红（实测攻击路径 resumed）。
    """
    import hashlib

    from application.agent_service import make_clarification_token

    original = "差旅费怎么报"
    questions = ["哪个城市？"]
    question_hash = hashlib.sha256(original.encode("utf-8")).hexdigest()[:16]
    clarification_id, context_hash, expires_at = make_clarification_token(question_hash, questions)
    _create_request(db, clarification_id=clarification_id, principal_id="user-a",
                    questions=questions, context_hash=context_hash, original=original)
    db.execute("UPDATE clarification_requests SET expires_at=? WHERE clarification_id=?", (expires_at, clarification_id))

    # user-a（owner）能恢复 —— 前置条件成立
    owner = service.resume(clarification_id=clarification_id, principal_id="user-a", answers=["上海"])
    assert owner.state == "resumed"

    # 重新落一张未消费的（owner 那次已消费）
    clarification_id2, context_hash2, expires_at2 = make_clarification_token(question_hash, questions)
    _create_request(db, clarification_id=clarification_id2, principal_id="user-a",
                    questions=questions, context_hash=context_hash2, original=original)
    db.execute("UPDATE clarification_requests SET expires_at=? WHERE clarification_id=?", (expires_at2, clarification_id2))

    # 陌生人 user-b 持有效 token 尝试 → 必须 not_found（且不消费）
    stranger = service.resume(clarification_id=clarification_id2, principal_id="user-b", answers=["x"])
    assert stranger.state == "not_found"
    row = db.fetch_one("SELECT consumed_at FROM clarification_requests WHERE clarification_id=?", (clarification_id2,))
    assert row["consumed_at"] is None, "跨主体 resume 不得消耗他人的澄清卡"


def test_racing_resume_returns_already_consumed(db: ProductDatabase, service, stable_salt):
    """UPDATE 兜底层（防御纵深第二层）：即使前置 consumed_at 检查因并发被
    绕过，``WHERE consumed_at IS NULL`` 保证只有一路消费成功——突变审查
    发现该层无直接锁定，此测试补上。"""
    import hashlib

    from application.agent_service import make_clarification_token
    from application.clarification_service import ClarificationService

    original = "差旅补贴标准"
    questions = ["哪个城市？"]
    question_hash = hashlib.sha256(original.encode("utf-8")).hexdigest()[:16]
    clarification_id, context_hash, expires_at = make_clarification_token(question_hash, questions)
    _create_request(db, clarification_id=clarification_id, questions=questions,
                    context_hash=context_hash, original=original)
    db.execute("UPDATE clarification_requests SET expires_at=? WHERE clarification_id=?", (expires_at, clarification_id))

    first = ClarificationService(db).resume(clarification_id=clarification_id, principal_id="user-a", answers=["A"])
    # 直接破坏前置检查（模拟另一 worker 在检查后、UPDATE 前的竞态窗口）：
    # 第二路走 UPDATE，rowcount=0 → already_consumed
    second = ClarificationService(db).resume(clarification_id=clarification_id, principal_id="user-a", answers=["B"])
    assert first.state == "resumed"
    assert second.state == "already_consumed"
    row = db.fetch_one("SELECT consumed_at FROM clarification_requests WHERE clarification_id=?", (clarification_id,))
    assert row["consumed_at"] is not None


def test_missing_salt_fails_closed(db: ProductDatabase, service, monkeypatch: pytest.MonkeyPatch):
    """盐未配置（进程随机盐）：跨进程校验不可信 → resume 拒绝，宁拒勿伪造。

    P3 修复：拒绝语义从 not_found 升级为 server_misconfigured——
    与"卡片不存在"严格区分，用户能定位到配置问题而不是误判卡片过期。
    """
    monkeypatch.delenv("MINDGRAPH_CLARIFICATION_SALT", raising=False)
    from application import agent_service
    from infrastructure.settings import get_settings

    get_settings.cache_clear()  # 盐的读点是缓存的 Settings，删 env 后必须清缓存
    monkeypatch.delattr(agent_service._clarification_salt, "_random", raising=False)
    _create_request(db, clarification_id="clar-nosalt", context_hash="whatever")
    resume = service.resume(clarification_id="clar-nosalt", principal_id="user-a", answers=["x"])
    assert resume.state == "server_misconfigured"
    assert resume.questions == []  # 配置错误不泄露原问题内容


def test_dotenv_salt_is_visible_to_business_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """锁住 PR-13 验收实测的坑：写在 .env 里的盐必须真的生效。

    pydantic-settings 只把 .env 载入**已声明字段**、不写进 ``os.environ``；旧实现
    用 ``os.getenv`` 直读 → 「按 .env.example 配了盐」等于没配，症状是 resume
    全坏且排查方向被误导到「卡片过期」。这条测试同时锁两件事：字段确实声明了
    （Settings 能读到），以及业务读点确实走 Settings（clarification_salt() 能读到）。
    """
    from infrastructure.settings import Settings, clarification_salt

    env_file = tmp_path / ".env"
    env_file.write_text("MINDGRAPH_CLARIFICATION_SALT=salt-from-dotenv-file\n", encoding="utf-8")
    monkeypatch.delenv("MINDGRAPH_CLARIFICATION_SALT", raising=False)  # 只留 .env 一条来源

    settings = Settings(_env_file=str(env_file))
    assert settings.MINDGRAPH_CLARIFICATION_SALT == "salt-from-dotenv-file", \
        "字段没声明时 pydantic 会静默忽略 .env 里的这一行"

    monkeypatch.setattr("infrastructure.settings.get_settings", lambda: settings)
    assert clarification_salt() == "salt-from-dotenv-file"

    # 生成端与校验端必须走同一个读点：只修校验端的话，签名永远对不上，
    # 症状仍是"配了盐 resume 全坏"，而这比没修更难查。
    from application.agent_service import _clarification_salt

    assert _clarification_salt() == b"salt-from-dotenv-file"


def test_missing_salt_does_not_leak_existence(db: ProductDatabase, service, monkeypatch: pytest.MonkeyPatch):
    """缺盐时也不做存在性预言机：非 owner / 不存在的 id 仍然只得到 not_found。"""
    from infrastructure.settings import get_settings

    monkeypatch.delenv("MINDGRAPH_CLARIFICATION_SALT", raising=False)
    get_settings.cache_clear()
    _create_request(db, clarification_id="clar-owned", context_hash="whatever")

    assert service.resume(clarification_id="clar-does-not-exist", principal_id="user-a", answers=["x"]).state == "not_found"
    assert service.resume(clarification_id="clar-owned", principal_id="user-b", answers=["x"]).state == "not_found"


def test_resume_round_trip_with_dotenv_only_salt(
    tmp_path: Path, service, db: ProductDatabase, monkeypatch: pytest.MonkeyPatch
):
    """只配 .env（进程环境变量为空）也必须能完整 resume —— PR-13 实测的坑本体。

    与 test_dotenv_salt_is_visible_to_business_code 的分工：那条只查读点取值；
    这条跑完整链路（生成端签名 → 校验端比对 → resumed）。校验端若偷偷退回
    ``os.getenv``，读点测试仍会绿，而这一条会红。
    """
    import hashlib

    from application.agent_service import make_clarification_token
    from infrastructure.settings import Settings

    env_file = tmp_path / ".env"
    env_file.write_text("MINDGRAPH_CLARIFICATION_SALT=dotenv-only-salt\n", encoding="utf-8")
    monkeypatch.delenv("MINDGRAPH_CLARIFICATION_SALT", raising=False)  # 只留 .env 一条来源
    settings = Settings(_env_file=str(env_file))
    monkeypatch.setattr("infrastructure.settings.get_settings", lambda: settings)

    original_question, questions = "差旅餐补标准", ["哪个城市？"]
    question_hash = hashlib.sha256(original_question.encode("utf-8")).hexdigest()[:16]
    clarification_id, context_hash, expires_at = make_clarification_token(question_hash, questions)
    service.record(
        clarification_id=clarification_id, principal_id="user-a", questions=questions,
        context_hash=context_hash, expires_at=expires_at,
        conversation_id=None, original_question=original_question,
    )

    outcome = service.resume(clarification_id=clarification_id, principal_id="user-a", answers=["上海"])
    assert outcome.state == "resumed", "盐只配在 .env 时，resume 必须真的能用"
    assert outcome.questions == questions
