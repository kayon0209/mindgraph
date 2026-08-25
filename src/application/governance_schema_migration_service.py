"""Explicit, auditable schema-8 to schema-9 governance migration."""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from infrastructure.database import (
    GOVERNANCE_SCHEMA_OBJECT_COUNT,
    GOVERNANCE_SCHEMA_OBJECTS,
    GOVERNANCE_TABLES,
    SCHEMA_VERSION,
    SOURCE_OWNERSHIP_SCHEMA_VERSION,
    _create_governance_schema,
)

MIGRATION_NAME = "ug008_governance_schema_9"


@dataclass(frozen=True, slots=True)
class GovernanceMigrationReport:
    mode: str
    status: str
    current_version: int
    target_version: int
    object_count: int
    run_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "status": self.status,
            "current_version": self.current_version,
            "target_version": self.target_version,
            "object_count": self.object_count,
            "run_id": self.run_id,
        }


class GovernanceMigrationError(RuntimeError):
    """Safe operator-facing migration failure with no database details."""

    def __init__(self, code: str, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


class GovernanceSchemaMigrationService:
    """Plan, apply, or roll back the one explicit governance schema migration."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)

    def plan(self) -> GovernanceMigrationReport:
        try:
            with closing(self._read_only_connection()) as connection:
                self._require_version(connection, SOURCE_OWNERSHIP_SCHEMA_VERSION)
                self._require_no_governance_objects(connection, allow_ledger=True)
        except GovernanceMigrationError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise GovernanceMigrationError(
                "database_unavailable",
                "database is unavailable",
                exit_code=2,
            ) from exc
        return GovernanceMigrationReport(
            mode="dry_run",
            status="planned",
            current_version=SOURCE_OWNERSHIP_SCHEMA_VERSION,
            target_version=SCHEMA_VERSION,
            object_count=GOVERNANCE_SCHEMA_OBJECT_COUNT,
            run_id=None,
        )

    def apply(self) -> GovernanceMigrationReport:
        connection = self._writable_connection()
        run_id = str(uuid4())
        started_at = _utc_now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_version(connection, SOURCE_OWNERSHIP_SCHEMA_VERSION)
            self._require_no_governance_objects(connection, allow_ledger=True)
            _create_governance_schema(connection)
            connection.execute(
                """
                INSERT INTO schema_migration_runs (
                    run_id, migration_name, previous_version, target_version,
                    status, object_count, started_at, completed_at
                ) VALUES (?, ?, ?, ?, 'completed', ?, ?, ?)
                """,
                (
                    run_id,
                    MIGRATION_NAME,
                    SOURCE_OWNERSHIP_SCHEMA_VERSION,
                    SCHEMA_VERSION,
                    GOVERNANCE_SCHEMA_OBJECT_COUNT,
                    started_at,
                    _utc_now(),
                ),
            )
            connection.execute("UPDATE schema_meta SET version = ?", (SCHEMA_VERSION,))
            connection.commit()
        except GovernanceMigrationError:
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as exc:
            if connection.in_transaction:
                connection.rollback()
            raise GovernanceMigrationError(
                "migration_failed",
                "migration failed",
            ) from exc
        finally:
            connection.close()
        return GovernanceMigrationReport(
            mode="apply",
            status="completed",
            current_version=SCHEMA_VERSION,
            target_version=SCHEMA_VERSION,
            object_count=GOVERNANCE_SCHEMA_OBJECT_COUNT,
            run_id=run_id,
        )

    def rollback(self, run_id: str) -> GovernanceMigrationReport:
        if not isinstance(run_id, str) or not run_id.strip():
            raise GovernanceMigrationError(
                "migration_run_not_found",
                "completed migration run not found",
                exit_code=4,
            )
        connection = self._writable_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_version(connection, SCHEMA_VERSION)
            run = connection.execute(
                """
                SELECT object_count
                FROM schema_migration_runs
                WHERE run_id = ?
                  AND migration_name = ?
                  AND previous_version = ?
                  AND target_version = ?
                  AND status = 'completed'
                """,
                (
                    run_id,
                    MIGRATION_NAME,
                    SOURCE_OWNERSHIP_SCHEMA_VERSION,
                    SCHEMA_VERSION,
                ),
            ).fetchone()
            if run is None:
                raise GovernanceMigrationError(
                    "migration_run_not_found",
                    "completed migration run not found",
                    exit_code=4,
                )
            if self._governance_row_count(connection):
                raise GovernanceMigrationError(
                    "governance_tables_in_use",
                    "governance tables are in use",
                    exit_code=4,
                )
            _drop_governance_business_schema(connection)
            connection.execute(
                "UPDATE schema_meta SET version = ?",
                (SOURCE_OWNERSHIP_SCHEMA_VERSION,),
            )
            connection.execute(
                """
                UPDATE schema_migration_runs
                SET status = 'rolled_back', rolled_back_at = ?
                WHERE run_id = ?
                """,
                (_utc_now(), run_id),
            )
            connection.commit()
        except GovernanceMigrationError:
            if connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as exc:
            if connection.in_transaction:
                connection.rollback()
            raise GovernanceMigrationError(
                "migration_failed",
                "migration failed",
            ) from exc
        finally:
            connection.close()
        return GovernanceMigrationReport(
            mode="rollback",
            status="rolled_back",
            current_version=SOURCE_OWNERSHIP_SCHEMA_VERSION,
            target_version=SOURCE_OWNERSHIP_SCHEMA_VERSION,
            object_count=int(run[0]),
            run_id=run_id,
        )

    def _read_only_connection(self) -> sqlite3.Connection:
        uri = f"{self.database_path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    def _writable_connection(self) -> sqlite3.Connection:
        if not self.database_path.is_file():
            raise GovernanceMigrationError(
                "database_unavailable",
                "database is unavailable",
                exit_code=2,
            )
        try:
            connection = sqlite3.connect(
                str(self.database_path),
                timeout=10.0,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
        except sqlite3.Error as exc:
            raise GovernanceMigrationError(
                "database_unavailable",
                "database is unavailable",
                exit_code=2,
            ) from exc
        return connection

    @staticmethod
    def _require_version(connection: sqlite3.Connection, expected: int) -> None:
        try:
            row = connection.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
        except sqlite3.Error as exc:
            raise GovernanceMigrationError(
                "invalid_schema_version",
                "database schema version is not eligible",
                exit_code=3,
            ) from exc
        if row is None or int(row[0]) != expected:
            raise GovernanceMigrationError(
                "invalid_schema_version",
                "database schema version is not eligible",
                exit_code=3,
            )

    @staticmethod
    def _require_no_governance_objects(
        connection: sqlite3.Connection,
        *,
        allow_ledger: bool,
    ) -> None:
        names = tuple(
            name
            for name in sorted(GOVERNANCE_SCHEMA_OBJECTS)
            if not (allow_ledger and name == "schema_migration_runs")
        )
        placeholders = ",".join("?" for _ in names)
        found = connection.execute(
            f"SELECT 1 FROM sqlite_master WHERE name IN ({placeholders}) LIMIT 1",
            names,
        ).fetchone()
        if found is not None:
            raise GovernanceMigrationError(
                "migration_conflict",
                "governance schema objects already exist",
                exit_code=3,
            )

    @staticmethod
    def _governance_row_count(connection: sqlite3.Connection) -> int:
        return sum(
            int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in GOVERNANCE_TABLES
        )


def _drop_governance_business_schema(connection: sqlite3.Connection) -> None:
    connection.execute("DROP TRIGGER governance_events_no_update")
    connection.execute("DROP TRIGGER governance_events_no_delete")
    connection.execute("DROP INDEX idx_governance_cases_status_policy")
    connection.execute("DROP INDEX idx_governance_case_notes_note")
    connection.execute("DROP INDEX idx_governance_events_case")
    connection.execute("DROP INDEX idx_governance_events_note")
    connection.execute("DROP INDEX idx_governance_events_policy")
    connection.execute("DROP INDEX idx_governance_events_created_at")
    connection.execute("DROP INDEX idx_governance_note_state_disposition")
    connection.execute("DROP TABLE governance_case_notes")
    connection.execute("DROP TABLE governance_note_state")
    connection.execute("DROP TABLE governance_events")
    connection.execute("DROP TABLE governance_cases")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
