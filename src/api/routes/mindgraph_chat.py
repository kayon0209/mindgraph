"""MindGraph 可信问答 API（M1-D4）。

复用现有 ChatService 的 answer/stream 逻辑，但走 MindGraph 检索管线
（Hybrid + 图谱一跳扩展 + 关系证据注入）。挂载于 /api/v1/mindgraph/chat。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from api.auth import current_actor, resolve_access_scope
from api.dependencies import get_container
from api.schemas.chat import AnswerResult, ChatRequest
from api.sse import iter_sync_events
from application.access_control import record_access_audit

logger = logging.getLogger("mindgraph.api.chat")
_executor = ThreadPoolExecutor(max_workers=4)

router = APIRouter(prefix="/mindgraph/chat", tags=["mindgraph-chat"])


@router.post("", response_model=AnswerResult)
def mindgraph_chat(request: Request, payload: ChatRequest):
    scope = resolve_access_scope(request)
    actor = current_actor(request)
    record_access_audit(
        get_container().database,
        actor=actor,
        action="chat",
        resource="mindgraph/chat",
        decision="allow",
        metadata={"scope_user": (scope or {}).get("user"), "question": payload.question[:80]},
    )
    return get_container().mindgraph_chat.answer(payload, access_scope=scope)


@router.post("/stream")
async def mindgraph_chat_stream(payload: ChatRequest, request: Request):
    scope = resolve_access_scope(request)

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
            logger.exception("mindgraph_chat_stream_error", extra={"request_id": request_id, "error": str(exc)})
            error_payload = {
                "request_id": request_id,
                "event": "error",
                "timestamp": datetime.now(UTC).isoformat(),
                "data": {"code": "stream_error", "message": "Stream failed — check server logs for details."},
            }
            yield f"event: error\ndata: {json.dumps(error_payload, ensure_ascii=False)}\n\n"
    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
