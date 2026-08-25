"""Run the auditable ACL backfill without exposing note-level data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from application.acl_backfill_service import AclBackfillService  # noqa: E402
from infrastructure.database import ProductDatabase  # noqa: E402


def _service() -> AclBackfillService:
    database = ProductDatabase(PROJECT_ROOT / "data" / "product" / "product.sqlite3")
    database.initialize()
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the MindGraph ACL backfill safely.")
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--dry-run", action="store_true", help="preview aggregate counts only")
    operation.add_argument("--apply", action="store_true", help="apply planned ACL changes")
    operation.add_argument("--rollback", metavar="RUN_ID", help="rollback one completed run")
    args = parser.parse_args(argv)
    if args.rollback is not None and not args.rollback.strip():
        parser.error("--rollback requires a non-empty RUN_ID")

    service = _service()
    if args.apply:
        mode = "apply"
        result = service.apply()
    elif args.rollback is not None:
        mode = "rollback"
        result = service.rollback(args.rollback)
    else:
        mode = "dry_run"
        result = service.plan()
    print(json.dumps(_output(mode, result), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
