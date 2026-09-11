"""索引一致性审计：把「notes 声明 vs 活跃索引实际」的偏差打印成人能读的表。

用法（项目根目录）：

    ./.venv/Scripts/python.exe scripts/audit_index_consistency.py
    # 需要机器可读输出时加 --json

为什么需要这个脚本：``data/retrieval_indexes/`` 下有三个 builder 在写索引
（``m3-`` 文件扫描 / ``m4-`` document_versions / ``mg-`` notes 表），``CURRENT``
指向哪一版取决于最后一次构建。2026-09-09 就出现过「``notes`` 显示 25 篇 ready，
活跃索引实际只有 4 篇 / 69 chunks」的静默分叉——索引规模、按源过滤、ACL 过滤
同时失真，而整条链路上没有任何提示。这个脚本让分叉变成一行结论。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from application.index_metadata import audit_index_consistency, parse_included_subtrees  # noqa: E402

INDEX_ROOT = ROOT / "data" / "retrieval_indexes"
DB_PATH = ROOT / "data" / "product" / "product.sqlite3"


def _default_included_subtrees() -> tuple[str, ...]:
    """默认读应用配置（``INDEX_INCLUDED_SUBTREES``）；读不到就退回"仅根目录"。"""
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from infrastructure.settings import get_settings  # noqa: PLC0415

        return parse_included_subtrees(getattr(get_settings(), "INDEX_INCLUDED_SUBTREES", ""))
    except Exception:  # 配置不可用不应让审计失败，退回声明的默认范围
        return parse_included_subtrees("")


def main() -> int:
    parser = argparse.ArgumentParser(description="审计 MindGraph 索引与 notes 表的一致性")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON 报告")
    parser.add_argument("--index-root", default=str(INDEX_ROOT))
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument(
        "--included-subtrees",
        default=None,
        help="已声明的语料范围（逗号分隔的一级子树；空 = 仅根目录）。默认取应用配置。",
    )
    args = parser.parse_args()

    included = (
        parse_included_subtrees(args.included_subtrees)
        if args.included_subtrees is not None
        else _default_included_subtrees()
    )
    report = audit_index_consistency(index_root=args.index_root, db_path=args.db, included_subtrees=included)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["scope_consistent"] else 1

    print("=" * 68)
    print("MindGraph 索引一致性审计")
    print("=" * 68)
    print(f"活跃索引版本        : {report['index_version']}")
    print(f"元数据事实源可用    : {report['metadata_source_available']}"
          + (f"（{report['metadata_source_reason']}）" if report["metadata_source_reason"] else ""))
    print(f"已声明的语料范围    : "
          + ("、".join(report["included_subtrees"]) if report["included_subtrees"] else "仅 vault 根目录"))
    print(f"notes 声明可检索    : {report['declared_documents']} 篇")
    print(f"索引实际覆盖        : {report['indexed_documents']} 篇 / {report['chunks']} chunks")
    print(f"带 workspace 元数据 : {report['chunks_with_workspace']} chunks")
    print(f"带 acl_json 元数据  : {report['chunks_with_acl']} chunks")
    print("-" * 68)
    if report["consistent"]:
        print("结论：一致 ✅")
    elif report["scope_consistent"]:
        print("结论：与已声明口径一致 ✅（存在范围外缺失，属已接受的口径）")
    else:
        print("结论：分叉 ❌（范围内缺失或索引含 notes 不认识的文档）")
    if report["in_scope_missing_count"]:
        print(f"\n【需要处理】范围内该进索引却没有（{report['in_scope_missing_count']} 篇）：")
        for item in report["in_scope_missing"][:20]:
            print(f"  - {item}")
    if report["undeclared_in_index_count"]:
        print(f"\n【需要处理】索引里有但 notes 未声明（{report['undeclared_in_index_count']} 篇）：")
        for item in report["undeclared_in_index"]:
            print(f"  - {item}")
    if report["out_of_scope_missing_count"]:
        print(f"\n【已声明范围外，不处理】{report['out_of_scope_missing_count']} 篇，按子树：")
        for subtree, count in report["out_of_scope_subtrees"].items():
            print(f"  - {subtree}/: {count} 篇")
        print("  （要收录它们 = 改语料口径，须重跑检索消融并重新公布指标）")
    print("-" * 68)
    print("提示：索引缩水会被 POST /knowledge/index/rebuild 的准入守卫拦下（需显式 force）。")
    return 0 if report["scope_consistent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
