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


def test_malicious_archive_members_rejected(tmp_path, monkeypatch):
    """安全审查 F2 回归锁定：restore 拒绝符号链接成员、路径穿越成员与
    Windows 盘符相对名——恶意备份包不能把 data 目录替换成指向任意位置的
    符号链接，也不能写出白名单子目录。"""
    import io
    import sys
    import tarfile as _tarfile

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import backup as backup_module

    # 演练环境：BACKUP_DIR/DATA_DIR/PROJECT_ROOT 指向临时目录
    work = tmp_path / "mal"
    work.mkdir()
    (work / "project" / "data").mkdir(parents=True)
    (work / "backups").mkdir()
    monkeypatch.setattr(backup_module, "BACKUP_DIR", work / "backups")
    monkeypatch.setattr(backup_module, "DATA_DIR", work / "project" / "data")
    monkeypatch.setattr(backup_module, "PROJECT_ROOT", work / "project")

    evil = work / "backups" / "evil.tar.gz"
    with _tarfile.open(evil, "w:gz") as tar:
        # 1) 符号链接成员（攻击核心：data → /etc 或 C:\Windows）
        link = _tarfile.TarInfo("data")
        link.type = _tarfile.SYMTYPE
        link.linkname = "/etc"
        tar.addfile(link)
        # 2) 穿越成员
        traversal = _tarfile.TarInfo("data/../../escape.txt")
        traversal.type = _tarfile.REGTYPE
        traversal.size = 0
        tar.addfile(traversal, io.BytesIO(b""))
        # 3) Windows 盘符相对名
        drive = _tarfile.TarInfo("C:evil")
        drive.type = _tarfile.REGTYPE
        drive.size = 0
        tar.addfile(drive, io.BytesIO(b""))

    # restore 不应抛异常（拒绝是 warning + skip），且不产生逃逸文件
    result = backup_module.restore(evil)
    # 无论返回值如何：不得存在逃逸文件与符号链接目录
    assert not (work / "escape.txt").exists()
    assert not (tmp_path / "escape.txt").exists()
    restore_temp = work / "backups" / "_restore_temp"
    data_dest = work / "project" / "data"
    if data_dest.exists():
        assert not data_dest.is_symlink(), "data 目录被替换为符号链接——攻击成功！"
