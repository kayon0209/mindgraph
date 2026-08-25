"""Explicit operator CLI for the schema-8 to schema-9 governance migration."""
from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HELP_ITEMS = ("--dry-run (default)", "--apply", "--rollback RUN_ID")


class _InvalidArgumentsError(ValueError):
    pass


class _RedactedArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _InvalidArgumentsError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _RedactedArgumentParser(
        description="Explicitly migrate MindGraph governance schema from version 8 to 9.",
        add_help=False,
    )
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--help", action="store_true")
    operation.add_argument(
        "--dry-run",
        action="store_true",
        help="validate schema 8 read-only and report aggregate migration metadata",
    )
    operation.add_argument(
        "--apply",
        action="store_true",
        help="atomically apply the schema-8 to schema-9 migration",
    )
    operation.add_argument(
        "--rollback",
        metavar="RUN_ID",
        help="roll back one exact completed and unused migration run",
    )
    return parser


def _print_error(code: str) -> None:
    print(json.dumps({"ok": False, "error": code}, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if args.rollback is not None and not args.rollback.strip():
            raise _InvalidArgumentsError("rollback run ID must not be empty")
    except _InvalidArgumentsError:
        _print_error("invalid_arguments")
        return 2

    if args.help:
        print(json.dumps({"ok": True, "help": list(HELP_ITEMS)}, sort_keys=True))
        return 0

    src_path = str(PROJECT_ROOT / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    from application.governance_schema_migration_service import (
        GovernanceMigrationError,
        GovernanceSchemaMigrationService,
    )
    from infrastructure.settings import get_settings

    try:
        service = GovernanceSchemaMigrationService(Path(get_settings().DATABASE_PATH))
        if args.rollback is not None:
            report = service.rollback(args.rollback)
        elif args.apply:
            report = service.apply()
        else:
            report = service.plan()
    except GovernanceMigrationError as exc:
        _print_error(exc.code)
        return exc.exit_code
    except Exception:
        _print_error("internal_error")
        return 1

    print(json.dumps({"ok": True, **report.to_dict()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
