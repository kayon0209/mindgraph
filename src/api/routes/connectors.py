"""企业连接器 API（Phase 5-2）。

挂载于 /api/v1/connectors。当前实现「本地目录 / Markdown 目录」增量同步器：
- POST /connectors/directories：同步一个本地 Markdown 目录；
- GET /connectors/directories/status：查看同步历史；
- GET /connectors/directories/{connector_id}：查看单次同步详情。

同步逻辑复用 VaultSyncService（增量 upsert + 剪枝 + ACL 元数据），
并按目录结构推断 workspace/department，写入 notes 表与 connector_syncs 审计表。
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import require_role, resolve_access_scope, current_actor
from api.dependencies import get_container
from application.access_control import record_access_audit

logger = logging.getLogger("mindgraph.api.connectors")
router = APIRouter(prefix="/connectors", tags=["connectors"])


class SyncDirectoryRequest(BaseModel):
    source_path: str = Field(..., description="本地 Markdown 目录的绝对路径")
    workspace: str | None = Field(None, description="默认 workspace（frontmatter 优先）")
    department: str | None = Field(None, description="默认 department（frontmatter 优先）")
    acl_json: str | None = Field(None, description="默认 ACL JSON（如 {\"allow\":[\"workspace:corp\"]}）")
    acl_public: bool = Field(False, description="是否将该目录下未声明 ACL 的笔记标记为公开")
    trigger_index: bool = Field(False, description="同步后是否触发索引构建")


@router.post("/directories")
def sync_directory(
    body: SyncDirectoryRequest,
    _auth: dict = Depends(require_role("write")),
):
    """同步一个本地 Markdown 目录到 MindGraph（增量）。"""
    source = Path(body.source_path)
    if not source.is_absolute():
        raise HTTPException(status_code=400, detail="source_path must be an absolute path")
    if not source.exists() or not source.is_dir():
        raise HTTPException(status_code=404, detail=f"Directory not found: {source}")
    container = get_container()
    try:
        result = container.directory_connector.sync(
            source,
            workspace=body.workspace,
            department=body.department,
            acl_json=body.acl_json,
            acl_public=body.acl_public,
            trigger_index=body.trigger_index,
        )
        record_access_audit(
            container.database,
            actor=_auth.get("name", "anonymous"),
            action="sync_directory",
            resource=f"connectors/directories:{result.get('connector_id')}",
            decision="allow",
            metadata={"source_path": body.source_path, "file_count": result.get("file_count")},
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("sync_directory_failed", extra={"source": body.source_path})
        raise HTTPException(status_code=500, detail=f"sync failed: {exc}") from exc


@router.get("/directories/status")
def list_sync_status(
    _auth: dict = Depends(require_role("read")),
    limit: int = Query(50, ge=1, le=200),
):
    """查看目录同步历史。"""
    rows = get_container().directory_connector.status()
    return {"items": rows[:limit], "total": len(rows)}


@router.get("/directories/{connector_id}")
def get_sync_status(connector_id: str, _auth: dict = Depends(require_role("read"))):
    """查看单次同步详情。"""
    rows = get_container().directory_connector.status(connector_id)
    if not rows:
        raise HTTPException(status_code=404, detail="connector sync not found")
    return rows[0]
