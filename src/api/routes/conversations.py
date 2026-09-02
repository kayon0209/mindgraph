"""服务端会话 API（M3，实施方案 §7.1）。挂载于 /api/v1/mindgraph，全部
经 require_authenticated；CONVERSATION_PERSISTENCE_ENABLED 默认关闭，
关闭时路由不挂载（404）。principal_id 取 auth 的稳定用户标识。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth import require_authenticated
from api.dependencies import get_container
from application.conversation_service import ConversationNotFoundError, SequenceConflictError

logger = logging.getLogger("mindgraph.api.conversations")

router = APIRouter(prefix="/mindgraph/conversations", tags=["conversations"])


def _principal_id(request: Request, principal: dict) -> str:
    """稳定主体标识：取认证主体名（与 require_authenticated 同源，
    避免 get_optional_principal 在 off 模式下的另一套命名）。"""
    return str(principal.get("name") or principal.get("username") or "anonymous")


class CreateConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    workspace: str | None = None
    department: str | None = None


class ImportTurnsRequest(BaseModel):
    """显式迁移（方案 §8.2）：前端逐会话上传本地轮次，服务端返回映射表。"""

    turns: list[dict[str, Any]] = Field(min_length=1, max_length=500)


@router.post("")
def create_conversation(payload: CreateConversationRequest, request: Request, principal: dict = Depends(require_authenticated)) -> dict[str, Any]:
    service = get_container().conversation_service
    return service.create_conversation(
        principal_id=_principal_id(request, principal),
        title=payload.title,
        workspace=payload.workspace,
        department=payload.department,
    )


@router.get("")
def list_conversations(request: Request, cursor: str | None = None, limit: int = 50, principal: dict = Depends(require_authenticated)) -> dict[str, Any]:
    service = get_container().conversation_service
    return service.list_conversations(principal_id=_principal_id(request, principal), cursor=cursor, limit=limit)


@router.get("/{conversation_id}/messages")
def get_messages(conversation_id: str, request: Request, principal: dict = Depends(require_authenticated)) -> list[dict[str, Any]]:
    service = get_container().conversation_service
    try:
        return service.get_messages(conversation_id=conversation_id, principal_id=_principal_id(request, principal))
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail="conversation not found") from None


@router.delete("/{conversation_id}")
def archive_conversation(conversation_id: str, request: Request, principal: dict = Depends(require_authenticated)) -> dict[str, str]:
    service = get_container().conversation_service
    try:
        service.archive_conversation(conversation_id=conversation_id, principal_id=_principal_id(request, principal))
        return {"status": "archived"}
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail="conversation not found") from None


@router.post("/{conversation_id}/import-turns")
def import_turns(conversation_id: str, payload: ImportTurnsRequest, request: Request, principal: dict = Depends(require_authenticated)) -> dict[str, Any]:
    """显式迁移入口：幂等导入本地轮次（local_turn_id 查重）。"""
    service = get_container().conversation_service
    try:
        return service.import_local_turns(
            conversation_id=conversation_id,
            principal_id=_principal_id(request, principal),
            turns=payload.turns,
        )
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail="conversation not found") from None
    except SequenceConflictError as exc:
        logger.warning("import_sequence_conflict", extra={"conversation_id": conversation_id})
        raise HTTPException(status_code=409, detail="concurrent update, retry") from exc
