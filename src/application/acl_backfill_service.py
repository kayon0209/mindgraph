from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path, PurePosixPath
import sqlite3
from typing import Any
import uuid

from infrastructure.database import ProductDatabase

_ACL_KEYS = {
    "allow",
    "deny",
    "allow_workspaces",
    "allow_departments",
    "deny_workspaces",
    "deny_departments",
    "workspaces",
    "departments",
    "public",
    "is_public",
}


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _frontmatter_acl(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return {"allow": list(value)}
    return _json_object(value)


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(value)


def _acl_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class AclBackfillService:
    def __init__(self, database: ProductDatabase, builtin_root: Path) -> None:
        self.database = database
        self.builtin_root = Path(builtin_root)

    def plan(self) -> dict:
        with self.database.connect() as connection:
            return self._plan(connection)

    def apply(self) -> dict:
        run_id = uuid.uuid4().hex
        started_at = _utc_iso()
        connection = self.database.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            plan = self._plan(connection)
            connection.execute(
                "INSERT INTO acl_backfill_runs "
                "(run_id, status, started_at, item_count, changed_count, unresolved_count) "
                "VALUES (?, 'running', ?, ?, ?, ?)",
                (
                    run_id,
                    started_at,
                    plan["note_count"],
                    plan["changed_count"],
                    plan["unresolved_count"],
                ),
            )
            rows = {
                row["note_id"]: dict(row)
                for row in connection.execute(
                    "SELECT note_id, workspace, department, acl_json, acl_public FROM notes"
                )
            }
            for item in plan["items"]:
                original = rows[item["note_id"]]
                planned_acl_json = _acl_json(item["acl"])
                connection.execute(
                    "INSERT INTO acl_backfill_items "
                    "(run_id, note_id, old_workspace, old_department, old_acl_json, "
                    "old_acl_public, planned_workspace, planned_department, planned_acl_json, "
                    "planned_acl_public, action, reason, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        item["note_id"],
                        original["workspace"],
                        original["department"],
                        original["acl_json"],
                        int(bool(original["acl_public"])),
                        item["workspace"],
                        item["department"],
                        planned_acl_json,
                        item["acl_public"],
                        item["action"],
                        item["reason"],
                        started_at,
                    ),
                )
                if item["action"] == "updated":
                    connection.execute(
                        "UPDATE notes SET workspace=?, department=?, acl_json=?, acl_public=? "
                        "WHERE note_id=?",
                        (
                            item["workspace"],
                            item["department"],
                            planned_acl_json,
                            item["acl_public"],
                            item["note_id"],
                        ),
                    )
            completed_at = _utc_iso()
            connection.execute(
                "UPDATE acl_backfill_runs SET status='completed', completed_at=? WHERE run_id=?",
                (completed_at, run_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return {
            "run_id": run_id,
            "status": "completed",
            "changed": plan["changed_count"],
            "unchanged": plan["unchanged_count"],
            "unresolved_count": plan["unresolved_count"],
        }

    def rollback(self, run_id: str) -> dict:
        connection = self.database.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT status FROM acl_backfill_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if run is None or run["status"] != "completed":
                raise ValueError(f"unknown or incomplete ACL backfill run: {run_id}")
            items = connection.execute(
                "SELECT note_id, old_workspace, old_department, old_acl_json, old_acl_public "
                "FROM acl_backfill_items WHERE run_id=? AND action='updated'",
                (run_id,),
            ).fetchall()
            restored = 0
            for item in items:
                cursor = connection.execute(
                    "UPDATE notes SET workspace=?, department=?, acl_json=?, acl_public=? "
                    "WHERE note_id=?",
                    (
                        item["old_workspace"],
                        item["old_department"],
                        item["old_acl_json"],
                        item["old_acl_public"],
                        item["note_id"],
                    ),
                )
                restored += cursor.rowcount
            rolled_back_at = _utc_iso()
            connection.execute(
                "UPDATE acl_backfill_runs SET status='rolled_back', rolled_back_at=? WHERE run_id=?",
                (rolled_back_at, run_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return {"run_id": run_id, "status": "rolled_back", "restored": restored}

    def _plan(self, connection: sqlite3.Connection) -> dict:
        notes = connection.execute(
            "SELECT note_id, vault_path, source_id, frontmatter_json, workspace, department, "
            "acl_json, acl_public FROM notes ORDER BY note_id"
        ).fetchall()
        items = [self._decision(dict(note), connection) for note in notes]
        changed_count = sum(item["action"] == "updated" for item in items)
        unresolved_count = sum(item["reason"] == "source_unavailable" for item in items)
        return {
            "note_count": len(items),
            "changed_count": changed_count,
            "unchanged_count": len(items) - changed_count,
            "unresolved_count": unresolved_count,
            "items": items,
        }

    def _decision(
        self,
        note: dict[str, Any],
        connection: sqlite3.Connection,
    ) -> dict[str, Any]:
        resolved = self._resolve_note(note, connection)
        if resolved is None:
            workspace = None
            department = None
            acl = {"allow": [], "backfill_reason": "source_unavailable"}
            acl_public = 0
            reason = "source_unavailable"
        else:
            path, root, connector = resolved
            controlled = self._controlled_defaults(path, root, connector)
            frontmatter = _json_object(note["frontmatter_json"])
            explicit_acl = "acl" in frontmatter
            explicit_public = any(
                key in frontmatter for key in ("acl_public", "public", "is_public")
            )
            if explicit_acl or explicit_public:
                workspace = self._text(
                    frontmatter.get("workspace") or frontmatter.get("tenant")
                ) or controlled["workspace"] or note["workspace"]
                department = self._text(
                    frontmatter.get("department") or frontmatter.get("dept")
                ) or controlled["department"] or note["department"]
                if explicit_acl:
                    acl = _frontmatter_acl(frontmatter.get("acl"))
                elif controlled["available"]:
                    acl = dict(controlled["acl"])
                else:
                    acl = {}
                if "acl_public" in frontmatter:
                    public = _as_bool(frontmatter["acl_public"])
                elif "public" in frontmatter:
                    public = _as_bool(frontmatter["public"])
                elif "is_public" in frontmatter:
                    public = _as_bool(frontmatter["is_public"])
                elif explicit_acl:
                    public = _as_bool(acl.get("public", acl.get("is_public", False)))
                else:
                    public = controlled["acl_public"]
                acl = self._normalize_acl(acl, workspace, department, public)
                acl_public = int(public)
                reason = "explicit_frontmatter"
            elif controlled["available"]:
                workspace = controlled["workspace"]
                department = controlled["department"]
                acl_public = int(controlled["acl_public"])
                acl = self._normalize_acl(
                    controlled["acl"], workspace, department, bool(acl_public)
                )
                reason = "controlled_default"
            else:
                workspace = None
                department = None
                acl = {"allow": [], "backfill_reason": "source_unavailable"}
                acl_public = 0
                reason = "source_unavailable"

        same = (
            note["workspace"] == workspace
            and note["department"] == department
            and _json_object(note["acl_json"]) == acl
            and int(bool(note["acl_public"])) == acl_public
        )
        return {
            "note_id": note["note_id"],
            "source_id": note["source_id"],
            "workspace": workspace,
            "department": department,
            "acl": acl,
            "acl_public": acl_public,
            "action": "unchanged" if same else "updated",
            "reason": reason,
        }

    def _resolve_note(
        self,
        note: dict[str, Any],
        connection: sqlite3.Connection,
    ) -> tuple[Path, Path, dict[str, Any] | None] | None:
        normalized = str(note["vault_path"]).replace("\\", "/")
        relative = PurePosixPath(normalized)
        if relative.is_absolute() or not relative.parts or any(
            part in {"", ".", ".."} for part in relative.parts
        ):
            return None

        connector: dict[str, Any] | None = None
        if note["source_id"] == "builtin":
            root_value = self.builtin_root
            path_parts = relative.parts
        else:
            row = connection.execute(
                "SELECT connector_id, source_path, workspace, department, metadata_json "
                "FROM connector_syncs WHERE connector_id=? AND status='completed' "
                "ORDER BY finished_at DESC LIMIT 1",
                (note["source_id"],),
            ).fetchone()
            if row is None or relative.parts[0] != note["source_id"]:
                return None
            connector = dict(row)
            root_value = Path(connector["source_path"])
            path_parts = relative.parts[1:]
            if not path_parts:
                return None

        try:
            root = Path(root_value).resolve(strict=True)
            candidate = root.joinpath(*path_parts).resolve(strict=True)
        except OSError:
            return None
        if not root.is_dir() or root not in candidate.parents or not candidate.is_file():
            return None
        return candidate, root, connector

    def _controlled_defaults(
        self,
        path: Path,
        root: Path,
        connector: dict[str, Any] | None,
    ) -> dict[str, Any]:
        workspace = self._text(connector.get("workspace")) if connector else None
        department = self._text(connector.get("department")) if connector else None
        acl: dict[str, Any] = {}
        public = False
        available = bool(workspace or department)

        if connector:
            metadata = _json_object(connector.get("metadata_json"))
            default_acl = metadata.get("acl", metadata.get("acl_json"))
            parsed_default = _frontmatter_acl(default_acl)
            if parsed_default:
                acl.update(parsed_default)
                available = True
            if "acl_public" in metadata:
                public = _as_bool(metadata["acl_public"])
                available = True

        relative = path.relative_to(root)
        directories = [root]
        current = root
        for part in relative.parts[:-1]:
            current /= part
            directories.append(current)

        for directory in directories:
            acl_path = directory / "acl.json"
            if not acl_path.exists():
                continue
            try:
                resolved_acl_path = acl_path.resolve(strict=True)
                if root not in resolved_acl_path.parents:
                    continue
                data = json.loads(resolved_acl_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue

            workspace_map = data.get("workspaces")
            if isinstance(workspace_map, dict):
                candidates = [workspace]
                if relative.parts:
                    candidates.append(relative.parts[0])
                selected = next((key for key in candidates if key in workspace_map), None)
                config = workspace_map.get(selected) if selected else None
                if isinstance(config, dict):
                    workspace = workspace or selected
                    department = self._text(config.get("department")) or department
                    nested_acl = _frontmatter_acl(config.get("acl"))
                    if nested_acl:
                        acl.update(nested_acl)
                    if "acl_public" in config:
                        public = _as_bool(config["acl_public"])
                    available = True

            workspace = self._text(data.get("workspace")) or workspace
            department = self._text(data.get("department")) or department
            nested_acl = _frontmatter_acl(data.get("acl"))
            direct_acl = {key: data[key] for key in _ACL_KEYS if key in data and key != "workspaces"}
            if nested_acl or direct_acl:
                acl.update(nested_acl)
                acl.update(direct_acl)
                available = True
            if "acl_public" in data:
                public = _as_bool(data["acl_public"])
                available = True
            elif "public" in acl or "is_public" in acl:
                public = _as_bool(acl.get("public", acl.get("is_public", False)))
            if workspace or department:
                available = True

        return {
            "available": available,
            "workspace": workspace,
            "department": department,
            "acl": acl,
            "acl_public": public,
        }

    @staticmethod
    def _text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _normalize_acl(
        acl: dict[str, Any],
        workspace: str | None,
        department: str | None,
        public: bool,
    ) -> dict[str, Any]:
        normalized = dict(acl)
        if workspace:
            normalized.setdefault("workspace", workspace)
        if department:
            normalized.setdefault("department", department)
        if public:
            normalized["public"] = True
        else:
            normalized.pop("public", None)
            normalized.pop("is_public", None)
        return normalized
