"""M0-7：数据库 schema 兼容性与幂等性测试。

背景：代码 SCHEMA_VERSION=9，在线库（data/product/product.sqlite3）仍为 v8
（v8→v9 只发布增量 DDL，无破坏）。本测试把“从任意既有版本幂等升级到 9、
且不丢数据”变成正式护栏；真实在线库的漂移修复在验证阶段执行
（python -c 调 ProductDatabase.initialize()）。
"""

from __future__ import annotations

from pathlib import Path

from infrastructure.database import SCHEMA_VERSION, ProductDatabase

V9_ONLY_TABLES = ("concept_signals", "concept_mine_runs")


def _stored_version(database: ProductDatabase) -> int:
    row = database.fetch_one("SELECT version FROM schema_meta LIMIT 1")
    assert row is not None
    return int(row["version"])


def _table_names(database: ProductDatabase) -> set[str]:
    rows = database.fetch_all("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    return {row["name"] for row in rows}


def test_fresh_database_initializes_to_current_version(tmp_path: Path):
    database = ProductDatabase(tmp_path / "fresh.sqlite3")
    database.initialize()
    try:
        assert _stored_version(database) == SCHEMA_VERSION
        assert V9_ONLY_TABLES[0] in _table_names(database)
        assert V9_ONLY_TABLES[1] in _table_names(database)
    finally:
        database.close()


def test_initialize_is_idempotent(tmp_path: Path):
    database = ProductDatabase(tmp_path / "twice.sqlite3")
    database.initialize()
    database.initialize()
    try:
        assert _stored_version(database) == SCHEMA_VERSION
    finally:
        database.close()


def test_upgrade_from_simulated_v8_is_additive_and_preserves_rows(tmp_path: Path):
    """模拟 v8 库（回退版本号 + 删除 v9 表）→ 再跑 initialize()：

    - 版本号回到 SCHEMA_VERSION；
    - v9 表重建；
    - 既有业务数据行原样保留（升级只做加法）。
    """
    database = ProductDatabase(tmp_path / "v8.sqlite3")
    database.initialize()
    try:
        database.execute(
            "INSERT INTO query_logs (request_id, question_hash, result_state, requested_strategy, "
            "actual_strategy, trace_json, citations_json, timing_json, usage_json, created_at) "
            "VALUES ('keep-me', 'hash', 'answered', 'hybrid', 'hybrid', '{}', '[]', '{}', '{}', '2026-01-01T00:00:00')"
        )
        # 回退到 v8 语义：版本号降为 8，并删除 v9 独有的两张表
        database.execute("UPDATE schema_meta SET version=8")
        for table in V9_ONLY_TABLES:
            database.execute(f"DROP TABLE IF EXISTS {table}")  # nosec B608 -- 表名来自模块常量
        assert _stored_version(database) == 8

        # 升级路径：幂等 initialize()
        database.initialize()

        assert _stored_version(database) == SCHEMA_VERSION
        assert V9_ONLY_TABLES[0] in _table_names(database)
        assert V9_ONLY_TABLES[1] in _table_names(database)
        kept = database.fetch_one("SELECT request_id FROM query_logs WHERE request_id='keep-me'")
        assert kept is not None
    finally:
        database.close()
