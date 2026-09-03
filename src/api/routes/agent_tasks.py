"""Agent 任务 API（M4-A，ADR-004 §API）。AGENT_TASKS_ENABLED 默认关闭。

POST /agent/tasks：头 Idempotency-Key 必填；重复提交返回原任务（幂等）。
轮询详情；无任务 SSE（事件持久化回放后才有）；无 approve/reject（M5-A 增量）。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth import require_authenticated
from api.dependencies import get_container
from application.task_service import InvalidTaskConstraints, TaskNotFoundError

logger = logging.getLogger("mindgraph.api.agent_tasks")

router = APIRouter(prefix="/mindgraph/agent/tasks", tags=["agent-tasks"])


def _principal_id(principal: dict) -> str:
    return str(principal.get("name") or principal.get("username") or "anonymous")


class SubmitTaskRequest(BaseModel):
    task_type: str = "batch_policy_check"
    constraints: dict[str, Any] = Field(default_factory=dict)


@router.post("")
def submit_task(
    payload: SubmitTaskRequest,
    request: Request,
    principal: dict = Depends(require_authenticated),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    if not idempotency_key or len(idempotency_key.strip()) < 8:
        raise HTTPException(status_code=400, detail="Idempotency-Key header (>=8 chars) is required")
    service = get_container().task_service
    try:
        return service.submit(
            principal_id=_principal_id(principal),
            idempotency_key=idempotency_key.strip(),
            task_type=payload.task_type,
            constraints=payload.constraints,
            workspace=principal.get("workspace"),
            department=principal.get("department"),
        )
    except InvalidTaskConstraints as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("")
def list_tasks(
    request: Request,
    cursor: str | None = None,
    limit: int = 50,
    principal: dict = Depends(require_authenticated),
) -> dict[str, Any]:
    service = get_container().task_service
    try:
        return service.list_tasks(principal_id=_principal_id(principal), cursor=cursor, limit=limit)
    except TaskNotFoundError:
        # 审查 F8：非法/过期 cursor 不应 500+堆栈，统一 400 语义
        raise HTTPException(status_code=400, detail="invalid cursor") from None


@router.get("/{task_id}")
def get_task(task_id: str, request: Request, principal: dict = Depends(require_authenticated)) -> dict[str, Any]:
    service = get_container().task_service
    try:
        return service.get_task(task_id=task_id, principal_id=_principal_id(principal))
    except TaskNotFoundError:
        raise HTTPException(status_code=404, detail="task not found") from None


@router.get("/{task_id}/artifacts/{artifact_id}")
def get_artifact(
    task_id: str,
    artifact_id: str,
    request: Request,
    principal: dict = Depends(require_authenticated),
) -> dict[str, Any]:
    """private artifact 内容下载（owner 校验；下载是普通显式用户动作，无伪审批）。"""
    service = get_container().task_service
    try:
        content = service.get_artifact_content(artifact_id=artifact_id, principal_id=_principal_id(principal))
    except TaskNotFoundError:
        raise HTTPException(status_code=404, detail="artifact not found") from None
    if content["task_id"] != task_id:
        raise HTTPException(status_code=404, detail="artifact not found") from None
    return content


@router.post("/{task_id}/cancel")
def cancel_task(task_id: str, request: Request, principal: dict = Depends(require_authenticated)) -> dict[str, Any]:
    service = get_container().task_service
    try:
        return service.cancel_task(task_id=task_id, principal_id=_principal_id(principal))
    except TaskNotFoundError:
        raise HTTPException(status_code=404, detail="task not found") from None
