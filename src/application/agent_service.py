"""AgentService：受治理的确定性 Assist 编排（M2，实施方案 §4）。

设计（ADR-003 红线）：
- 只调用内部应用服务（EvidenceQueryService / provider complete-stream），绝不
  通过 HTTP/MCP 自调用；
- 不做 Provider function calling（tools/tool_choice 不出现），工具选择由
  AgentExecutionPolicy 的静态映射决定；
- 实际工具调用数 ≤ AGENT_MAX_TOOL_CALLS（超出即 loop_fell_back 回到单遍，
  不是继续执行）；
- fail-closed：冲突（过门后）、无证据、无权限任一命中，立即停止，不生成；
- citation integrity：为守住"未校验的 token 不得展示为可信答案"，生成阶段在
  服务端缓冲完整输出，CitationIntegrityValidator 通过后才发 answer_delta；
  失败允许一次重生成，仍失败则 citation_integrity_failed + evidence-only。

澄清协议（P0-1 诚实化）：clarification_required 携带 clarification_id /
questions / context_hash / expires_at；随后 completed(result_state=
waiting_for_input) 正常关流。**当前没有服务端恢复**：用户补充后，前端把
补充信息拼进原问题，作为一次全新的 assist 请求提交（"新的补充问题请求"）。
clarification_id / context_hash 仅作为澄清卡的定位与过期标记，不由后端
resume_from 校验消费（服务端无该字段、无校验调用点）；真实的服务端
conversation resume 需要独立的 clarification_requests 持久化契约（数据库
schema 授权后另行实现），不在本层预埋半成品 API。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from application.agent_execution_policy import ExecutionPlan, ExecutionStep
from application.chat_service import ChatService
from application.citation_integrity import CitationIntegrityValidator
from application.evidence_query_service import EvidenceQueryService, EvidenceQueryResult
from domain.errors import RetrievalUnavailableError
from domain.evidence import EvidenceResultState
from domain.models import ChatRequest, Citation, ResultState

logger = logging.getLogger("mindgraph.agent")

CLARIFICATION_TTL_MINUTES = 30


def _clarification_salt() -> bytes:
    """澄清 token 的 HMAC 盐（审查 F10）：部署经 MINDGRAPH_CLARIFICATION_SALT
    注入；缺省时进程级随机——源码可见的固定盐不可用于伪造。当前 token 仅用于
    澄清卡展示面的定位与过期判定（前端拼接新请求，不做服务端恢复），跨进程
    一致性无消费方；若未来引入服务端 resume 校验，需先落地独立的
    clarification_requests 持久化契约（见模块 docstring）。"""
    import os as _os
    import secrets as _secrets

    salt = _os.getenv("MINDGRAPH_CLARIFICATION_SALT", "")
    if not salt:
        cached = getattr(_clarification_salt, "_random", None)
        if cached is None:
            cached = _secrets.token_hex(32)
            _clarification_salt._random = cached  # type: ignore[attr-defined]
        salt = cached
    return salt.encode()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def make_clarification_token(conversation_key: str, questions: list[str]) -> tuple[str, str, str]:
    """生成 clarification_id + context_hash + expires_at（签名防伪造）。

    context_hash 绑定会话+问题集，clarification_id 是随机定位符（不参与
    签名）。当前无服务端 resume 消费方——见模块 docstring。"""
    payload = "|".join(questions)
    context_hash = hmac.new(
        _clarification_salt(), (conversation_key + payload).encode(), hashlib.sha256
    ).hexdigest()[:16]
    clarification_id = uuid.uuid4().hex[:12]
    expires_at = (datetime.now(UTC) + timedelta(minutes=CLARIFICATION_TTL_MINUTES)).isoformat()
    return clarification_id, context_hash, expires_at


def verify_clarification_token(clarification_id: str, context_hash: str, *, expires_at: str, conversation_key: str, questions: list[str]) -> bool:
    """澄清 token 一致性校验（过期 + 签名）。

    P0-1 诚实化：当前生产路径没有调用点（无服务端恢复）；保留为 token
    语义的确定性定义与测试面。真实 resume 必须等 clarification_requests
    持久化契约授权后接入，不得在无存储的情况下信任客户端回传。"""
    try:
        if datetime.fromisoformat(expires_at) < datetime.now(UTC):
            return False
    except ValueError:
        return False
    _, expected_hash, _ = make_clarification_token(conversation_key, questions)
    return hmac.compare_digest(expected_hash, context_hash)


class AgentService:
    """确定性 assist 编排。入口：stream_assist（SSE 事件生成器）。"""

    def __init__(self, chat_service: ChatService, *, max_tool_calls: int = 3) -> None:
        self.chat_service = chat_service
        self.evidence = EvidenceQueryService(chat_service)
        self.max_tool_calls = max(1, int(max_tool_calls))

    def _event(self, request_id: str, name: str, data: dict[str, Any]) -> dict[str, Any]:
        return {"request_id": request_id, "event": name, "timestamp": _now_iso(), "data": data}

    def stream_assist(
        self,
        request: ChatRequest,
        *,
        access_scope: dict | None = None,
    ) -> Iterable[dict[str, Any]]:
        """受治理 assist 流。事件序列见 domain/contracts.SSE_EVENT_NAMES 的 M2 段。"""
        started = time.perf_counter()
        request_id = str(uuid.uuid4())
        yield self._event(request_id, "request_started", {"strategy": request.retrieval_strategy})

        # ── 路由与计划（plan_created） ──
        try:
            decision, routing_ms = self.chat_service._route(request)
        except Exception:
            logger.exception("assist_routing_failed", extra={"request_id": request_id})
            yield self._event(request_id, "error", {"code": "retrieval_unavailable", "message": "检索服务暂不可用，请稍后重试。"})
            return
        from application.agent_execution_policy import AgentExecutionPolicy

        policy = AgentExecutionPolicy()
        plan = policy.plan_for_route(decision.route, [code.value for code in decision.reasons])
        tool_budget = plan.budget_steps()
        fallback_reason: str | None = None
        if len(tool_budget) > self.max_tool_calls:
            # 预算超限不是继续执行：降级为单遍（loop_fell_back）
            fallback_reason = "tool_budget_exceeded"
            yield self._event(request_id, "loop_fell_back", {"reason": fallback_reason, "route": decision.route})
            plan = policy.plan_for_route("manual", [])
        yield self._event(
            request_id, "plan_created",
            {
                "steps": [{"name": step.name, "label": step.label} for step in plan.steps],
                "route": decision.route,
                "reason_codes": [code.value for code in decision.reasons],
                "routing_ms": routing_ms,
            },
        )

        # ── 澄清路由特例：结构化提问，不检索 ──
        # P0-1：此路径只发 clarification_required + completed(waiting_for_input)
        # 后关流，不产出 answer_delta；用户补充后由前端拼成新请求重新提交。
        if decision.route == "clarification_required":
            questions = self._clarification_questions(request.question)
            clarification_id, context_hash, expires_at = make_clarification_token(request.question, questions)
            yield self._event(
                request_id, "clarification_required",
                {
                    "clarification_id": clarification_id,
                    "questions": questions,
                    "context_hash": context_hash,
                    "expires_at": expires_at,
                },
            )
            yield self._event(request_id, "completed", {"result_state": "waiting_for_input", "request_id": request_id})
            return

        # ── 步骤执行 ──
        result: EvidenceQueryResult | None = None
        executed = 0
        gate_reached = False
        used_names: list[str] = []
        principal = (access_scope or {}).get("user") if access_scope else None
        for index, step in enumerate(plan.steps):
            if not step.counts_toward_budget:
                break  # finalize 由生成段处理
            if executed >= self.max_tool_calls:
                fallback_reason = fallback_reason or "tool_budget_exceeded"
                yield self._event(request_id, "loop_fell_back", {"reason": "tool_budget_exceeded", "after": step.name})
                break
            yield self._event(request_id, "tool_call_started", {"step": step.name, "label": step.label, "index": index})
            started_step = time.perf_counter()
            try:
                result = self._execute_step(step, request, access_scope, result)
            except RetrievalUnavailableError:
                yield self._event(
                    request_id, "tool_call_finished",
                    {"step": step.name, "status": "error", "error": "retrieval_unavailable", "latency_ms": round((time.perf_counter() - started_step) * 1000, 1)},
                )
                yield self._event(request_id, "error", {"code": "retrieval_unavailable", "message": "检索服务暂不可用，请稍后重试。"})
                return
            if step.name in {"check_conflicts", "resolve_version"}:
                gate_reached = True
            executed += 1
            used_names.append(step.name)
            state = result.bundle.result_state
            status = "ok"
            if state in {EvidenceResultState.insufficient_evidence, EvidenceResultState.permission_denied, EvidenceResultState.conflicting_evidence}:
                status = "denied" if state is EvidenceResultState.permission_denied else "failed"
            yield self._event(
                request_id, "tool_call_finished",
                {"step": step.name, "status": status, "result_state": state.value, "latency_ms": round((time.perf_counter() - started_step) * 1000, 1)},
            )
            if policy.should_halt(state, gate_reached=gate_reached):
                # fail-closed：不扩图、不生成，走现有终态呈现
                for event in self._terminal_without_generation(request_id, request, result, started, tool_calls_executed=executed, principal=principal):
                    yield event
                return

        if result is None:
            result = self.evidence.query(request, access_scope=access_scope)

        # ── 生成段（服务端缓冲 + 完整性校验后才发 answer_delta） ──
        provider = self.chat_service._provider(request.chat_provider, request.chat_model)
        citations = result.citations
        if not provider.available:
            text = "已找到相关制度证据，但生成模型未配置。请直接查看引用。"
            yield self._event(request_id, "answer_delta", {"text": text, "stream_mode": "deterministic"})
            for event in self._finalize_completed(request_id, request, result, text, ResultState.model_unavailable, started,
                                                  tool_calls_executed=executed, fallback_reason=fallback_reason, principal=principal):
                yield event
            return

        messages = self.chat_service._messages(request.question, citations, None)
        answer, usage = provider.complete(messages)
        integrity_ok, integrity_report = self._check_integrity(answer, citations)
        if not integrity_ok:
            # 一次重生成（约束提示）
            retry_messages = list(messages)
            retry_messages[0] = {"role": "system", "content": messages[0]["content"] + "\n注意：只能使用给定的 citation ID 标注，不得发明新编号。"}
            answer, usage = provider.complete(retry_messages)
            integrity_ok, integrity_report = self._check_integrity(answer, citations)
        yield self._event(
            request_id, "citation_integrity_checked",
            {"passed": integrity_ok, "applicable": integrity_report.applicable, "checks": {
                "unknown_markers": integrity_report.unknown_markers,
                "duplicate_markers": integrity_report.duplicate_markers,
                "malformed_markers": integrity_report.malformed_markers,
                "unused_citations": integrity_report.unused_citations,
            }},
        )
        if not integrity_ok:
            # citation_integrity_failed：不把文本展示为可信结论，回 evidence-only
            fallback_reason = "citation_integrity_failed"
            yield self._event(request_id, "loop_fell_back", {"reason": fallback_reason})
            for event in self._finalize_completed(
                request_id, request, result, "本次回答未通过引用校验，已改为仅显示证据。", ResultState.system_error, started,
                integrity_failed=True, tool_calls_executed=executed, fallback_reason=fallback_reason, principal=principal,
            ):
                yield event
            return
        # 校验通过后才发正文
        yield self._event(request_id, "answer_delta", {"text": answer, "stream_mode": "provider_native"})
        for event in self._finalize_completed(request_id, request, result, answer, ResultState.answered, started,
                                              usage=usage, tool_calls_executed=executed, fallback_reason=fallback_reason, principal=principal):
            yield event

    # ── 步骤执行器 ──

    def _execute_step(self, step: ExecutionStep, request: ChatRequest, access_scope: dict | None, previous: EvidenceQueryResult | None) -> EvidenceQueryResult:
        """执行单个预算步骤。性能修正（审查发现）：check_conflicts /
        resolve_version 复用上一步的检索结果，只增量复查冲突——此前每步
        重跑完整 route→retrieve→citations→conflicts，factual 请求重复
        检索 2 次、cross_policy 3 次（嵌入检索是最贵操作，P95 直接翻倍）。

        - retrieve_evidence：首次全链检索；
        - check_conflicts：基于 previous 的 citations 增量复查版本冲突
          （唯一可能变化的部分），不重检索；
        - resolve_version：复用 previous（版本族在冲突服务里按 policy_key
          增量核对，无需新检索）；
        - expand_relations：以 graph_enabled=True 做一次新检索（真正需要
          不同检索面的唯一步骤）。
        """
        if step.name in {"check_conflicts", "resolve_version"} and previous is not None:
            conflicts = self.evidence.policy_conflict_service.find_for_policy_keys(
                {item.policy_key for item in previous.citations if item.policy_key},
                as_of=request.query_date,
                include_historical=request.include_historical,
                access_scope=access_scope,
            )
            return self.evidence.rebundle_with_conflicts(previous, conflicts)
        if step.name == "expand_relations" and previous is not None:
            graph_request = request.model_copy(update={"graph_enabled": True})
            return self.evidence.query(graph_request, access_scope=access_scope)
        return self.evidence.query(request, access_scope=access_scope)

    def _clarification_questions(self, question: str) -> list[str]:
        """结构化澄清问题（确定性模板，无 LLM）。"""
        questions = ["你要查的是哪一项制度或费用类型？（例如差旅、招待、办公采购）"]
        if any(term in question for term in ("哪个", "哪个版本", "新旧", "最新")):
            questions.append("你关注的具体生效日期或版本是？（例如 2026 年 7 月 1 日之后）")
        return questions[:3]

    def _check_integrity(self, answer: str, citations: list[Citation]):
        """生成门校验：与 chat 通道的 fail-closed 语义对齐。

        阻断项 = 答案标注失真三害：越界（unknown）/畸形（malformed）/重复
        （duplicate）——正文引用了不存在的标注即失真。``unused_citations``
        （检索多返回但正文未引用）不是失真，只进 citation_integrity_checked
        事件的 checks 供展示，不触发 evidence-only 降级；完整视图
        （含 unused）仍由 answer evaluation 的 citation_marker_validity 指标
        度量。语义支持属评测侧 claim-support，本门不声称验证。
        """
        validator = CitationIntegrityValidator(citation_ids=[item.citation_id for item in citations])
        report = validator.validate(answer)
        fatal = bool(report.unknown_markers or report.malformed_markers or report.duplicate_markers)
        return (not fatal) and bool(report.applicable), report

    # ── 终态组装 ──

    def _terminal_without_generation(self, request_id: str, request: ChatRequest, result: EvidenceQueryResult, started: float, *, tool_calls_executed: int = 0, principal: str | None = None) -> Iterable[dict[str, Any]]:
        state = result.bundle.result_state
        if state is EvidenceResultState.conflicting_evidence:
            text = "检测到同一制度在查询日期存在多个有效版本，已停止生成答案。请由制度责任人确认有效版本。"
            final_state = ResultState.conflicting_evidence
        elif state is EvidenceResultState.permission_denied:
            text = "当前账号没有权限访问相关制度内容。请联系管理员申请对应工作区/部门的访问权限。"
            final_state = ResultState.permission_denied
        else:
            text = "未在制度文件中找到足够依据。建议联系 HR/财务确认。"
            final_state = ResultState.insufficient_evidence
        yield self._event(request_id, "answer_delta", {"text": text, "stream_mode": "deterministic"})
        yield from self._finalize_completed(request_id, request, result, text, final_state, started, tool_calls_executed=tool_calls_executed, principal=principal)

    def _finalize_completed(
        self, request_id: str, request: ChatRequest, result: EvidenceQueryResult, answer: str, state: ResultState, started: float,
        *, usage: dict[str, Any] | None = None, integrity_failed: bool = False,
        tool_calls_executed: int = 0, fallback_reason: str | None = None,
        principal: str | None = None,
    ) -> Iterable[dict[str, Any]]:
        payload: dict[str, Any] = {
            "request_id": request_id,
            "question": request.question,
            "answer": answer,
            "result_state": state.value,
            "citations": [item.model_dump(mode="json") for item in result.citations],
            "actual_strategy": result.bundle.route.selected_strategy if result.bundle.route else None,
            "index_version": result.bundle.index_version,
            "citation_integrity": False if integrity_failed else None,
            "total_ms": round((time.perf_counter() - started) * 1000, 1),
            # assist 运营指标（M2 验收：工具调用数 ≤3 可事后审计、fallback 率可统计）
            "tool_calls_executed": tool_calls_executed,
            "fallback_reason": fallback_reason,
        }
        if usage:
            payload["usage"] = usage
        self._persist_assist(request_id, request, payload, principal)
        yield self._event(request_id, "citations", {"citations": payload["citations"]})
        yield self._event(request_id, "completed", payload)

    def _persist_assist(self, request_id: str, request: ChatRequest, payload: dict[str, Any], principal: str | None = None) -> None:
        """assist 轮落 query_logs（prompt_version=assist-agent-v1 标记渠道）：
        M2 验收要求 fallback 触发率与工具调用数可回溯统计，仅靠 SSE 事件无法
        事后查询。落库失败绝不阻断应答（与 ChatService._persist 同策略）。
        principal_id 与 ChatService._persist_or_raise 同口径（PR-02：assist 渠道
        归属此前恒 NULL，导致反馈面 fail-closed 全拒）。"""
        import hashlib

        from infrastructure.database import dumps

        try:
            question = request.question if self.chat_service.privacy_log_questions else None
            trace_payload = {
                "channel": "assist_agent",
                "tool_calls_executed": payload.get("tool_calls_executed"),
                "fallback_reason": payload.get("fallback_reason"),
                "citation_integrity": payload.get("citation_integrity"),
            }
            self.chat_service.database.execute(
                """INSERT INTO query_logs (
                    request_id, question, question_hash, answer, result_state, requested_strategy, actual_strategy,
                    trace_json, citations_json, timing_json, usage_json, created_at, index_version, prompt_version,
                    requested_provider, actual_provider, query_date, category_filter_json, principal_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    request_id, question,
                    hashlib.sha256((request.question + "mindgraph-question-salt").encode()).hexdigest(),
                    str(payload.get("answer") or ""), payload.get("result_state", "system_error"),
                    request.retrieval_strategy, payload.get("actual_strategy") or "assist",
                    dumps(trace_payload), dumps(payload.get("citations") or []),
                    dumps({"total_ms": payload.get("total_ms")}), dumps(payload.get("usage") or {}),
                    datetime.now(UTC).isoformat(), payload.get("index_version"), "assist-agent-v1",
                    request.chat_provider or "", "assist",
                    request.query_date, dumps([]),
                    principal or "anonymous",
                ),
            )
        except Exception:
            logger.exception("assist_query_log_persist_failed", extra={"request_id": request_id})
