"""本地目录 / Markdown 目录增量同步连接器（Phase 5-2）。

目标：把企业资料源（本地文件夹 / Markdown 目录）增量同步进 MindGraph，
并同步 workspace / department / ACL 元数据，供检索与台账做权限裁剪。

设计原则（对齐 Phase 5）：
- 优先选一个企业资料源（本地目录），不做 SharePoint / Confluence；
- 增量同步：基于 content_hash 检测新增 / 修改 / 删除，复用 VaultSyncService；
- workspace / department 推断：frontmatter > 目录结构（第一级=workspace，第二级=department）> acl.json；
- ACL 继承：根目录或每级目录的 ``acl.json`` 声明该子树的 ACL，子文件继承；
- 同步结果写入 ``connector_syncs`` 表，供运维审计；
- 同步后可选触发索引构建（由调用方决定，避免频繁重建）。
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from application.vault_sync_service import VaultSyncService
from infrastructure.database import ProductDatabase

logger = logging.getLogger("mindgraph.connectors.directory")

CONNECTOR_TYPE = "markdown_directory"
SUPPORTED_SUFFIXES = {".md", ".markdown"}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_dir_acl(dir_path: Path) -> dict[str, Any] | None:
    acl_file = dir_path / "acl.json"
    if not acl_file.exists():
        return None
    try:
        data = json.loads(acl_file.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        return None
    return None


class DirectoryConnectorService:
    """本地 Markdown 目录增量同步连接器。

    用法：
        svc = DirectoryConnectorService(database, vault_root, index_service)
        result = svc.sync(source_path, workspace="corp", department="finance")
    """

    def __init__(
        self,
        database: ProductDatabase,
        vault_root: Path,
        index_service: Any | None = None,
        vault_sync: VaultSyncService | None = None,
    ) -> None:
        self.database = database
        self.vault_root = Path(vault_root)
        self.index_service = index_service
        self._vault_sync = vault_sync

    def _sync_service(self, source_path: Path) -> VaultSyncService:
        """为指定源目录构建 VaultSyncService（write_ids=True 以注入稳定 ID）。"""
        if self._vault_sync is not None:
            return self._vault_sync
        return VaultSyncService(self.database, source_path, write_ids=True)

    def _load_root_acl_map(self, source_path: Path) -> dict[str, dict[str, Any]]:
        """加载根目录 acl.json，返回顶层 ACL 配置。"""
        root_acl = _load_dir_acl(source_path)
        if not root_acl:
            return {}
        workspaces = root_acl.get("workspaces") or {}
        if isinstance(workspaces, dict):
            return workspaces
        return {}

    def _resolve_acl_for_path(self, source_path: Path, rel: Path, root_acl_map: dict[str, dict[str, Any]]) -> tuple[str | None, str | None, dict[str, Any]]:
        """推断单个文件的 workspace / department / acl。

        优先级：
        1. frontmatter（由 VaultSyncService._access_metadata 解析）；
        2. 目录结构：第一级目录 = workspace，第二级 = department；
        3. 根 acl.json 的 workspaces 配置。
        """
        parts = [p for p in rel.parts if p]
        workspace = parts[0] if len(parts) >= 2 else None
        department = parts[1] if len(parts) >= 2 else (parts[0] if parts else None)
        acl: dict[str, Any] = {}

        if workspace and workspace in root_acl_map:
            ws_config = root_acl_map[workspace]
            if isinstance(ws_config, dict):
                acl.update(ws_config.get("acl") or {})
                if "department" in ws_config:
                    department = department or str(ws_config["department"])

        dir_acl = _load_dir_acl(rel.parent if rel.parent != Path(".") else source_path)
        if dir_acl:
            acl.update(dir_acl)

        return workspace, department, acl

    def sync(
        self,
        source_path: Path | str,
        *,
        workspace: str | None = None,
        department: str | None = None,
        acl_json: str | None = None,
        acl_public: bool = False,
        trigger_index: bool = False,
        connector_id: str | None = None,
    ) -> dict[str, Any]:
        """同步一个本地 Markdown 目录。

        - 扫描 source_path 下所有 .md / .markdown 文件；
        - 复用 VaultSyncService 做增量 upsert + 剪枝；
        - workspace/department: frontmatter 优先，否则用参数或目录结构推断；
        - 同步后若 trigger_index=True，触发索引构建；
        - 结果写入 connector_syncs 表。
        """
        source = Path(source_path)
        if not source.exists() or not source.is_dir():
            raise ValueError(f"Source path is not a directory: {source}")

        connector_id = connector_id or f"dir-{uuid.uuid4().hex[:12]}"
        started_at = _utc_iso()
        root_acl_map = self._load_root_acl_map(source)

        self.database.execute(
            "INSERT OR REPLACE INTO connector_syncs "
            "(connector_id, connector_type, source_path, workspace, department, status, "
            "file_count, added, updated, pruned, error, metadata_json, started_at, finished_at) "
            "VALUES (?, ?, ?, ?, ?, 'running', 0, 0, 0, 0, NULL, '{}', ?, NULL)",
            (connector_id, CONNECTOR_TYPE, str(source), workspace, department, started_at),
        )

        try:
            # 同步前记录已存在的 note_id，用于区分新增 vs 更新
            pre_existing_ids = {
                row["note_id"]
                for row in self.database.fetch_all("SELECT note_id FROM notes")
            }
            sync = self._sync_service(source)
            result = sync.scan_vault()
            file_count = len(result.scanned)
            pruned = result.pruned

            added = 0
            updated = 0
            for note in result.scanned:
                if note.note_id in pre_existing_ids:
                    updated += 1
                else:
                    added += 1

            self._apply_connector_acl(source, root_acl_map, workspace, department, acl_json, acl_public)

            metadata = {
                "scanned": file_count,
                "skipped": len(result.skipped),
                "errors": result.errors[:20],
                "source": str(source),
            }
            self.database.execute(
                "UPDATE connector_syncs SET status='completed', file_count=?, added=?, updated=?, pruned=?, "
                "error=NULL, metadata_json=?, finished_at=? WHERE connector_id=?",
                (file_count, added, updated, pruned, json.dumps(metadata, ensure_ascii=False), _utc_iso(), connector_id),
            )

            index_version = None
            if trigger_index and self.index_service is not None:
                manifest = self.index_service.build(force=pruned > 0)
                index_version = manifest.get("index_version") if isinstance(manifest, dict) else None

            return {
                "connector_id": connector_id,
                "status": "completed",
                "file_count": file_count,
                "added": added,
                "updated": updated,
                "pruned": pruned,
                "errors": result.errors,
                "index_version": index_version,
            }
        except Exception as exc:
            self.database.execute(
                "UPDATE connector_syncs SET status='failed', error=?, finished_at=? WHERE connector_id=?",
                (str(exc)[:500], _utc_iso(), connector_id),
            )
            logger.exception("directory_connector_sync_failed", extra={"connector_id": connector_id, "source": str(source)})
            raise

    def _apply_connector_acl(
        self,
        source: Path,
        root_acl_map: dict[str, dict[str, Any]],
        default_workspace: str | None,
        default_department: str | None,
        acl_json: str | None,
        acl_public: bool,
    ) -> None:
        """对同步后的 notes 按目录结构回填 workspace/department/ACL（仅对缺失值的行）。"""
        rows = self.database.fetch_all(
            "SELECT note_id, vault_path, workspace, department FROM notes"
        )
        if not rows:
            return
        default_acl = json.loads(acl_json) if acl_json else {}
        # 当 default_workspace 给定时，把 workspace 不等于 default 的行也纳入回填范围
        target_rows = []
        if default_workspace:
            for row in rows:
                target_rows.append(row)
        else:
            for row in rows:
                if row["workspace"] is None or row["department"] is None or not row.get("acl_json") or row.get("acl_json") == "{}":
                    target_rows.append(row)
        if not target_rows:
            return
        for row in target_rows:
            rel = Path(row["vault_path"])
            parts = [p for p in rel.parts if p]
            # 目录结构推断：第一级目录=department（如 finance/、hr/）；workspace 用 default_workspace
            dir_department = parts[0] if parts else None
            # 当 default_workspace 提供时优先使用它（覆盖 VaultSync 从 parent.name 推断的值）
            ws = default_workspace or (parts[0] if len(parts) >= 2 else None)
            dept = default_department or dir_department
            ws_acl_map = root_acl_map.get(ws) if isinstance(root_acl_map, dict) else None
            dir_acl: dict[str, Any] = {}
            if isinstance(ws_acl_map, dict):
                dir_acl.update(ws_acl_map.get("acl") or {})
            dir_acl_file = _load_dir_acl(source / rel.parent) if rel.parent != Path(".") else _load_dir_acl(source)
            if dir_acl_file:
                dir_acl.update(dir_acl_file)
            merged_acl = dict(default_acl)
            merged_acl.update(dir_acl)
            if ws and "workspace" not in merged_acl:
                merged_acl["workspace"] = ws
            if dept and "department" not in merged_acl:
                merged_acl["department"] = dept
            allow = list(merged_acl.get("allow") or [])
            if ws and f"workspace:{ws}" not in allow:
                allow.append(f"workspace:{ws}")
            if dept and f"department:{dept}" not in allow:
                allow.append(f"department:{dept}")
            merged_acl["allow"] = allow
            self.database.execute(
                "UPDATE notes SET workspace=CASE WHEN ? IS NOT NULL THEN ? ELSE workspace END, "
                "department=CASE WHEN ? IS NOT NULL THEN ? ELSE department END, "
                "acl_json=CASE WHEN acl_json='{}' THEN ? ELSE acl_json END, "
                "acl_public=CASE WHEN acl_public=0 THEN ? ELSE acl_public END "
                "WHERE note_id=?",
                (ws, ws, dept, dept, json.dumps(merged_acl, ensure_ascii=False), 1 if acl_public else 0, row["note_id"]),
            )

    def status(self, connector_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM connector_syncs"
        params: tuple[Any, ...] = ()
        if connector_id:
            sql += " WHERE connector_id=?"
            params = (connector_id,)
        sql += " ORDER BY started_at DESC LIMIT 50"
        rows = self.database.fetch_all(sql, params)
        return [dict(row) for row in rows]
