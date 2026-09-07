"""Fail-closed source registration and dry-run ownership audit."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path, PurePosixPath
from typing import Any
import uuid

from domain.source_ownership import (
    AuditStatus,
    OwnershipAuditResult,
    OwnershipFinding,
    RegisteredSource,
    SourceStatus,
    SourceOwnershipError,
)
from infrastructure.database import ProductDatabase

_DIRECTORY_CONNECTOR_TYPE = "markdown_directory"


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def _source_status_from_row(value: Any) -> SourceStatus:
    status = str(value)
    if status == "active":
        return "active"
    if status == "disabled":
        return "disabled"
    raise SourceOwnershipError("source_status_invalid")


def _source_from_row(row: Any) -> RegisteredSource:
    return RegisteredSource(
        source_id=str(row["source_id"]),
        connector_id=str(row["connector_id"]),
        connector_type=str(row["connector_type"]),
        root_locator=str(row["root_locator"]),
        status=_source_status_from_row(row["status"]),
    )


def _canonical_directory(source_path: Path) -> tuple[Path, str]:
    if not source_path.exists() or not source_path.is_dir():
        raise SourceOwnershipError("source_directory_invalid")
    resolved = source_path.resolve(strict=True)
    return resolved, str(resolved).casefold()


def _roots_overlap(left: str, right: str) -> bool:
    left_path = Path(left)
    right_path = Path(right)
    return left_path == right_path or left_path in right_path.parents or right_path in left_path.parents


def _valid_source_path(source_id: str, source_path: Any) -> bool:
    if not isinstance(source_path, str) or not source_path or "\\" in source_path:
        return False
    path = PurePosixPath(source_path)
    if len(path.parts) < 2 or path.parts[0] != source_id:
        return False
    return all(part not in {"", ".", ".."} for part in path.parts)


class SourceOwnershipService:
    def __init__(self, database: ProductDatabase) -> None:
        self.database = database

    def register_directory_source(self, connector_id: str, source_path: Path) -> RegisteredSource:
        connector_id = connector_id.strip()
        if not connector_id:
            raise SourceOwnershipError("connector_id_invalid")
        _, locator = _canonical_directory(Path(source_path))
        existing = self.source_for_connector(connector_id)
        if existing is not None:
            if existing.root_locator != locator:
                raise SourceOwnershipError("source_root_overlap")
            return existing

        for row in self.database.fetch_all("SELECT * FROM knowledge_sources"):
            if _roots_overlap(locator, str(row["root_locator"])):
                raise SourceOwnershipError("source_root_overlap")

        now = _utc_iso()
        self.database.execute(
            "INSERT INTO knowledge_sources "
            "(source_id, connector_id, connector_type, root_locator, status, created_at, updated_at, last_audited_at) "
            "VALUES (?, ?, ?, ?, 'active', ?, ?, NULL)",
            (connector_id, connector_id, _DIRECTORY_CONNECTOR_TYPE, locator, now, now),
        )
        return RegisteredSource(connector_id, connector_id, _DIRECTORY_CONNECTOR_TYPE, locator, "active")

    def source_for_connector(self, connector_id: str) -> RegisteredSource | None:
        row = self.database.fetch_one("SELECT * FROM knowledge_sources WHERE connector_id=?", (connector_id,))
        return _source_from_row(row) if row is not None else None

    def dry_run_audit(self, source_id: str | None = None) -> OwnershipAuditResult:
        source_rows = self.database.fetch_all("SELECT * FROM knowledge_sources")
        sources = {str(row["source_id"]): _source_from_row(row) for row in source_rows}
        if source_id is not None and source_id not in sources:
            raise SourceOwnershipError("unknown_source_id")
        sql = "SELECT note_id, source_id, source_path, acl_json FROM notes"
        params: tuple[Any, ...] = ()
        if source_id is not None:
            sql += " WHERE source_id=?"
            params = (source_id,)
        findings: list[OwnershipFinding] = []
        for row in self.database.fetch_all(sql, params):
            note_id = str(row["note_id"])
            note_source_id = row["source_id"]
            if not note_source_id:
                findings.append(OwnershipFinding("missing_source_id", note_id, None, row["source_path"]))
                continue
            source = sources.get(str(note_source_id))
            if source is None:
                findings.append(OwnershipFinding("unknown_source_id", note_id, str(note_source_id), row["source_path"]))
                continue
            if not _valid_source_path(source.source_id, row["source_path"]):
                findings.append(OwnershipFinding("source_path_outside_root", note_id, source.source_id, row["source_path"]))
                continue
            try:
                acl = json.loads(str(row["acl_json"]))
                if not isinstance(acl, dict):
                    raise ValueError("ACL must be an object")
            except (TypeError, ValueError, json.JSONDecodeError):
                findings.append(OwnershipFinding("invalid_acl_json", note_id, source.source_id, row["source_path"]))

        audit_run_id = uuid.uuid4().hex
        now = _utc_iso()
        status: AuditStatus = "clean" if not findings else "needs_review"
        summary_json = json.dumps({"finding_count": len(findings)}, sort_keys=True)
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO source_ownership_audit_runs "
                "(audit_run_id, source_id, mode, status, summary_json, started_at, finished_at) "
                "VALUES (?, ?, 'dry_run', ?, ?, ?, ?)",
                (audit_run_id, source_id, status, summary_json, now, now),
            )
            for finding in findings:
                connection.execute(
                    "INSERT INTO source_ownership_findings "
                    "(finding_id, audit_run_id, reason_code, note_id, source_id, source_path, metadata_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, '{}', ?)",
                    (uuid.uuid4().hex, audit_run_id, finding.reason_code, finding.note_id, finding.source_id, finding.source_path, now),
                )
            if source_id is not None:
                connection.execute("UPDATE knowledge_sources SET last_audited_at=? WHERE source_id=?", (now, source_id))
        return OwnershipAuditResult(audit_run_id, status, source_id, len(findings))

    def require_sync_authorized(self, connector_id: str, source_path: Path) -> RegisteredSource:
        source = self.source_for_connector(connector_id)
        if source is None:
            raise SourceOwnershipError("connector_source_mismatch")
        _, locator = _canonical_directory(Path(source_path))
        if source.root_locator != locator:
            raise SourceOwnershipError("connector_source_mismatch")
        if source.status != "active":
            raise SourceOwnershipError("source_disabled")
        row = self.database.fetch_one(
            "SELECT status, finished_at FROM source_ownership_audit_runs "
            "WHERE source_id=? ORDER BY finished_at DESC LIMIT 1",
            (source.source_id,),
        )
        if row is None or row["status"] != "clean":
            raise SourceOwnershipError("audit_required")
        source_row = self.database.fetch_one("SELECT updated_at FROM knowledge_sources WHERE source_id=?", (source.source_id,))
        if source_row is None or str(row["finished_at"]) < str(source_row["updated_at"]):
            raise SourceOwnershipError("audit_required")
        return source
