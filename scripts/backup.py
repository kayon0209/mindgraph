#!/usr/bin/env python3
"""数据备份与恢复工具。

用法:
    备份:  python scripts/backup.py backup
    恢复:  python scripts/backup.py restore <backup_file>
    列表:  python scripts/backup.py list
    清理:  python scripts/backup.py cleanup --keep 7
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sqlite3
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", str(DATA_DIR / "backups")))
RETENTION_DAYS = int(os.getenv("BACKUP_RETENTION_DAYS", "30"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backup")


def ensure_backup_dir() -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def get_db_path() -> Path:
    """获取 SQLite 数据库路径。"""
    return DATA_DIR / "product" / "product.sqlite3"


def get_retrieval_indexes_dir() -> Path:
    return DATA_DIR / "retrieval_indexes"


def get_knowledge_dir() -> Path:
    return PROJECT_ROOT / "knowledge"


def get_backup_filename() -> str:
    """生成备份文件名。"""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"expense-rag-backup-{ts}.tar.gz"


def backup() -> Path:
    """执行完整备份。

    备份内容:
        - SQLite 数据库
        - 检索索引文件
        - 知识库文档
        - 环境配置备份

    Returns:
        Path: 备份文件路径
    """
    ensure_backup_dir()
    backup_file = BACKUP_DIR / get_backup_filename()

    # ── 1. 备份前先做 SQLite VACUUM ──
    db_path = get_db_path()
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("VACUUM")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
            logger.info("SQLite VACUUM + WAL checkpoint completed")
        except Exception as exc:
            logger.warning("VACUUM failed (non-fatal): %s", exc)

    # ── 2. 创建备份元数据 ──
    total_files = 0
    total_size = 0
    for base in [DATA_DIR, get_knowledge_dir()]:
        if base.exists():
            for f in base.rglob("*"):
                if f.is_file():
                    total_files += 1
                    total_size += f.stat().st_size

    metadata = {
        "version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "total_files": total_files,
        "total_size_bytes": total_size,
        "contents": ["data/", "knowledge/"],
        "database_path": str(db_path.relative_to(PROJECT_ROOT)) if db_path.exists() else None,
    }

    # ── 3. 打包备份 ──
    with tarfile.open(backup_file, "w:gz") as tar:
        # 添加元数据
        meta_path = BACKUP_DIR / "backup-metadata.json"
        meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        tar.add(meta_path, arcname="backup-metadata.json")
        meta_path.unlink()

        # 添加数据目录
        if DATA_DIR.exists():
            tar.add(DATA_DIR, arcname="data")

        # 添加知识库文档
        kb_dir = get_knowledge_dir()
        if kb_dir.exists() and any(kb_dir.iterdir()):
            tar.add(kb_dir, arcname="knowledge")

    file_size = backup_file.stat().st_size
    logger.info(
        "Backup completed: %s (%.2f MB, %d files)",
        backup_file.name,
        file_size / (1024 * 1024),
        total_files,
    )

    return backup_file


def _is_safe_path(base: Path, target: Path) -> bool:
    """Check that *target* resolves inside *base* (prevents directory traversal)."""
    try:
        resolved = (base / target).resolve(strict=False)
        return resolved.is_relative_to(base.resolve())
    except (ValueError, OSError):
        return False


def restore(backup_file: Path) -> bool:
    """从备份文件恢复数据。

    Args:
        backup_file: 备份 tar.gz 文件路径

    Returns:
        bool: 恢复成功返回 True
    """
    if not backup_file.exists():
        logger.error("Backup file not found: %s", backup_file)
        return False

    # ── 1. 先备份当前数据（防止恢复失败数据丢失） ──
    current_backup = backup()
    logger.info("Pre-restore backup created: %s", current_backup.name)

    # ── 2. 解压备份 ──
    with tarfile.open(backup_file, "r:gz") as tar:
        # 读取元数据
        try:
            meta_info = tar.getmember("backup-metadata.json")
            tar.extract(meta_info, path=str(BACKUP_DIR))
            metadata = json.loads((BACKUP_DIR / "backup-metadata.json").read_text(encoding="utf-8"))
            logger.info("Backup metadata: created=%s, files=%d", metadata["created_at"], metadata["total_files"])
            (BACKUP_DIR / "backup-metadata.json").unlink()
        except Exception as exc:
            logger.warning("Failed to read backup metadata: %s", exc)

        # 解压到临时目录再覆盖
        temp_dir = BACKUP_DIR / "_restore_temp"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        temp_dir.mkdir()

        # 路径穿越安全检查
        for member in tar.getmembers():
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                logger.warning("Skipping suspicious archive entry: %s", member.name)
                continue
            tar.extract(member, path=str(temp_dir))

        # ── 3. 恢复数据 (only safe subdirs) ──
        allowed_subdirs = {"data", "knowledge"}
        for item in temp_dir.iterdir():
            if item.name not in allowed_subdirs:
                logger.warning("Skipping unexpected archive entry: %s", item.name)
                continue
            dest = PROJECT_ROOT / item.name
            if dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
            shutil.move(str(item), str(dest))

        shutil.rmtree(temp_dir)

    logger.info("Restore completed from %s", backup_file.name)
    return True


def list_backups() -> list[dict]:
    """列出所有备份文件。"""
    ensure_backup_dir()
    backups = []
    for f in sorted(BACKUP_DIR.glob("expense-rag-backup-*.tar.gz"), reverse=True):
        stat = f.stat()
        backups.append({
            "filename": f.name,
            "size_mb": round(stat.st_size / (1024 * 1024), 2),
            "created": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })
    return backups


def cleanup_old_backups(keep: int = RETENTION_DAYS) -> int:
    """清理过期备份文件。

    Args:
        keep: 保留最近 N 天的备份

    Returns:
        int: 清理的文件数量
    """
    ensure_backup_dir()
    cutoff = datetime.now().timestamp() - keep * 86400
    deleted = 0
    for f in BACKUP_DIR.glob("expense-rag-backup-*.tar.gz"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            deleted += 1
            logger.info("Deleted old backup: %s", f.name)
    return deleted


def main():
    parser = argparse.ArgumentParser(description="Expense RAG QA 数据备份与恢复")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("backup", help="执行完整备份")

    restore_parser = sub.add_parser("restore", help="从备份恢复")
    restore_parser.add_argument("file", help="备份文件路径")

    sub.add_parser("list", help="列出所有备份")

    cleanup_parser = sub.add_parser("cleanup", help="清理过期备份")
    cleanup_parser.add_argument("--keep", type=int, default=RETENTION_DAYS, help=f"保留最近 N 天 (默认: {RETENTION_DAYS})")

    args = parser.parse_args()

    if args.command == "backup":
        result = backup()
        print(f"Backup saved: {result}")
        print(f"Size: {result.stat().st_size / (1024 * 1024):.2f} MB")

    elif args.command == "restore":
        ok = restore(Path(args.file))
        if ok:
            print("Restore completed. Please restart the service.")
        else:
            print("Restore failed.", file=sys.stderr)
            sys.exit(1)

    elif args.command == "list":
        items = list_backups()
        if not items:
            print("No backups found.")
        for item in items:
            print(f"  {item['filename']}  ({item['size_mb']} MB)  {item['created']}")

    elif args.command == "cleanup":
        count = cleanup_old_backups(args.keep)
        print(f"Deleted {count} old backup(s).")


if __name__ == "__main__":
    main()
