"""Runtime compliance tests for product SQLite data paths."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import infrastructure.database as database_module
from infrastructure.database import ProductDatabase
from infrastructure.sqlite_runtime import require_safe_sqlite_runtime


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("3.44.6", (3, 44, 6)),
        ("3.50.7", (3, 50, 7)),
        ("3.51.3", (3, 51, 3)),
        ("3.51.4", (3, 51, 4)),
        ("3.53.1", (3, 53, 1)),
    ],
)
def test_runtime_gate_accepts_only_fixed_release_lines(
    version: str,
    expected: tuple[int, int, int],
) -> None:
    assert require_safe_sqlite_runtime(version) == expected


@pytest.mark.parametrize(
    "version",
    [
        "3.44.5",
        "3.44.7",
        "3.46.1",
        "3.50.6",
        "3.50.8",
        "3.51.2",
        "",
        "3.51",
        "3.51.3.0",
        "3.51.3+vendor",
        "3.51.3 ",
        "v3.51.3",
        "3.a.3",
        "３.５１.３",
    ],
)
def test_runtime_gate_rejects_unsafe_or_noncanonical_versions(version: str) -> None:
    with pytest.raises(RuntimeError, match="not approved"):
        require_safe_sqlite_runtime(version)


def test_product_database_rejects_unsafe_runtime_before_opening_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An unsafe stdlib SQLite build must never reach the product DB opener."""
    connection_attempted = False

    def forbidden_connect(*args: object, **kwargs: object) -> None:
        nonlocal connection_attempted
        connection_attempted = True
        raise AssertionError("sqlite3.connect must not run for an unsafe runtime")

    monkeypatch.setattr(database_module.sqlite3, "sqlite_version", "3.46.1")
    monkeypatch.setattr(database_module.sqlite3, "connect", forbidden_connect)

    with pytest.raises(RuntimeError, match=r"SQLite 3\.46\.1.*WAL-reset"):
        ProductDatabase(tmp_path / "product.sqlite3").connect()

    assert connection_attempted is False


def test_api_lifespan_rejects_unsafe_runtime_before_container_initialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.main as api_main
    import infrastructure.sqlite_runtime as sqlite_runtime

    container_requested = False

    def forbidden_container() -> None:
        nonlocal container_requested
        container_requested = True
        raise AssertionError("container must not initialize for an unsafe runtime")

    async def start_lifespan() -> None:
        async with api_main.lifespan(api_main.app):
            pass

    monkeypatch.setattr(sqlite_runtime.sqlite3, "sqlite_version", "3.46.1")
    monkeypatch.setattr(api_main, "get_container", forbidden_container)

    with pytest.raises(RuntimeError, match=r"SQLite 3\.46\.1.*WAL-reset"):
        asyncio.run(start_lifespan())

    assert container_requested is False


def test_backup_rejects_unsafe_runtime_before_creating_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import infrastructure.sqlite_runtime as sqlite_runtime
    import scripts.backup as backup_script

    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(sqlite_runtime.sqlite3, "sqlite_version", "3.46.1")
    monkeypatch.setattr(backup_script, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(backup_script, "DATA_DIR", tmp_path / "data")

    with pytest.raises(RuntimeError, match=r"SQLite 3\.46\.1.*WAL-reset"):
        backup_script.backup()

    assert backup_dir.exists() is False


def test_backup_restore_drill_rejects_unsafe_runtime_before_live_db_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import infrastructure.sqlite_runtime as sqlite_runtime
    import scripts.run_backup_restore_drill as drill_script

    monkeypatch.setattr(sqlite_runtime.sqlite3, "sqlite_version", "3.46.1")

    with pytest.raises(RuntimeError, match=r"SQLite 3\.46\.1.*WAL-reset"):
        drill_script.run_drill()


def test_relation_seed_rejects_unsafe_runtime_before_opening_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import infrastructure.sqlite_runtime as sqlite_runtime
    import scripts.seed_relations as seed_script

    connection_attempted = False

    def forbidden_connect(*args: object, **kwargs: object) -> None:
        nonlocal connection_attempted
        connection_attempted = True
        raise AssertionError("sqlite3.connect must not run for an unsafe runtime")

    monkeypatch.setattr(sqlite_runtime.sqlite3, "sqlite_version", "3.46.1")
    monkeypatch.setattr(seed_script.sqlite3, "connect", forbidden_connect)

    with pytest.raises(RuntimeError, match=r"SQLite 3\.46\.1.*WAL-reset"):
        seed_script.load_notes(str(tmp_path / "product.sqlite3"))

    assert connection_attempted is False
