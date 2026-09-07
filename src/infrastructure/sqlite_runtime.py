"""Fail-closed runtime guard for product SQLite data paths."""

from __future__ import annotations

import re
import sqlite3


_CANONICAL_VERSION = re.compile(r"([0-9]+)\.([0-9]+)\.([0-9]+)")
_BACKPORTED_SAFE_VERSIONS = {(3, 44, 6), (3, 50, 7)}
_MINIMUM_SAFE_VERSION = (3, 51, 3)


def require_safe_sqlite_runtime(version: str | None = None) -> tuple[int, int, int]:
    """Reject a stdlib SQLite build that is unsafe for product WAL databases."""
    runtime_version = sqlite3.sqlite_version if version is None else version
    match = _CANONICAL_VERSION.fullmatch(runtime_version)
    parsed = (
        (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if match
        else None
    )
    if parsed is None or not (parsed >= _MINIMUM_SAFE_VERSION or parsed in _BACKPORTED_SAFE_VERSIONS):
        raise RuntimeError(
            f"SQLite {runtime_version} is not approved for MindGraph product WAL databases; "
            "required >=3.51.3 or exact backport 3.44.6/3.50.7 for the WAL-reset fix"
        )
    return parsed
