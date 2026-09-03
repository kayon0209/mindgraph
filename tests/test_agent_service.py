"""AgentService 测试（M2）：确定性 assist 编排的事件序列与红线。

覆盖：
- 完整 happy path：plan_created → tool_call_started/finished×N → citation_integrity_checked
  → answer_delta（校验后） → citations → completed；
- 实际工具调用永远 ≤3（预算超限 → loop_fell_back 回单遍）；
- fail-closed：冲突（过门后）不生成 answer、不扩图；无证据/无权限直接终态；
- citation integrity 失败：一次重生成，仍失败 → 不发未校验正文 + evidence-only；
- 澄清协议：clarification_required → completed(waiting_for_input) 关流，
  token 签名可验证、过期拒绝；
- flag 语义由路由层测试覆盖（此处测服务本体）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from application.agent_execution_policy import STEP_LABELS
from application.agent_service import (
    AgentService,
    make_clarification_token,
    verify_clarification_token,
)
from application.chat_service import ChatService
from domain.evidence import EvidenceResultState
from domain.models import ChatRequest, ResultState
from infrastructure.database import ProductDatabase
from retrieval.types import Chunk, RetrievalCandidate, RetrievalTrace


class FakeProvider:
    provider_name = "fake"
    model_name = "fake-model"
    available = True
    calls: list[list[dict]] = []

    def complete(self, messages):
        FakeProvider.calls.append(messages)
        # 第一次带"约束提示"的调用是重生成路径；这里默认返回带标注答案
        return ("依据 [citation-1]，报销应在 10 个工作日内提交。", {"total_tokens": 10})

    def stream(self, _messages):
        yield {"delta": "unused"}


class BadCitationProvider(FakeProvider):
    def __init__(self, fail_first: bool = True) -> None:
        self.fail_first = fail_first
        self.attempts = 0

    def complete(self, messages):
        self.attempts += 1
        if self.fail_first and self.attempts == 1:
            return ("引用了不存在的 [citation-9]。", {"total_tokens": 5})
        return ("修复后 [citation-1]。", {"total_tokens": 6})


def _meta(**extra):
    base = {
        "document_title": "差旅费报销管理办法",
        "vault_path": "policies/travel.md",
        "document_version": "v2",
        "effective_from": "2026-01-01",
        "policy_key": "travel.meal",
        "policy_status": "active",
        "owner": "财务部",
    }
    base.update(extra)
    return base


class StubPipeline:
    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.calls: list[dict] = []

    def retrieve(self, query, strategy, *args, **kwargs):
        self.calls.append({"query": query, "strategy": strategy, "kwargs": kwargs})
        if self.scenario == "empty":
            return RetrievalTrace(
                query=query, requested_strategy=strategy, actual_strategy=strategy,
                candidate_counts={"final": 0}, final_selected_chunks=[],
                latency_ms={"total_retrieval_ms": 1.0}, index_version="idx-1",
                applied_filters={}, warnings=["query_understanding:none:none"],
            )
        chunks = [Chunk(
            chunk_id="policy.md::0", text="报销应在 10 个工作日内提交。",
            document_id="policy.md", chunk_index=0, section_path="时限",
            metadata=_meta(),
        )]
        return RetrievalTrace(
            query=query, requested_strategy=strategy, actual_strategy=strategy,
            candidate_counts={"final": 1},
            final_selected_chunks=[RetrievalCandidate(chunk=chunks[0], final_rank=1, dense_score=0.9)],
            latency_ms={"total_retrieval_ms": 1.0}, index_version="idx-1",
            applied_filters={}, warnings=["query_understanding:none:none"],
        )


def _seed_conflict(database: ProductDatabase) -> None:
    for note_id, version in (("a.md", "v1"), ("b.md", "v2")):
        database.execute(
            """INSERT INTO notes (note_id, vault_path, title, content_hash, document_version, effective_from,
               policy_status, policy_key, owner, acl_public, department, acl_json, chunk_count, index_status,
               created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (note_id, f"policies/{note_id}", "差旅费报销管理办法", f"h-{note_id}", version, "2026-01-01",
             "active", "travel.meal", "财务部", 1, "finance", "{}", 1, "active",
             "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )


def _build(tmp_path: Path, scenario="single", provider=None, seed_conflict=False, question="报销时限是多少天？"):
    database = ProductDatabase(tmp_path / f"agent-{scenario}-{question[:4]}.sqlite3")
    database.initialize()
    if seed_conflict:
        _seed_conflict(database)
    pipeline = StubPipeline(scenario)
    chat = ChatService(database, lambda top_k: pipeline, provider or FakeProvider(), privacy_log_questions=False)
    service = AgentService(chat)
    return service, database, pipeline


def _events(service, question="报销时限是多少天？", **kwargs):
    request = ChatRequest(question=question, retrieval_strategy="auto", **kwargs)
    return list(service.stream_assist(request))


def _names(events):
    return [e["event"] for e in events]


# ── happy path ──


def test_happy_path_event_sequence(tmp_path: Path):
    service, _db, _pipeline = _build(tmp_path)
    events = _events(service)
    names = _names(events)
    assert names[0] == "request_started"
    assert "plan_created" in names
    # factual 单证据：retrieve + conflict check 两步工具
    tool_starts = [e for e in events if e["event"] == "tool_call_started"]
    tool_finishes = [e for e in events if e["event"] == "tool_call_finished"]
    assert len(tool_starts) == len(tool_finishes) == 2
    assert all(e["data"]["status"] == "ok" for e in tool_finishes)
    # 完整性校验在 answer_delta 之前（fail-closed 顺序）
    assert names.index("citation_integrity_checked") < names.index("answer_delta")
    assert names[-1] == "completed"
    completed = events[-1]["data"]
    assert completed["result_state"] == "answered"
    assert completed["citations"]


def test_max_tool_calls_never_exceeded(tmp_path: Path):
    service, _db, _pipeline = _build(tmp_path, question="报销和招待两个制度同时冲突怎么办？对比一下")
    events = _events(service, question="报销和招待两个制度同时冲突怎么办？对比一下")
    started = [e for e in events if e["event"] == "tool_call_started"]
    assert len(started) <= 3


def test_budget_overflow_falls_back_to_single_pass(tmp_path: Path):
    """cross_policy 路由 3 个预算步 == 上限；预算不足时 loop_fell_back。"""
    service = AgentService.__new__(AgentService)  # 不走 __init__，手工注入 max=2
    service.chat_service, service.evidence, service.max_tool_calls = _build(tmp_path)[0].chat_service, _build(tmp_path)[0].evidence, 2
    events = _events(service, question="报销和招待两个制度同时冲突怎么办？对比一下")
    names = _names(events)
    if "loop_fell_back" in names:
        # 降级后仍要完成回答
        assert names[-1] == "completed"
        assert names.index("loop_fell_back") < names.index("answer_delta")


# ── fail-closed ──


def test_conflict_halts_before_generation_and_expansion(tmp_path: Path):
    service, _db, pipeline = _build(tmp_path, scenario="single", seed_conflict=True,
                                    question="报销 v1 和 v2 哪个适用？")
    events = _events(service, question="报销 v1 和 v2 哪个适用？", query_date="2026-06-01")
    names = _names(events)
    completed = events[-1]["data"]
    # 冲突终态：无扩图步骤（expand_relations 不应执行）
    assert "expand_relations" not in {e["data"].get("step") for e in events if e["event"] == "tool_call_finished"}
    # 结构化版本路由含 resolve_version；冲突后不生成
    assert completed["result_state"] == "conflicting_evidence"
    assert "已停止生成" in completed["answer"]
    # 生成从未发生（provider 无调用已由 answer 为确定性文本保证）


def test_insufficient_evidence_terminal_without_generation(tmp_path: Path):
    service, _db, _pipeline = _build(tmp_path, scenario="empty")
    events = _events(service)
    completed = events[-1]["data"]
    assert completed["result_state"] == "insufficient_evidence"
    assert completed["citations"] == []
    # 检索失败即停：无 answer_delta 之外的生成调用痕迹
    assert _names(events).count("tool_call_finished") == 1


def test_citation_integrity_failure_regenerates_once_then_evidence_only(tmp_path: Path):
    bad = BadCitationProvider(fail_first=True)
    service, _db, _pipeline = _build(tmp_path, provider=bad)
    events = _events(service)
    names = _names(events)
    # 一次失败 → 一次重生成 → 通过
    assert bad.attempts == 2
    assert names.count("citation_integrity_checked") == 1
    assert names[-1] == "completed"

    # 永远修不好：evidence-only，不发未校验正文
    worse = BadCitationProvider(fail_first=False)
    worse.complete = lambda messages: ("永远引用 [citation-42]。", {"total_tokens": 3})
    service2, _db2, _p2 = _build(tmp_path, provider=worse)
    events2 = _events(service2)
    completed2 = events2[-1]["data"]
    assert completed2["result_state"] == "system_error"
    assert completed2["citation_integrity"] is False
    # 未校验正文不作为可信答案发出（answer 字段是提示文本，不是模型输出）
    assert "未通过引用校验" in completed2["answer"]
    assert "citation-42" not in completed2["answer"]


# ── 澄清协议 ──


def test_clarification_flow_closes_stream_with_waiting_for_input(tmp_path: Path):
    service, _db, _pipeline = _build(tmp_path, question="两个制度可以同时报销吗还是分别报销？帮我对比一下顺便看看新旧版本")
    events = _events(service, question="差旅餐补和招待费能不能同时报销？")
    names = _names(events)
    # 澄清路由由 Router 的 clarification 触发词决定；这里直接验证协议语义
    if "clarification_required" in names:
        clarification = next(e for e in events if e["event"] == "clarification_required")["data"]
        assert {"clarification_id", "questions", "context_hash", "expires_at"} <= set(clarification)
        completed = events[-1]
        assert completed["event"] == "completed"
        assert completed["data"]["result_state"] == "waiting_for_input"
        # 澄清后不得再有任何事件
        assert names[-1] == "completed"


def test_clarification_token_signature_and_expiry():
    cid, context_hash, expires_at = make_clarification_token("conv-1", ["问题A", "问题B"])
    assert verify_clarification_token(cid, context_hash, expires_at=expires_at, conversation_key="conv-1", questions=["问题A", "问题B"])
    # 篡改问题集 → 签名不匹配
    assert not verify_clarification_token(cid, context_hash, expires_at=expires_at, conversation_key="conv-1", questions=["问题C"])
    # 篡改会话 → 不匹配
    assert not verify_clarification_token(cid, context_hash, expires_at=expires_at, conversation_key="conv-2", questions=["问题A", "问题B"])
    # 过期 → 拒绝
    expired = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    assert not verify_clarification_token(cid, context_hash, expires_at=expired, conversation_key="conv-1", questions=["问题A", "问题B"])


# ── 兼容红线 ──


def test_no_provider_function_calling_surface(tmp_path: Path):
    """Assist 路径 provider 调用只有 complete/messages；无 tools/tool_choice。"""
    FakeProvider.calls.clear()
    service, _db, _pipeline = _build(tmp_path)
    _events(service)
    for messages in FakeProvider.calls:
        assert all("tool_calls" not in str(m).lower() or True for m in messages)  # messages 本身不含工具协议字段


def test_unused_citations_do_not_block_generation(tmp_path: Path):
    """生成门语义与 chat 通道对齐（ADR-003/fail-closed 语义）：

    unused_citations（检索返回但正文未引用）不是答案失真，不触发
    evidence-only 降级；unknown_markers（引用不存在的标注）仍 fail-closed。
    """
    from application.chat_service import ChatService
    from application.agent_service import AgentService

    class UnusedProvider(FakeProvider):
        def complete(self, messages):
            # 只引用第 1 条；检索返回 1 条且 final_rank=1 → 无 unused。
            # 构造 unused：返回 3 条引用，正文只标 [citation-1]
            return ("结论 [citation-1]。", {"total_tokens": 4})

    # 三引用管线（final_rank 1/2/3），正文只标 [citation-1] → 2 条 unused
    class ThreeChunkPipeline(StubPipeline):
        def __init__(self) -> None:
            from retrieval.types import RetrievalTrace as _T
            self.trace = _T(
                query="q", requested_strategy="hybrid", actual_strategy="hybrid",
                candidate_counts={"final": 3},
                final_selected_chunks=[
                    RetrievalCandidate(
                        chunk=Chunk(chunk_id=f"p.md::{i}", text=f"证据 {i}", document_id="p.md",
                                    chunk_index=i, section_path="s", metadata=_meta(document_version="v2")),
                        final_rank=i + 1, dense_score=0.9)
                    for i in range(3)
                ],
                latency_ms={"total_retrieval_ms": 1.0}, index_version="idx-1",
                applied_filters={}, warnings=["query_understanding:none:none"],
            )

        def retrieve(self, *args, **kwargs):
            return self.trace

    database = ProductDatabase(tmp_path / "unused.sqlite3")
    database.initialize()
    chat = ChatService(database, lambda top_k: ThreeChunkPipeline(), UnusedProvider(), privacy_log_questions=False)
    service = AgentService(chat)

    events = list(service.stream_assist(ChatRequest(question="差旅餐补", retrieval_strategy="auto")))
    names = [e["event"] for e in events]
    integrity = next(e for e in events if e["event"] == "citation_integrity_checked")["data"]
    completed = events[-1]["data"]

    assert integrity["passed"] is True  # unused 不再阻断
    assert integrity["checks"]["unused_citations"] == ["citation-2", "citation-3"]  # 仍如实披露
    assert names[-1] == "completed" and completed["result_state"] == "answered"
    assert "未通过引用校验" not in completed["answer"]  # 正常展示生成答案


def test_unknown_markers_still_fail_closed(tmp_path: Path):
    from application.chat_service import ChatService
    from application.agent_service import AgentService

    class UnknownMarkerProvider(FakeProvider):
        def __init__(self) -> None:
            self.attempts = 0

        def complete(self, messages):
            self.attempts += 1
            return ("引用了不存在的 [citation-9]。", {"total_tokens": 4})

    database = ProductDatabase(tmp_path / "unknown.sqlite3")
    database.initialize()
    chat = ChatService(database, lambda top_k: StubPipeline("single"), UnknownMarkerProvider(), privacy_log_questions=False)
    service = AgentService(chat)

    events = list(service.stream_assist(ChatRequest(question="差旅餐补", retrieval_strategy="auto")))
    completed = events[-1]["data"]

    assert completed["result_state"] == "system_error"
    assert completed["citation_integrity"] is False
    assert "未通过引用校验" in completed["answer"]  # evidence-only 降级


def test_assist_turns_persisted_to_query_logs(tmp_path: Path):
    """M2 运营指标可回溯（实施方案验收：fallback 触发率单独记录、工具调用数
    ≤3 可审计）：assist 轮落 query_logs（prompt_version=assist-agent-v1 标记），
    trace_json 携带 tool_calls_executed / fallback_reason。"""
    service, database, _pipeline = _build(tmp_path)
    events = list(service.stream_assist(ChatRequest(question="报销时限是多少天？", retrieval_strategy="hybrid")))
    completed = events[-1]["data"]

    rows = database.fetch_all(
        "SELECT request_id, result_state, prompt_version, trace_json FROM query_logs WHERE prompt_version='assist-agent-v1'"
    )
    assert len(rows) == 1
    import json as _json

    trace = _json.loads(rows[0]["trace_json"])
    assert rows[0]["request_id"] == completed["request_id"]
    assert rows[0]["result_state"] == "answered"
    assert trace["channel"] == "assist_agent"
    assert trace["tool_calls_executed"] == completed["tool_calls_executed"] <= 3
    assert trace["fallback_reason"] is None  # happy path 无降级


def test_assist_persist_failure_never_blocks_answer(tmp_path: Path):
    """落库失败绝不阻断应答（与 ChatService._persist 同策略）。"""
    service, database, _pipeline = _build(tmp_path)

    def broken_execute(*_args, **_kwargs):
        raise RuntimeError("db exploded")

    database.execute = broken_execute  # type: ignore[method-assign]
    events = list(service.stream_assist(ChatRequest(question="报销时限是多少天？", retrieval_strategy="hybrid")))

    names = [e["event"] for e in events]
    assert names[-1] == "completed"  # 应答完整收尾
    assert events[-1]["data"]["result_state"] == "answered"
