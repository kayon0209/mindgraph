"""离线填充真实 DB（无网络 / 无 BGE 模型时使用）。

复制真实 Obsidian Vault -> 用 VaultSyncService 扫描写入真实 product.sqlite3 的 notes 表
（注入 mindgraph_id 到【副本】，不改动原 Vault）-> 插入若干 confirmed / proposed 关系，
使 MindGraph 只读 API（知识库 / 评测 / 链接建议队列）返回真实数据。

不构建 FAISS 索引（只读端点不需要索引）。
最终上线请用真实 BGE 重跑：  python scripts/sync_vault.py --vault D:/ObsidianVault
清除本脚本写入的 demo 关系：  DELETE FROM note_relations WHERE model_version='offline-demo';
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from infrastructure.database import ProductDatabase  # noqa: E402
from application.vault_sync_service import VaultSyncService  # noqa: E402

VAULT = Path(r"D:/ObsidianVault")
DB_PATH = ROOT / "data" / "product" / "product.sqlite3"
DEMO_TAG = "offline-demo"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="mg_offline_"))
    copy = tmp / "vault"
    print(f"复制 Vault {VAULT} -> {copy}")
    shutil.copytree(
        VAULT, copy,
        ignore=shutil.ignore_patterns(".venv", "node_modules", "__pycache__", ".git", ".obsidian", ".trash"),
    )

    db = ProductDatabase(DB_PATH)
    db.initialize()
    svc = VaultSyncService(db, copy, write_ids=True)
    res = svc.scan_vault()
    print(f"扫描完成：scanned={len(res.scanned)} skipped={len(res.skipped)} errors={len(res.errors)}")
    for e in res.errors[:10]:
        print("  ERR", e)

    ids = [r["note_id"] for r in db.fetch_all("SELECT note_id FROM notes ORDER BY updated_at DESC")]
    print(f"notes 表总数：{len(ids)}")
    if len(ids) < 8:
        print("笔记不足，无法构造关系示例")
        return

    n = now_iso()

    def insert_rel(s: str, t: str, rtype: str, status: str, conf: float) -> None:
        rid = "rel-" + uuid.uuid4().hex[:12]
        resolved = n if status == "confirmed" else None
        resolved_by = DEMO_TAG if status == "confirmed" else None
        db.execute(
            "INSERT OR IGNORE INTO note_relations "
            "(relation_id, source_note_id, target_note_id, relation_type, direction, status, "
            "evidence_chunk_id, confidence, model_version, prompt_version, proposed_at, resolved_at, resolved_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, s, t, rtype, "outgoing", status, None, conf, DEMO_TAG, "v1", n, resolved, resolved_by),
        )

    # 两条 confirmed（进入 Graph RAG 检索路径）
    insert_rel(ids[0], ids[1], "相关主题", "confirmed", 0.91)
    insert_rel(ids[2], ids[3], "补充参考", "confirmed", 0.86)
    # 三条 proposed（链接建议队列；其中一条与 confirmed 同 pair -> 触发冲突检测）
    insert_rel(ids[4], ids[5], "潜在关联", "proposed", 0.71)
    insert_rel(ids[6], ids[7], "待确认", "proposed", 0.63)
    insert_rel(ids[0], ids[1], "重复确认", "proposed", 0.58)

    c = db.fetch_one("SELECT COUNT(*) AS c FROM note_relations WHERE model_version=?", (DEMO_TAG,))["c"]
    print(f"插入 demo 关系：{c} 条（confirmed 2 + proposed 3，含 1 条冲突样例）")
    print("完成。MindGraph 只读 API 现在返回真实数据。最终上线请用真实 BGE 重跑 sync_vault.py。")


if __name__ == "__main__":
    main()
