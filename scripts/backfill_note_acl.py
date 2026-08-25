"""Run the auditable ACL backfill without exposing note-level data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from application.acl_backfill_service import AclBackfillService  # noqa: E402
from infrastructure.database import SCHEMA_VERSION, ProductDatabase  # noqa: E402
from infrastructure.settings import get_settings  # noqa: E402


def _database_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def _validate_database(path: Path) -> None:
    with sqlite3.connect(_database_uri(path), uri=True) as connection:
        row = connection.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
        if row is None or int(row[0]) != SCHEMA_VERSION:
            raise RuntimeError("database schema is not ready for ACL backfill")


class _ReadOnlyProductDatabase:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(_database_uri(self.path), uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection


def _service(*, read_only: bool) -> AclBackfillService:
    database_path = Path(get_settings().DATABASE_PATH)
    _validate_database(database_path)
    database = (
        _ReadOnlyProductDatabase(database_path)
        if read_only
        else ProductDatabase(database_path)
    )
    return AclBackfillService(database, PROJECT_ROOT / "knowledge")


def _output(mode: str, result: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "note_count",
        "changed_count",
        "unchanged_count",
        "unresolved_count",
        "changed",
        "unchanged",
        "restored",
        "run_id",
    }
    return {"mode": mode, **{key: result[key] for key in allowed if key in result}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the MindGraph ACL backfill safely.")
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--dry-run", action="store_true", help="preview aggregate counts only")
    operation.add_argument("--apply", action="store_true", help="apply planned ACL changes")
    operation.add_argument("--rollback", metavar="RUN_ID", help="rollback one completed run")
    args = parser.parse_args(argv)
    if args.rollback is not None and not args.rollback.strip():
        parser.error("--rollback requires a non-empty RUN_ID")

    if args.apply:
        mode = "apply"
    elif args.rollback is not None:
        mode = "rollback"
    else:
        mode = "dry_run"

    try:
        service = _service(read_only=mode == "dry_run")
        if mode == "apply":
            result = service.apply()
        elif mode == "rollback":
            result = service.rollback(args.rollback)
        else:
            result = service.plan()
    except Exception:
        print(
            json.dumps({"error": "operation_failed", "mode": mode}, sort_keys=True),
            file=sys.stderr,
        )
        return 1

    print(json.dumps(_output(mode, result), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
