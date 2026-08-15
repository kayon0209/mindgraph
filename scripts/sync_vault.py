"""MindGraph 增量同步 CLI（M1-D3）。

整合 Vault 扫描与索引构建，支持单次运行与持续监听两种模式。

用法：
    # 单次扫描 + 增量构建（编辑后跑一次即可）
    python scripts/sync_vault.py --vault "D:/你的/Vault路径"

    # 持续监听：检测变更自动增量重建（Ctrl+C 退出）
    python scripts/sync_vault.py --vault "D:/你的/Vault路径" --watch

    # 仅扫描不构建
    python scripts/sync_vault.py --vault "D:/你的/Vault路径" --scan-only

    # 额外忽略目录（叠加默认列表：.venv/node_modules/__pycache__/.git/.obsidian 等）
    python scripts/sync_vault.py --vault "D:/你的/Vault路径" --ignore "tmp,drafts"

    # 完全不忽略（仅索引所有 .md，谨慎使用）
    python scripts/sync_vault.py --vault "D:/你的/Vault路径" --no-default-ignore
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(ROOT))

from config import ROOT as PROJECT_ROOT  # noqa: E402

from application.mindgraph_index_service import MindGraphIndexService  # noqa: E402
from application.mindgraph_sync_watcher import MindGraphSyncWatcher  # noqa: E402
from application.vault_sync_service import VaultSyncService, DEFAULT_IGNORE_DIRS  # noqa: E402
from infrastructure.database import ProductDatabase  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="MindGraph 增量同步（扫描 + 索引构建）")
    ap.add_argument("--vault", required=True, help="Obsidian Vault 根目录")
    ap.add_argument("--watch", action="store_true", help="持续监听模式（Ctrl+C 退出）")
    ap.add_argument("--scan-only", action="store_true", help="仅扫描标记 pending，不构建索引")
    ap.add_argument(
        "--reset", action="store_true",
        help="先清空 notes / note_relations 再从头同步（清除离线演示等污染数据；"
             "只删库内记录，不改 Vault 文件正文，首次真实扫描会重新注入 mindgraph_id）",
    )
    ap.add_argument("--db", default=str(PROJECT_ROOT / "data" / "product" / "product.sqlite3"))
    ap.add_argument("--index-root", default=str(PROJECT_ROOT / "data" / "mindgraph_indexes"))
    ap.add_argument("--poll", type=float, default=3.0, help="监听模式轮询间隔（秒）")
    ap.add_argument(
        "--ignore", default="",
        help="额外忽略的目录名（逗号分隔），叠加在默认忽略列表之上，如 'tmp,drafts'",
    )
    ap.add_argument(
        "--no-default-ignore", action="store_true",
        help="禁用默认忽略列表（.venv/node_modules/__pycache__ 等），仅索引所有 .md",
    )
    args = ap.parse_args()

    if args.no_default_ignore:
        ignore_dirs: set[str] | None = set()
    else:
        ignore_dirs = set(DEFAULT_IGNORE_DIRS)
    if args.ignore.strip():
        ignore_dirs.update(d.strip() for d in args.ignore.split(",") if d.strip())

    db = ProductDatabase(Path(args.db))
    db.initialize()

    if args.reset:
        db.execute("DELETE FROM note_relations")
        db.execute("DELETE FROM notes")
        print("[reset] 已清空 notes / note_relations，将重新扫描 Vault 并注入 mindgraph_id")

    scan = VaultSyncService(db, Path(args.vault), ignore_dirs=ignore_dirs)
    index = MindGraphIndexService(db, Path(args.vault), Path(args.index_root))
    watcher = MindGraphSyncWatcher(scan, index, poll_interval=args.poll)

    if args.scan_only:
        result = scan.scan_vault()
        print(json.dumps({
            "scanned": len(result.scanned),
            "skipped": len(result.skipped),
            "pruned": result.pruned,
            "errors": result.errors,
        }, ensure_ascii=False, indent=2))
        return

    if args.watch:
        watcher.run_forever()
        return

    summary = watcher.run_once()
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
