"""PR-12 服务端会话上下文与指代解析：ConversationContextService + FollowupResolver。

任务书测试矩阵：指代、补充条件、纠错、版本切换、归档、权限变化、跨主体。

设计红线：
- 解析是**确定性规则**，不用 LLM 改写（不虚构用户没说的条件）；
- 上下文按 owner 隔离（跨主体串线 = 0）；
- 截断预算显式（max_turns + max_context_chars），额外 token/延迟可观测；
- flag 默认关：关闭时 conversation 流与单轮行为完全一致。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from application.conversation_service import ConversationService
from application.followup_resolver import ConversationContextService, FollowupResolver
from infrastructure.database import ProductDatabase


@pytest.fixture
def service(tmp_path: Path) -> ConversationService:
    db = ProductDatabase(tmp_path / "ctx.sqlite3")
    db.initialize()
    return ConversationService(db)


def _conversation(service: ConversationService, principal: str = "user-a") -> str:
    created = service.create_conversation(principal_id=principal, title="T")
    return created["conversation_id"]


def _turn(service: ConversationService, conversation_id: str, role: str, content: str) -> None:
    service.append_message(
        conversation_id=conversation_id, principal_id="user-a", role=role, content=content,
    )


# ── ConversationContextService：窗口、预算、归属 ──────────────────────


def test_context_window_respects_turn_budget(service: ConversationService):
    """默认最近 N 轮：旧消息不进上下文（截断预算显式可见）。"""
    conversation_id = _conversation(service)
    for i in range(10):
        _turn(service, conversation_id, "user", f"问题{i}")
        _turn(service, conversation_id, "assistant", f"回答{i}")

    context = ConversationContextService(service, max_turns=3, max_context_chars=4000)
    window = context.recent_window(conversation_id, principal_id="user-a")
    assert len(window) == 6  # 3 轮 = 3 user + 3 assistant，不掺半轮
    assert window[-1]["content"] == "回答9"
    assert window[0]["content"] == "问题7"  # 完整轮：以 user 开头
    assert window[0]["role"] == "user"  # 孤儿 assistant 必须被弹出


def test_context_window_respects_char_budget(service: ConversationService):
    """字符预算更紧时以字符为准（双限取更严）。"""
    conversation_id = _conversation(service)
    _turn(service, conversation_id, "user", "长问题" * 300)
    _turn(service, conversation_id, "assistant", "长回答" * 300)
    _turn(service, conversation_id, "user", "短问题")

    context = ConversationContextService(service, max_turns=10, max_context_chars=100)
    window = context.recent_window(conversation_id, principal_id="user-a")
    total = sum(len(item["content"]) for item in window)
    assert total <= 100 * 2, "字符预算必须约束上下文总量"


def test_context_is_owner_scoped(service: ConversationService):
    """跨主体读取上下文 = 拒绝（串线 = 0）。"""
    conversation_id = _conversation(service, principal="user-a")
    _turn(service, conversation_id, "user", "user-a 的机密问题")
    context = ConversationContextService(service, max_turns=3, max_context_chars=4000)
    with pytest.raises(Exception):
        context.recent_window(conversation_id, principal_id="user-b")


def test_archived_conversation_context_blocked(service: ConversationService):
    """归档后的会话不再提供上下文（retention 一致）。"""
    conversation_id = _conversation(service)
    _turn(service, conversation_id, "user", "旧问题")
    service.archive_conversation(conversation_id=conversation_id, principal_id="user-a")
    context = ConversationContextService(service, max_turns=3, max_context_chars=4000)
    with pytest.raises(Exception):
        context.recent_window(conversation_id, principal_id="user-a")


# ── FollowupResolver：确定性指代解析 ──────────────────────────────────


def test_reference_resolution_to_recent_topic():
    """指代：「那个标准」→ 绑定最近讨论的主题，输出解析证据。"""
    resolver = FollowupResolver()
    history = [
        {"role": "user", "content": "差旅费住宿标准是多少"},
        {"role": "assistant", "content": "住宿标准为每天 500 元（见《差旅费管理办法》）"},
    ]
    result = resolver.resolve("那个标准的报销时限是多久", history)
    assert result.resolved_query is not None
    assert "住宿" in result.resolved_query or "差旅费" in result.resolved_query
    assert result.substitutions, "解析必须留下证据：替换了哪个指代"
    assert result.context_tokens > 0, "多轮上下文的额外 token 必须可观测"


def test_version_reference_resolution():
    """版本切换：「上一版」→ 显式绑定上一轮的版本语境。"""
    resolver = FollowupResolver()
    history = [
        {"role": "user", "content": "报销管理办法 V3 的时限"},
        {"role": "assistant", "content": "V3 规定 30 天内提交。"},
    ]
    result = resolver.resolve("上一版的时限呢", history)
    assert result.resolved_query is not None
    assert "V3" in result.resolved_query or "报销管理办法" in result.resolved_query


def test_condition_inheritance():
    """补充条件：未完成槽位（金额/日期）从上文继承，不虚构。"""
    resolver = FollowupResolver()
    history = [
        {"role": "user", "content": "2026 年新员工差旅费怎么报"},
        {"role": "assistant", "content": "按现行制度 30 天内提交"},
    ]
    result = resolver.resolve("那住宿呢", history)
    assert result.resolved_query is not None
    assert "差旅费" in result.resolved_query  # 主题槽位继承


def test_plain_question_passthrough():
    """无指代/无槽位 → 原样通过，不硬造改写（宁缺毋滥）。"""
    resolver = FollowupResolver()
    history = [{"role": "user", "content": "差旅费怎么报"}]
    result = resolver.resolve("发票抬头写什么", history)
    assert result.resolved_query == "发票抬头写什么"
    assert result.substitutions == []


def test_no_history_passthrough():
    """空历史 → 单轮语义（flag 关闭时同行为）。"""
    resolver = FollowupResolver()
    result = resolver.resolve("差旅费怎么报", [])
    assert result.resolved_query == "差旅费怎么报"


def test_correction_marks_previous_query():
    """纠错语义：明确指向上一问的否定/更正必须可追溯。"""
    resolver = FollowupResolver()
    history = [
        {"role": "user", "content": "市内交通费标准"},
        {"role": "assistant", "content": "市内交通 80 元/天。"},
    ]
    result = resolver.resolve("不对，我问的是机票标准", history)
    assert result.resolved_query is not None
    assert "机票" in result.resolved_query
    assert any(item["kind"] == "correction" for item in result.substitutions)
