"""CLI：扫描 Obsidian Vault，注入稳定 mindgraph_id 并写入 notes 表（M1-D2 入口）。

用法：
    python scripts/scan_vault.py --vault "D:/path/to/your/vault"
    python scripts/scan_vault.py --vault "..." --dry-run        # 只解析，不写回 ID / 不写库
    python scripts/scan_vault.py --vault "..." --db "data/product/product.sqlite3"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from infrastructure.database import ProductDatabase
from application.vault_sync_service import VaultSyncService


def _main() -> int:
    ap = argparse.ArgumentParser(description="MindGraph Vault 扫描器（M1-D2）")
    ap.add_argument("--vault", required=True, help="Obsidian Vault 根目录")
    ap.add_argument(
        "--db",
        default=str(ROOT / "data" / "product" / "product.sqlite3"),
        help="SQLite 数据库路径（默认复用现有库）",
    )
    ap.add_argument("--dry-run", action="store_true", help="只解析，不写回 ID、不写库")
    args = ap.parse_args()

    vault = Path(args.vault)
    if not vault.is_dir():
        print(f"错误：Vault 目录不存在：{vault}", file=sys.stderr)
        return 2

    db = ProductDatabase(Path(args.db))
    db.initialize()
    svc = VaultSyncService(db, vault, write_ids=not args.dry_run)
    result = svc.scan_vault()

    print("扫描完成：")
    print(f"  已入库笔记 : {len(result.scanned)}")
    print(f"  新注入 ID  : {sum(1 for n in result.scanned if n.id_injected)}")
    print(f"  重复ID消解 : {sum(1 for n in result.scanned if n.duplicate_resolved)}")
    print(f"  跳过非md  : {len(result.skipped)}")
    print(f"  剪枝缺失  : {result.pruned}")
    if args.dry_run:
        print("  [dry-run] 未写回 ID / 未写库")
    if result.errors:
        print(f"  错误({len(result.errors)})：")
        for e in result.errors:
            print(f"    - {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
