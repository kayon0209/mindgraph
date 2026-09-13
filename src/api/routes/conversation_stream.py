"""会话内续问流（M3-E 缺口修复：方案 §7.1 messages/stream）。

语义：owner 校验 → user 消息落库（稳定 sequence）→ 进程内复用
mindgraph_chat.stream（与 /mindgraph/chat 同源事件，绝不 HTTP 自调用）→
completed 事件时把 assistant 回答与 citations 快照按序落库。

PR-12：``CONVERSATION_SERVER_CONTEXT_ENABLED`` 打开时，服务端取最近窗口做
确定性续问解析（指代/槽位/纠错），``resolved_query`` 进检索与生成，
原文照常落库；SSE 先发 ``context_resolution`` 事件（替换证据 + 额外 token），
多轮上下文的成本由此可观测。flag 关闭 = 单轮语义（历史行为）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.auth import require_authenticated
from api.dependencies import get_container
from api.schemas.chat import ChatRequest
from api.sse import iter_sync_events
from application.conversation_service import ConversationNotFoundError, SequenceConflictError
from domain.models import RetrievalStrategy

logger = logging.getLogger("mindgraph.api.conversations_stream")

router = APIRouter(prefix="/mindgraph/conversations", tags=["conversations"])

_executor_imported = False


def _principal_id(principal: dict) -> str:
    return str(principal.get("name") or principal.get("username") or "anonymous")


class ConversationMessageRequest(BaseModel):
    """会话内续问：question 必填；其余检索参数与 ChatRequest 同语义。"""

    question: str = Field(min_length=1, max_length=2000)
    retrieval_strategy: RetrievalStrategy = "auto"
    final_top_k: int = Field(default=5, ge=1, le=50)
    query_date: str | None = None
    include_historical: bool = False
    graph_enabled: bool = False
    graph_hops: int = Field(default=1, ge=1, le=2)


@router.post("/{conversation_id}/messages/stream")
async def conversation_message_stream(
    conversation_id: str,
    payload: ConversationMessageRequest,
    request: Request,
    principal: dict = Depends(require_authenticated),
):
    container = get_container()
    service = container.conversation_service
    actor = _principal_id(principal)
    try:
        service.append_message(
            conversation_id=conversation_id, principal_id=actor,
            role="user", content=payload.question,
        )
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail="conversation not found") from None
    except SequenceConflictError as exc:
        logger.warning("conversation_stream_sequence_conflict", extra={"conversation_id": conversation_id})
        raise HTTPException(status_code=409, detail="concurrent update, retry") from exc

    chat_request = ChatRequest(
        question=payload.question,
        retrieval_strategy=payload.retrieval_strategy,
        final_top_k=payload.final_top_k,
        query_date=payload.query_date,
        include_historical=payload.include_historical,
        graph_enabled=payload.graph_enabled,
        graph_hops=payload.graph_hops,
        include_retrieval_trace=False,
    )

    # PR-12：服务端续问解析（flag 控制）。解析失败绝不阻断对话流——
    # 降级为单轮语义并留 warning，不让上下文层把问答打挂。
    resolution_payload: dict[str, Any] | None = None
    settings_context_enabled = False
    try:
        from infrastructure.settings import get_settings

        settings_context_enabled = bool(get_settings().CONVERSATION_SERVER_CONTEXT_ENABLED)
    except Exception:
        logger.warning("conversation_context_settings_unavailable", exc_info=True)
    if settings_context_enabled:
        try:
            from application.followup_resolver import ConversationContextService, FollowupResolver
            from infrastructure.settings import get_settings

            settings = get_settings()
            context_service = ConversationContextService(
                service,
                max_turns=settings.CONVERSATION_CONTEXT_MAX_TURNS,
                max_context_chars=settings.CONVERSATION_CONTEXT_MAX_CHARS,
            )
            window = context_service.recent_window(conversation_id, principal_id=actor)
            # 当前问题已在前面 append 进库——解析窗口必须排除它，否则指代
            # 会绑定到本次问题自己（resolved == 原文，解析失效）。
            window = [item for item in window if item.get("content") != payload.question]
            resolution = FollowupResolver().resolve(payload.question, window)
            if resolution.resolved_query and resolution.resolved_query != payload.question:
                chat_request.resolved_query = resolution.resolved_query
            resolution_payload = resolution.to_dict()
        except Exception:
            logger.exception("conversation_context_resolution_failed", extra={"conversation_id": conversation_id})
            resolution_payload = None

    async def generate():
        completed_payload: dict[str, Any] | None = None
        try:
            if resolution_payload is not None:
                # 上下文解析证据先于检索事件发出：前端可据此展示"续问被理解为…"
                context_event = {
                    "request_id": getattr(request.state, "request_id", None),
                    "event": "context_resolution",
                    "data": resolution_payload,
                }
                yield f"event: context_resolution\ndata: {json.dumps(context_event, ensure_ascii=False, default=str)}\n\n"
            async for item in iter_sync_events(
                lambda: get_container().mindgraph_chat.stream(chat_request),
                executor=_shared_executor(),
            ):
                if await request.is_disconnected():
                    break
                if item.get("event") == "completed":
                    completed_payload = item.get("data") or {}
                yield f"event: {item['event']}\ndata: {json.dumps(item, ensure_ascii=False, default=str)}\n\n"
        except Exception as exc:
            request_id = getattr(request.state, "request_id", None)
            logger.exception("conversation_stream_error", extra={"request_id": request_id, "error": str(exc)})
            error_payload = {"request_id": request_id, "event": "error", "data": {"code": "stream_error", "message": "Stream failed."}}
            yield f"event: error\ndata: {json.dumps(error_payload, ensure_ascii=False)}\n\n"
        finally:
            # 终态落库（含中断：把已生成的部分/错误态也按序保存，保证回放完整）
            if completed_payload:
                try:
                    service.append_message(
                        conversation_id=conversation_id, principal_id=actor,
                        role="assistant",
                        content=str(completed_payload.get("answer") or ""),
                        citations=completed_payload.get("citations") or [],
                        request_id=str(completed_payload.get("request_id") or "") or None,
                    )
                except Exception:
                    logger.exception("assistant_message_persist_failed", extra={"conversation_id": conversation_id})

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _shared_executor():
    global _executor_imported
    if not _executor_imported:
        from api.routes.assist import _executor as assist_executor

        globals()["_executor_ref"] = assist_executor
        _executor_imported = True
    return globals().get("_executor_ref")
