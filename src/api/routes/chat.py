from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from concurrent.futures import ThreadPoolExecutor

from api.dependencies import get_container
from api.schemas.chat import AnswerResult, ChatRequest
from api.auth import resolve_access_scope

logger = logging.getLogger("mindgraph.api.chat")
_executor = ThreadPoolExecutor(max_workers=4)


router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=AnswerResult)
def chat(payload: ChatRequest, request: Request):
    scope = resolve_access_scope(request)
    return get_container().chat.answer(payload, access_scope=scope)


@router.post("/stream")
async def chat_stream(payload: ChatRequest, request: Request):
    scope = resolve_access_scope(request)

    async def generate():
        try:
            loop = asyncio.get_running_loop()
            for item in await loop.run_in_executor(_executor, lambda: list(get_container().chat.stream(payload, access_scope=scope))):
                if await request.is_disconnected():
                    break
                yield f"event: {item['event']}\ndata: {json.dumps(item, ensure_ascii=False, default=str)}\n\n"
        except Exception as exc:
            request_id = getattr(request.state, "request_id", None)
            logger.exception("chat_stream_error", extra={"request_id": request_id, "error": str(exc)})
            error_payload = {
                "request_id": request_id,
                "event": "error",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": {"code": "stream_error", "message": "Stream failed — check server logs for details."},
            }
            yield f"event: error\ndata: {json.dumps(error_payload, ensure_ascii=False)}\n\n"
    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
