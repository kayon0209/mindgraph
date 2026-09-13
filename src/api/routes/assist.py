"""MindGraph Assist 路由（M1，受治理的 agent 交付面）。

复用 ChatService.answer/stream 进程内调用（绝不 HTTP/MCP 自调用），
与 /mindgraph/chat 的唯一差异：
- 挂在独立前缀 /assist（仅 ASSIST_ENABLED 时由 api.main 挂载）；
- 同步响应封装为带机器可判定 ``verdict`` 的 AssistResult 信封；
- 审计 action='assist'，供 agent 通道独立对账。

SSE 流式面与 /mindgraph/chat/stream 同源（同一 ChatService.stream 生成器、
同一信封），客户端按事件名 switch 即可复用。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime
import json
import logging
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from api.auth import current_actor, resolve_access_scope
from api.dependencies import get_container
from api.schemas.assist import AssistRequest, AssistResult
from api.sse import iter_sync_events
from application.access_control import record_access_audit
from domain.contracts import error_event_data
from domain.models import ErrorCode, ResultState, TimingMetrics, error_code_for_result_state
from infrastructure.settings import get_settings

logger = logging.getLogger("mindgraph.api.assist")
_executor = ThreadPoolExecutor(max_workers=4)

router = APIRouter(prefix="/assist", tags=["assist"])

ASSIST_TIMEOUT_MESSAGE = "处理超时：本次请求未在限定时间内完成，请稍后重试或缩小问题范围。"


@router.post("/clarifications/{clarification_id}/resume")
async def resume_clarification(clarification_id: str, request: Request):
    """PR-13：可恢复澄清协议的 resume 端点。

    语义（与 ClarificationService 状态机一一对应，全部是 **200 业务态**——
    状态机可判定，不靠 4xx 猜测；SSE/轮询客户端拿到的永远是结构化结果）：
    - ``resumed`` / ``already_consumed`` / ``expired`` / ``not_found``；
    - ``server_misconfigured``：部署缺 ``MINDGRAPH_CLARIFICATION_SALT``（PR-13
      验收发现的坑）。它与 ``not_found`` 严格区分——否则"系统没配好"会被当成
      "卡片不存在/过期"，排查方向完全错。判定在归属/过期/已消费之后，非 owner
      与不存在的 id 仍然只得到 ``not_found``；
    - 跨主体与不存在不可区分（``not_found``，不暴露存在性——PR-02 语义原则）；
    - resume 成功返回原问题集 + 原检索预算；重复恢复幂等，不重复副作用。
    """
    import json as _json

    from pydantic import BaseModel, Field

    class ResumeBody(BaseModel):
        answers: list[str] = Field(default_factory=list, max_length=10)
        conversation_id: str | None = Field(default=None, max_length=100)

    container = get_container()
    # 空 body 合法（只查询状态）：默认 {}
    raw = await request.body()
    payload = _json.loads(raw or b"{}")
    body = ResumeBody(**payload)

    from application.clarification_service import ClarificationService

    service = ClarificationService(container.database)
    outcome = service.resume(
        clarification_id=clarification_id,
        principal_id=current_actor(request),
        answers=body.answers,
        conversation_id=body.conversation_id,
    )
    record_access_audit(
        container.database,
        actor=current_actor(request),
        action="clarification_resume",
        resource=f"assist/clarifications/{clarification_id}",
        decision="allow",
        metadata={"state": outcome.state, "answers_count": len(body.answers)},
    )
    return outcome.to_dict()


@router.post("", response_model=AssistResult)
def assist(payload: AssistRequest, request: Request) -> AssistResult:
    """同步 Assist：进程内复用 ChatService.answer，返回带 verdict 的信封。

    应用服务在独立线程池执行，超时后返回 ``verdict=timeout`` 信封（不抛
    5xx，agent 只读 verdict 即可判定）；后台任务继续跑完并照常落 query_logs，
    但结果不再回传客户端。
    """
    scope = resolve_access_scope(request)
    container = get_container()
    audit_metadata = {"scope_user": (scope or {}).get("user")}
    if container.privacy_log:
        audit_metadata["question"] = payload.question[:80]
    record_access_audit(
        container.database,
        actor=current_actor(request),
        action="assist",
        resource="assist",
        decision="allow",
        metadata=audit_metadata,
    )
    started = time.perf_counter()
    timeout_seconds = get_settings().ASSIST_TIMEOUT_SECONDS
    future = _executor.submit(container.mindgraph_chat.answer, payload, access_scope=scope)
    try:
        result = future.result(timeout=timeout_seconds)
    except FutureTimeoutError:
        logger.error(
            "assist_timeout",
            extra={
                "request_id": getattr(request.state, "request_id", None),
                "timeout_seconds": timeout_seconds,
            },
        )
        return _timeout_envelope(payload, started, timeout_seconds)
    except Exception as exc:
        logger.exception("assist_answer_failed", extra={"error": str(exc)})
        return _timeout_envelope(payload, started, timeout_seconds, message="处理失败：内部错误。请稍后重试。", verdict="system_error")
    return AssistResult(**result.model_dump(mode="json"), verdict=error_code_for_result_state(result.result_state))


def _timeout_envelope(
    payload: AssistRequest,
    started: float,
    timeout_seconds: float,
    *,
    message: str = ASSIST_TIMEOUT_MESSAGE,
    verdict: str = "timeout",
) -> AssistResult:
    """构造返回给 agent 的机器可判定失败信封（不依赖后台任务的结果）。"""
    return AssistResult(
        request_id=str(uuid.uuid4()),
        question=payload.question,
        answer=message,
        result_state=ResultState.system_error,
        error_code=ErrorCode(verdict),
        citation_fidelity=None,
        citations=[],
        retrieval_trace=None,
        timing=TimingMetrics(total_ms=round((time.perf_counter() - started) * 1000, 3)),
        requested_strategy=payload.retrieval_strategy,
        actual_strategy="assist_timeout",
        degraded=True,
        degradation_reason=f"assist_timeout:{timeout_seconds}s",
        model="",
        verdict=ErrorCode(verdict),
    )


@router.post("/stream")
async def assist_stream(payload: AssistRequest, request: Request):
    """流式 Assist：进程内复用 ChatService.stream（SSE 事件与 /chat 同源）。"""
    scope = resolve_access_scope(request)
    container = get_container()

    async def generate():
        try:
            async for item in iter_sync_events(
                lambda: get_container().mindgraph_chat.stream(payload, access_scope=scope),
                executor=_executor,
            ):
                if await request.is_disconnected():
                    break
                yield f"event: {item['event']}\ndata: {json.dumps(item, ensure_ascii=False, default=str)}\n\n"
        except Exception as exc:
            request_id = getattr(request.state, "request_id", None)
            logger.exception("assist_stream_error", extra={"request_id": request_id, "error": str(exc)})
            error_payload = {
                "request_id": request_id,
                "event": "error",
                "timestamp": datetime.now(UTC).isoformat(),
                "data": error_event_data("stream_error", "Stream failed — check server logs for details."),
            }
            yield f"event: error\ndata: {json.dumps(error_payload, ensure_ascii=False)}\n\n"

    # 流式通道同样落审计（action='assist'，decision 由事件结果决定：completed 前不预判）
    record_access_audit(
        container.database,
        actor=current_actor(request),
        action="assist_stream",
        resource="assist/stream",
        decision="allow",
        metadata={"scope_user": (scope or {}).get("user"), "stream": True},
    )
    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/agent/stream")
async def assist_agent_stream(payload: AssistRequest, request: Request):
    """M2：确定性 Assist Agent 流（AGENT_ASSIST_ENABLED 默认关闭时 404）。

    与 /assist/stream 的差异：经 AgentService 确定性编排（plan/tool 轨迹/
    澄清协议/引用完整性门），复用同一应用服务层，无 HTTP/MCP 自调用。
    事件序列见 domain/contracts.SSE_EVENT_NAMES 的 M2 段；旧客户端忽略未知事件。
    """
    from fastapi import HTTPException

    from infrastructure.settings import get_settings

    if not get_settings().AGENT_ASSIST_ENABLED:
        raise HTTPException(status_code=404, detail="assist agent is disabled")

    scope = resolve_access_scope(request)
    actor = current_actor(request)
    container = get_container()
    record_access_audit(
        container.database,
        actor=actor,
        action="assist_stream",
        resource="assist/agent/stream",
        decision="allow",
        metadata={"scope_user": (scope or {}).get("user"), "mode": "agent"},
    )

    async def generate():
        try:
            async for item in iter_sync_events(
                lambda: get_container().agent_service.stream_assist(payload, access_scope=scope),
                executor=_executor,
            ):
                if await request.is_disconnected():
                    break
                yield f"event: {item['event']}\ndata: {json.dumps(item, ensure_ascii=False, default=str)}\n\n"
        except Exception as exc:
            request_id = getattr(request.state, "request_id", None)
            logger.exception("assist_agent_stream_error", extra={"request_id": request_id, "error": str(exc)})
            error_payload = {
                "request_id": request_id,
                "event": "error",
                "timestamp": datetime.now(UTC).isoformat(),
                "data": error_event_data("stream_error", "Stream failed — check server logs for details."),
            }
            yield f"event: error\ndata: {json.dumps(error_payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
