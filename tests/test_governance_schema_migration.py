from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any

import pytest

from application.governance_schema_migration_service import (
    GovernanceMigrationError,
    GovernanceSchemaMigrationService,
)
from infrastructure.database import ProductDatabase

GOVERNANCE_TABLES = {
    "governance_cases",
    "governance_case_notes",
    "governance_note_state",
    "governance_events",
}
MIGRATION_TABLE = "schema_migration_runs"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = PROJECT_ROOT / "scripts" / "migrate_governance_schema.py"


def _schema_version(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
    assert row is not None
    return int(row[0])


def _table_names(path: Path) -> set[str]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    return {str(row[0]) for row in rows}


def _schema_objects(path: Path) -> dict[str, tuple[str, str]]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT name, type, sql FROM sqlite_master "
            "WHERE name LIKE 'governance_%' OR name LIKE 'idx_governance_%' OR name = ?",
            (MIGRATION_TABLE,),
        ).fetchall()
    return {str(row[0]): (str(row[1]), str(row[2])) for row in rows}


def _file_snapshot(path: Path) -> dict[str, bytes]:
    """Snapshot persistent SQLite bytes; the non-persistent SHM file holds reader locks."""
    return {
        suffix: candidate.read_bytes()
        for suffix in ("", "-wal")
        if (candidate := Path(f"{path}{suffix}")).exists()
    }


def _strip_governance_schema(path: Path) -> None:
    """Turn a freshly initialized test database into a schema-8 fixture."""
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            DROP TABLE IF EXISTS governance_case_notes;
            DROP TABLE IF EXISTS governance_note_state;
            DROP TABLE IF EXISTS governance_events;
            DROP TABLE IF EXISTS governance_cases;
            DROP TABLE IF EXISTS schema_migration_runs;
            UPDATE schema_meta SET version = 8;
            """
        )


@pytest.fixture
def schema8_db(tmp_path: Path) -> Path:
    path = tmp_path / "schema8.sqlite3"
    ProductDatabase(path).initialize()
    _strip_governance_schema(path)
    assert _schema_version(path) == 8
    assert not (GOVERNANCE_TABLES | {MIGRATION_TABLE}) & _table_names(path)
    return path


@pytest.fixture
def schema9_db(tmp_path: Path) -> Path:
    path = tmp_path / "schema9.sqlite3"
    ProductDatabase(path).initialize()
    assert _schema_version(path) == 9
    return path


def _insert_event(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO governance_events (
                event_id, actor, action, previous_state_json, new_state_json,
                reason_code, evidence_ids_json, source, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "event-1",
                "reviewer",
                "proposed",
                "{}",
                '{"status":"proposed"}',
                "candidate_detected",
                "[]",
                "human_review",
                "2026-08-25T00:00:00Z",
            ),
        )


def _run_cli(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_PATH"] = str(path)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _json_stdout(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert result.stderr == ""
    assert result.stdout.count("\n") == 1
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    return payload


def test_existing_schema_8_initialize_does_not_create_governance_tables(
    schema8_db: Path,
) -> None:
    ProductDatabase(schema8_db).initialize()

    assert _schema_version(schema8_db) == 8
    assert not GOVERNANCE_TABLES & _table_names(schema8_db)
    assert MIGRATION_TABLE not in _table_names(schema8_db)


def test_new_database_initializes_at_schema_9(tmp_path: Path) -> None:
    path = tmp_path / "new.sqlite3"

    ProductDatabase(path).initialize()

    assert _schema_version(path) == 9
    assert GOVERNANCE_TABLES | {MIGRATION_TABLE} <= _table_names(path)


def test_schema_9_contains_only_aggregate_safe_event_columns(schema9_db: Path) -> None:
    with sqlite3.connect(schema9_db) as connection:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(governance_events)")
        }

    assert columns == {
        "event_id",
        "case_id",
        "note_id",
        "policy_key",
        "actor",
        "action",
        "previous_state_json",
        "new_state_json",
        "reason_code",
        "evidence_ids_json",
        "source",
        "request_id",
        "created_at",
    }
    assert not columns & {
        "content",
        "body",
        "title",
        "path",
        "vault_path",
        "acl",
        "acl_json",
        "credential",
        "token",
        "secret",
    }


def test_schema_9_has_required_indexes_and_append_only_triggers(
    schema9_db: Path,
) -> None:
    objects = _schema_objects(schema9_db)

    assert {
        "idx_governance_cases_status_policy",
        "idx_governance_case_notes_note",
        "idx_governance_events_case",
        "idx_governance_events_note",
        "idx_governance_events_policy",
        "idx_governance_events_created_at",
        "idx_governance_note_state_disposition",
        "governance_events_no_update",
        "governance_events_no_delete",
    } <= objects.keys()


def test_dry_run_is_byte_for_byte_read_only(schema8_db: Path) -> None:
    before = _file_snapshot(schema8_db)

    report = GovernanceSchemaMigrationService(schema8_db).plan()

    assert report.mode == "dry_run"
    assert report.status == "planned"
    assert report.current_version == 8
    assert report.target_version == 9
    assert report.object_count == 14
    assert report.run_id is None
    assert _file_snapshot(schema8_db) == before
    assert _schema_version(schema8_db) == 8


def test_dry_run_refuses_wrong_version_without_mutation(schema8_db: Path) -> None:
    with sqlite3.connect(schema8_db) as connection:
        connection.execute("UPDATE schema_meta SET version = 7")
    before = _file_snapshot(schema8_db)

    with pytest.raises(GovernanceMigrationError) as captured:
        GovernanceSchemaMigrationService(schema8_db).plan()

    assert captured.value.code == "invalid_schema_version"
    assert _file_snapshot(schema8_db) == before


def test_apply_and_exact_run_rollback(schema8_db: Path) -> None:
    service = GovernanceSchemaMigrationService(schema8_db)

    applied = service.apply()

    assert applied.mode == "apply"
    assert applied.status == "completed"
    assert applied.current_version == 9
    assert applied.target_version == 9
    assert applied.object_count == 14
    assert applied.run_id
    assert _schema_version(schema8_db) == 9
    assert GOVERNANCE_TABLES | {MIGRATION_TABLE} <= _table_names(schema8_db)

    rolled_back = service.rollback(applied.run_id)

    assert rolled_back.mode == "rollback"
    assert rolled_back.status == "rolled_back"
    assert rolled_back.current_version == 8
    assert rolled_back.target_version == 8
    assert rolled_back.object_count == 14
    assert rolled_back.run_id == applied.run_id
    assert _schema_version(schema8_db) == 8
    assert not _table_names(schema8_db) & GOVERNANCE_TABLES
    assert MIGRATION_TABLE in _table_names(schema8_db)
    with sqlite3.connect(schema8_db) as connection:
        ledger = connection.execute(
            "SELECT status, rolled_back_at FROM schema_migration_runs WHERE run_id = ?",
            (applied.run_id,),
        ).fetchone()
    assert ledger is not None
    assert ledger[0] == "rolled_back"
    assert ledger[1]


def test_rollback_requires_the_exact_completed_run(schema8_db: Path) -> None:
    service = GovernanceSchemaMigrationService(schema8_db)
    applied = service.apply()

    with pytest.raises(GovernanceMigrationError) as captured:
        service.rollback("not-the-completed-run")

    assert captured.value.code == "migration_run_not_found"
    assert _schema_version(schema8_db) == 9
    assert _table_names(schema8_db) >= GOVERNANCE_TABLES
    with sqlite3.connect(schema8_db) as connection:
        status = connection.execute(
            "SELECT status FROM schema_migration_runs WHERE run_id = ?",
            (applied.run_id,),
        ).fetchone()
    assert status == ("completed",)


@pytest.mark.parametrize(
    "table, sql, values",
    [
        (
            "governance_cases",
            """INSERT INTO governance_cases (
                case_id, case_type, status, reason_code, rule_key, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                "case-1",
                "duplicate",
                "proposed",
                "same_content",
                "rule-1",
                "2026-08-25T00:00:00Z",
                "2026-08-25T00:00:00Z",
            ),
        ),
        (
            "governance_case_notes",
            "INSERT INTO governance_case_notes (case_id, note_id, participant_role) VALUES (?, ?, ?)",
            ("orphan-case", "private-note-id", "candidate"),
        ),
        (
            "governance_note_state",
            """INSERT INTO governance_note_state (
                note_id, evaluated_on, lifecycle_state, disposition,
                reason_codes_json, decision_fingerprint, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                "private-note-id",
                "2026-08-25",
                "current",
                "eligible",
                "[]",
                "fingerprint",
                "2026-08-25T00:00:00Z",
            ),
        ),
        (
            "governance_events",
            """INSERT INTO governance_events (
                event_id, actor, action, previous_state_json, new_state_json,
                reason_code, evidence_ids_json, source, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "event-1",
                "reviewer",
                "proposed",
                "{}",
                "{}",
                "candidate_detected",
                "[]",
                "human_review",
                "2026-08-25T00:00:00Z",
            ),
        ),
    ],
)
def test_rollback_refuses_when_any_governance_table_is_in_use(
    schema8_db: Path,
    table: str,
    sql: str,
    values: tuple[str, ...],
) -> None:
    service = GovernanceSchemaMigrationService(schema8_db)
    applied = service.apply()
    with sqlite3.connect(schema8_db) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(sql, values)

    with pytest.raises(
        GovernanceMigrationError,
        match="governance tables are in use",
    ) as captured:
        service.rollback(applied.run_id or "")

    assert captured.value.code == "governance_tables_in_use"
    assert _schema_version(schema8_db) == 9
    with sqlite3.connect(schema8_db) as connection:
        assert connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 1


def test_governance_events_are_append_only(schema9_db: Path) -> None:
    _insert_event(schema9_db)

    with sqlite3.connect(schema9_db) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE governance_events SET action = 'confirmed' WHERE event_id = 'event-1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM governance_events WHERE event_id = 'event-1'")


@pytest.mark.parametrize(
    "table, columns, values",
    [
        (
            "governance_cases",
            "case_id, case_type, status, reason_code, rule_key, created_at, updated_at",
            ("case-1", "duplicate", "invalid", "reason", "rule", "now", "now"),
        ),
        (
            "governance_case_notes",
            "case_id, note_id, participant_role",
            ("case-1", "note-1", "invalid"),
        ),
        (
            "governance_note_state",
            "note_id, evaluated_on, lifecycle_state, disposition, reason_codes_json, decision_fingerprint, updated_at",
            ("note-1", "2026-08-25", "invalid", "eligible", "[]", "fp", "now"),
        ),
        (
            "governance_note_state",
            "note_id, evaluated_on, lifecycle_state, disposition, reason_codes_json, decision_fingerprint, updated_at",
            ("note-1", "2026-08-25", "current", "invalid", "[]", "fp", "now"),
        ),
        (
            "governance_events",
            "event_id, actor, action, previous_state_json, new_state_json, reason_code, evidence_ids_json, source, created_at",
            ("event-1", "actor", "invalid", "{}", "{}", "reason", "[]", "human_review", "now"),
        ),
        (
            "governance_events",
            "event_id, actor, action, previous_state_json, new_state_json, reason_code, evidence_ids_json, source, created_at",
            ("event-1", "actor", "proposed", "{}", "{}", "reason", "[]", "invalid", "now"),
        ),
    ],
)
def test_governance_enums_are_database_constrained(
    schema9_db: Path,
    table: str,
    columns: str,
    values: tuple[str, ...],
) -> None:
    placeholders = ", ".join("?" for _ in values)

    with sqlite3.connect(schema9_db) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f'INSERT INTO "{table}" ({columns}) VALUES ({placeholders})',
                values,
            )


def test_cli_defaults_to_one_json_dry_run_without_mutation(schema8_db: Path) -> None:
    before = _file_snapshot(schema8_db)

    result = _run_cli(schema8_db)

    assert result.returncode == 0
    assert _json_stdout(result) == {
        "current_version": 8,
        "mode": "dry_run",
        "object_count": 14,
        "ok": True,
        "run_id": None,
        "status": "planned",
        "target_version": 9,
    }
    assert _file_snapshot(schema8_db) == before


def test_cli_apply_and_exact_run_rollback_use_runtime_database(schema8_db: Path) -> None:
    applied_result = _run_cli(schema8_db, "--apply")

    assert applied_result.returncode == 0
    applied = _json_stdout(applied_result)
    assert applied["ok"] is True
    assert applied["mode"] == "apply"
    assert applied["run_id"]
    assert _schema_version(schema8_db) == 9

    rollback_result = _run_cli(schema8_db, "--rollback", str(applied["run_id"]))

    assert rollback_result.returncode == 0
    rolled_back = _json_stdout(rollback_result)
    assert rolled_back["ok"] is True
    assert rolled_back["status"] == "rolled_back"
    assert rolled_back["run_id"] == applied["run_id"]
    assert _schema_version(schema8_db) == 8
    assert MIGRATION_TABLE in _table_names(schema8_db)


def test_cli_rollback_error_is_one_redacted_json_object(schema8_db: Path) -> None:
    applied_result = _run_cli(schema8_db, "--apply")
    applied = _json_stdout(applied_result)
    private_path = "private-finance-policy.sqlite3"
    private_note_id = "employee-private-note-id"
    with sqlite3.connect(schema8_db) as connection:
        connection.execute(
            """
            INSERT INTO governance_events (
                event_id, note_id, actor, action, previous_state_json,
                new_state_json, reason_code, evidence_ids_json, source, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "event-private",
                private_note_id,
                "reviewer",
                "proposed",
                "{}",
                "{}",
                "candidate_detected",
                "[]",
                "human_review",
                "2026-08-25T00:00:00Z",
            ),
        )

    result = _run_cli(schema8_db, "--rollback", str(applied["run_id"]))

    assert result.returncode != 0
    output = result.stdout
    assert _json_stdout(result) == {
        "error": "governance_tables_in_use",
        "ok": False,
    }
    for secret in (
        str(schema8_db),
        private_path,
        private_note_id,
        "Traceback",
    ):
        assert secret not in output


@pytest.mark.parametrize("args", [("--apply", "--dry-run"), ("--rollback", "")])
def test_cli_argument_errors_are_redacted_json(
    schema8_db: Path,
    args: tuple[str, ...],
) -> None:
    result = _run_cli(schema8_db, *args)

    assert result.returncode != 0
    assert _json_stdout(result) == {"error": "invalid_arguments", "ok": False}
    assert "usage:" not in result.stdout
    assert "Traceback" not in result.stdout


def test_cli_missing_database_error_does_not_expose_path(tmp_path: Path) -> None:
    private_path = tmp_path / "finance-secret-policy.sqlite3"

    result = _run_cli(private_path)

    assert result.returncode != 0
    assert _json_stdout(result) == {"error": "database_unavailable", "ok": False}
    assert str(private_path) not in result.stdout
    assert "Traceback" not in result.stdout
