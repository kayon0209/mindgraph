"""Regression tests for the test-suite database isolation boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from infrastructure.database import ProductDatabase


def test_product_database_construction_is_blocked_for_tests() -> None:
    """The test harness must fail before a test can open the business database."""
    product_database = Path(__file__).resolve().parents[1] / "data" / "product" / "product.sqlite3"

    with pytest.raises(AssertionError, match="business database"):
        ProductDatabase(product_database)


def test_initialize_closes_its_setup_connection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Schema setup must not leave a second SQLite handle open on Windows."""

    class TrackingConnection:
        closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

        def executescript(self, _sql: str) -> None:
            return None

        def execute(self, _sql: str, _params: object = ()):
            return self

        def fetchone(self):
            return None

        def __iter__(self):
            return iter(())

        def close(self) -> None:
            self.closed = True

    connection = TrackingConnection()
    database = ProductDatabase(tmp_path / "isolated.sqlite3")
    monkeypatch.setattr(database, "connect", lambda: connection)

    database.initialize()

    assert connection.closed is True


def test_close_releases_connection_when_wal_checkpoint_fails(tmp_path: Path) -> None:
    """A failed checkpoint must not retain a Windows file handle."""

    class CheckpointFailingConnection:
        closed = False

        def execute(self, _sql: str) -> None:
            raise RuntimeError("checkpoint unavailable")

        def close(self) -> None:
            self.closed = True

    connection = CheckpointFailingConnection()
    database = ProductDatabase(tmp_path / "isolated.sqlite3")
    database._local.connection = connection

    database.close()

    assert connection.closed is True
