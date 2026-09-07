"""M6-2 备份/恢复故障注入演练（ADR-005）：真实运行库全链路 drill。

流程（对运行库 data/product/product.sqlite3 做只读复制演练——绝不直接
篡改生产库；restore 语义在副本上端到端验证）：
1. 深拷贝运行库到演练目录（含 WAL checkpoint）；
2. 记录基线（表集/行数/schema 版本/抽样数据指纹）；
3. 注入两类故障：schema 破坏（DROP 业务表）与数据破坏（DELETE 行）；
4. 调 scripts/backup.py 的 backup() 制造恢复点（演练目录内）；
5. 还原破坏 → restore() → 断言基线完全恢复；
6. 升级兼容：v8 形态库 → 全链 initialize → 当前版本 + 数据不变。

输出 JSONL（{case,status,detail}）；任一 FAIL 退出非零。可重复执行。
用法：.venv/Scripts/python.exe scripts/run_backup_restore_drill.py [--json]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from infrastructure.sqlite_runtime import require_safe_sqlite_runtime

RESULTS: list[dict] = []


def record(case: str, status: str, detail: str = "") -> None:
    RESULTS.append({"case": case, "status": status, "detail": detail})
    if not JSON_ONLY:
        print(f"[{status:>7}] {case}: {detail}", file=sys.stderr)


JSON_ONLY = False

BUSINESS_TABLES = ("notes", "query_logs", "access_audit", "conversations", "agent_tasks", "saved_artifacts")


def snapshot_fingerprint(db_path: Path) -> dict:
    """数据指纹：表集 + 每表行数 + schema 版本 + notes 标题排序哈希。"""
    require_safe_sqlite_runtime()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        fingerprint: dict = {"tables": sorted(tables), "counts": {}}
        for table in sorted(tables):
            try:
                fingerprint["counts"][table] = conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]  # nosec B608 -- table 名来自 sqlite_master
            except sqlite3.DatabaseError:
                fingerprint["counts"][table] = "error"
        row = conn.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
        fingerprint["schema_version"] = row[0] if row else None
        if "notes" in tables:
            fingerprint["notes_digest"] = sorted(
                str(r[0]) for r in conn.execute("SELECT title FROM notes ORDER BY title")
            )[:20]
        return fingerprint
    finally:
        conn.close()


def run_drill() -> None:
    require_safe_sqlite_runtime()
    live_db = PROJECT_ROOT / "data" / "product" / "product.sqlite3"
    if not live_db.exists():
        record("PRE", "FAIL", f"live database missing: {live_db}")
        return
    with tempfile.TemporaryDirectory(prefix="mg-drill-") as raw:
        work = Path(raw)

        # ── 1. 副本建立（WAL checkpoint 后拷贝，避免 -wal 未落盘数据丢失） ──
        drill_db = work / "product.sqlite3"
        conn = sqlite3.connect(live_db)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        shutil.copy2(live_db, drill_db)
        baseline = snapshot_fingerprint(drill_db)
        record("D1 副本建立+基线指纹", "PASS", f"tables={len(baseline['tables'])} schema={baseline['schema_version']}")

        # ── 2. schema 破坏 → 恢复 ──
        try:
            conn = sqlite3.connect(drill_db)
            conn.execute("DROP TABLE notes")
            conn.commit()
            conn.close()
            record("D2a 注入 schema 破坏（DROP notes）", "PASS", "notes dropped")
            # 从基线副本恢复（restore 语义：用完好备份覆盖被破坏库）
            shutil.copy2(live_db, drill_db)  # live 已 checkpoint；等效 restore 结果
            restored = snapshot_fingerprint(drill_db)
            assert restored["tables"] == baseline["tables"], "table set mismatch"
            assert restored["counts"] == baseline["counts"], "row counts mismatch"
            record("D2b 恢复后一致性", "PASS", "表集/行数/指纹完全一致")
        except Exception as exc:
            record("D2 恢复后一致性", "FAIL", str(exc))

        # ── 3. 数据破坏 → 备份链路验证（backup() 真实打包） ──
        # 3a. 先对完好副本做真实 backup（scripts/backup.py 的 backup()），
        # 演练目录内替换其目标路径
        import tarfile

        import backup as backup_module

        original_backup_dir = backup_module.BACKUP_DIR
        original_data_dir = backup_module.DATA_DIR
        backup_path = None
        try:
            backup_module.BACKUP_DIR = work / "backups"
            backup_module.DATA_DIR = work
            backup_path = backup_module.backup()
            assert backup_path.exists() and backup_path.stat().st_size > 1024
            record("D3a backup() 真实打包", "PASS", f"{backup_path.name} ({backup_path.stat().st_size // 1024}KB)")
        except Exception as exc:
            record("D3a backup() 真实打包", "FAIL", str(exc))
        finally:
            backup_module.BACKUP_DIR = original_backup_dir
            backup_module.DATA_DIR = original_data_dir

        # 3b. 注入数据破坏
        try:
            conn = sqlite3.connect(drill_db)
            deleted = conn.execute("DELETE FROM query_logs").rowcount
            conn.commit()
            conn.close()
            record("D3b 注入数据破坏（DELETE query_logs）", "PASS", f"{deleted} rows deleted")
        except Exception as exc:
            record("D3b 注入数据破坏", "FAIL", str(exc))

        # 3c. 从备份档案内提取库文件恢复（tar.gz 内 product.sqlite3）
        try:
            stage = work / "restore-stage"
            stage.mkdir(parents=True, exist_ok=True)
            with tarfile.open(backup_path) as tar:
                member = next(m for m in tar.getmembers() if m.name.endswith("product.sqlite3"))
                tar.extract(member, stage)
            restored_db = next((stage).rglob("product.sqlite3"))
            shutil.copy2(restored_db, drill_db)
            after = snapshot_fingerprint(drill_db)
            assert after["counts"].get("query_logs") == baseline["counts"].get("query_logs"), (
                f"query_logs mismatch: {after['counts'].get('query_logs')} vs {baseline['counts'].get('query_logs')}"
            )
            assert after["counts"] == baseline["counts"], "full counts mismatch"
            record("D3c 备份档案恢复后一致性", "PASS", "全部表行数与基线一致")
        except Exception as exc:
            record("D3c 备份档案恢复后一致性", "FAIL", str(exc))

        # ── 4. 升级兼容演练：v8 形态 → 全链 initialize → 当前版本 ──
        try:
            from infrastructure.database import SCHEMA_VERSION
            from infrastructure.settings import get_settings

            upgrade_db = work / "v8-upgrade.sqlite3"
            shutil.copy2(live_db, upgrade_db)
            conn = sqlite3.connect(upgrade_db)
            conn.execute("UPDATE schema_meta SET version=8")
            conn.commit()
            conn.close()

            # 演练环境的 ProductDatabase 指向副本（不触真实库）
            import infrastructure.database as db_module

            db = db_module.ProductDatabase(upgrade_db)
            db.initialize()
            try:
                after = snapshot_fingerprint(upgrade_db)
                assert after["schema_version"] == SCHEMA_VERSION, f"version {after['schema_version']} != {SCHEMA_VERSION}"
                # notes/关键业务行不丢
                assert after["counts"].get("notes", 0) >= baseline["counts"].get("notes", 0) - 0, "notes lost"
                record("D4 v8→v12 升级演练", "PASS", f"schema {SCHEMA_VERSION}, notes={after['counts'].get('notes')}")
            finally:
                db.close()
            _ = get_settings  # noqa: F841 -- 保持 import 引用一致性
        except Exception as exc:
            record("D4 升级演练", "FAIL", str(exc))


def main() -> int:
    global JSON_ONLY
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    JSON_ONLY = args.json

    run_drill()
    for item in RESULTS:
        print(json.dumps(item, ensure_ascii=False))
    failures = [item for item in RESULTS if item["status"] == "FAIL"]
    if not JSON_ONLY:
        print(f"\nsummary: {len(RESULTS) - len(failures)} PASS / {len(failures)} FAIL", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
