"""离线填充演示 DB（无网络 / 无 BGE 模型时使用）。

复制指定 Markdown Vault -> 用 VaultSyncService 扫描写入独立 demo SQLite 的 notes 表
（注入 mindgraph_id 到【副本】，不改动原 Vault）-> 插入若干 confirmed / proposed 关系，
使 MindGraph 只读 API（知识库 / 评测 / 链接建议队列）返回真实数据。

不构建 FAISS 索引（只读端点不需要索引）。
默认输入是仓库内的合成 ``demo-vault``，默认输出是 ``data/demo/product.sqlite3``。
本脚本不会构建 FAISS 索引；完整离线链路请运行 ``validate_mindgraph_offline.py``。
"""
from __future__ import annotations

import argparse
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

DEFAULT_VAULT = ROOT / "demo-vault"
DEFAULT_DB_PATH = ROOT / "data" / "demo" / "product.sqlite3"
DEMO_TAG = "offline-demo"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="把合成 Vault 同步到独立 MindGraph 演示数据库")
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT, help="Markdown Vault 路径")
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="演示 SQLite 路径；该数据库中的 notes 会与指定 Vault 同步",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    vault = args.vault.resolve()
    db_path = args.database.resolve()
    if not vault.is_dir():
        print(f"错误：Vault 不存在或不是目录：{vault}")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="mg_offline_"))
    copy = tmp / "vault"
    try:
        print(f"复制 Vault {vault} -> {copy}")
        shutil.copytree(
            vault,
            copy,
            ignore=shutil.ignore_patterns(".venv", "node_modules", "__pycache__", ".git", ".obsidian", ".trash"),
        )

        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = ProductDatabase(db_path)
        db.initialize()
        svc = VaultSyncService(db, copy, write_ids=True)
        res = svc.scan_vault()
        print(f"扫描完成：scanned={len(res.scanned)} skipped={len(res.skipped)} errors={len(res.errors)}")
        for error in res.errors[:10]:
            print("  ERR", error)

        ids = [note.note_id for note in res.scanned]
        print(f"本次同步 notes：{len(ids)}")
        if len(ids) < 8:
            print("错误：笔记不足 8 篇，无法构造关系示例")
            return 1

        now = now_iso()

        def insert_rel(source: str, target: str, relation_type: str, status: str, confidence: float) -> None:
            relation_id = "rel-" + uuid.uuid4().hex[:12]
            resolved = now if status == "confirmed" else None
            resolved_by = DEMO_TAG if status == "confirmed" else None
            db.execute(
                "INSERT INTO note_relations "
                "(relation_id, source_note_id, target_note_id, relation_type, direction, status, "
                "evidence_chunk_id, confidence, model_version, prompt_version, proposed_at, resolved_at, resolved_by) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    relation_id,
                    source,
                    target,
                    relation_type,
                    "outgoing",
                    status,
                    None,
                    confidence,
                    DEMO_TAG,
                    "v1",
                    now,
                    resolved,
                    resolved_by,
                ),
            )

        # 幂等重建：只替换本脚本自己的演示关系，不碰其他模型或人工关系。
        db.execute("DELETE FROM note_relations WHERE model_version=?", (DEMO_TAG,))
        insert_rel(ids[0], ids[1], "supports", "confirmed", 0.91)
        insert_rel(ids[2], ids[3], "supplements", "confirmed", 0.86)
        insert_rel(ids[4], ids[5], "potentially_related", "proposed", 0.71)
        insert_rel(ids[6], ids[7], "potentially_related", "proposed", 0.63)
        insert_rel(ids[0], ids[1], "duplicates", "proposed", 0.58)

        count = db.fetch_one(
            "SELECT COUNT(*) AS c FROM note_relations WHERE model_version=?",
            (DEMO_TAG,),
        )["c"]
        print(f"插入 demo 关系：{count} 条（confirmed 2 + proposed 3，含 1 条冲突样例）")
        print(f"完成。演示数据库：{db_path}")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
