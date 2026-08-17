import os
from pathlib import Path

from scripts import backup as backup_module


def test_backup_naming_and_legacy_discovery(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(backup_module, "BACKUP_DIR", tmp_path)
    legacy = tmp_path / "expense-rag-backup-20260101T000000Z.tar.gz"
    current = tmp_path / "mindgraph-backup-20260801T000000Z.tar.gz"
    legacy.write_bytes(b"legacy")
    current.write_bytes(b"current")

    assert backup_module.get_backup_filename().startswith("mindgraph-backup-")
    assert {item["filename"] for item in backup_module.list_backups()} == {
        legacy.name,
        current.name,
    }


def test_cleanup_handles_current_and_legacy_names(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(backup_module, "BACKUP_DIR", tmp_path)
    old_timestamp = 1_700_000_000
    for name in (
        "expense-rag-backup-20230101T000000Z.tar.gz",
        "mindgraph-backup-20230101T000000Z.tar.gz",
    ):
        path = tmp_path / name
        path.write_bytes(b"old")
        os.utime(path, (old_timestamp, old_timestamp))

    assert backup_module.cleanup_old_backups(keep=1) == 2
    assert backup_module.list_backups() == []
