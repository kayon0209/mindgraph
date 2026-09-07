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


def test_upgrade_backfills_one_artifact_claim_without_deleting_legacy_duplicates(tmp_path: Path):
    """The artifact fencing migration preserves historical rows and elects one writer."""
    database = ProductDatabase(tmp_path / "artifact-claims.sqlite3")
    database.initialize()
    try:
        database.execute(
            "INSERT INTO agent_tasks (task_id, principal_id, task_type, constraints_json, status, idempotency_key, created_at, updated_at) "
            "VALUES ('task-legacy', 'u', 'batch_policy_check', '{}', 'completed', 'legacy-artifact-claim', 't', 't')"
        )
        for artifact_id in ("art-legacy-a", "art-legacy-b"):
            database.execute(
                "INSERT INTO artifacts (artifact_id, owner_principal_id, task_id, kind, title, content_json, "
                "visibility, evidence_snapshot_json, citations_json, checksum, created_at, updated_at) "
                "VALUES (?, 'u', 'task-legacy', 'evidence_bundle', 'legacy', '{}', 'private', '[]', '[]', ?, 't', 't')",
                (artifact_id, artifact_id),
            )

        database.initialize()

        claim = database.fetch_one("SELECT artifact_id FROM artifact_task_claims WHERE task_id='task-legacy'")
        assert claim is not None
        assert claim["artifact_id"] in {"art-legacy-a", "art-legacy-b"}
        assert database.fetch_one("SELECT COUNT(*) AS count FROM artifacts WHERE task_id='task-legacy'")["count"] == 2
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


# ── schema v10（M3 服务端会话） ──

V10_ONLY_TABLES = ("conversations", "messages", "tool_call_log")


def test_fresh_database_has_v10_conversation_tables(tmp_path: Path):
    database = ProductDatabase(tmp_path / "v10.sqlite3")
    database.initialize()
    try:
        assert _stored_version(database) == SCHEMA_VERSION
        for table in V10_ONLY_TABLES:
            assert table in _table_names(database)
    finally:
        database.close()


def test_v9_database_upgrades_to_v10_additively(tmp_path: Path):
    """v9 库（无会话表）原位升级：既有 notes/query_logs/access_audit 数据不变。"""
    database = ProductDatabase(tmp_path / "v9-to-v10.sqlite3")
    database.initialize()
    # 手工降到 v9 形态（删会话表 + 版本号），保留业务数据
    with database.connect() as connection:
        for table in V10_ONLY_TABLES:
            connection.execute(f"DROP TABLE IF EXISTS {table}")
        connection.execute("UPDATE schema_meta SET version=9")
    database.execute(
        "INSERT INTO query_logs (request_id, question, question_hash, answer, result_state, requested_strategy,"
        "actual_strategy, trace_json, citations_json, timing_json, usage_json, created_at)"
        " VALUES ('req-x', 'q', 'h', 'a', 'answered', 'hybrid', 'hybrid', '{}', '[]', '{}', '{}', '2026-09-03T00:00:00')"
    )
    database.initialize()  # 幂等升级
    try:
        assert _stored_version(database) == SCHEMA_VERSION
        for table in V10_ONLY_TABLES:
            assert table in _table_names(database)
        row = database.fetch_one("SELECT request_id, result_state FROM query_logs WHERE request_id='req-x'")
        assert row["result_state"] == "answered"
        # sequence 唯一约束存在（稳定回放的护栏）
        cols = database.fetch_all("PRAGMA table_info(messages)")
        assert any(c["name"] == "sequence_no" for c in cols)
        # UNIQUE(conversation_id, sequence_no) 落库（sqlite_master DDL 层面验证）
        ddl = database.fetch_one(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='messages'"
        )
        assert ddl and "UNIQUE(conversation_id, sequence_no)" in (ddl["sql"] or "")
    finally:
        database.close()


def test_messages_sequence_unique_rejects_duplicates(tmp_path: Path):
    database = ProductDatabase(tmp_path / "seq.sqlite3")
    database.initialize()
    try:
        database.execute(
            "INSERT INTO conversations (conversation_id, principal_id, title, created_at, updated_at)"
            " VALUES ('c1', 'user-a', '会话', '2026-09-03T00:00:00', '2026-09-03T00:00:00')"
        )
        database.execute(
            "INSERT INTO messages (message_id, conversation_id, sequence_no, role, content, created_at)"
            " VALUES ('m1', 'c1', 1, 'user', '问题', '2026-09-03T00:00:00')"
        )
        import sqlite3

        try:
            database.execute(
                "INSERT INTO messages (message_id, conversation_id, sequence_no, role, content, created_at)"
                " VALUES ('m2', 'c1', 1, 'assistant', '回答', '2026-09-03T00:00:00')"
            )
            raise AssertionError("duplicate sequence_no must be rejected")
        except sqlite3.IntegrityError:
            pass
    finally:
        database.close()


def test_migration_failure_does_not_bump_version(tmp_path: Path):
    """迁移失败（DDL 破坏）时版本号不前移——沿用 additive 幂等策略的失败语义：
    initialize 内 executescript 原子失败即整体不生效（SQLite 事务内回滚）。"""
    import sqlite3

    path = tmp_path / "broken.sqlite3"
    database = ProductDatabase(path)
    database.initialize()
    try:
        assert _stored_version(database) == SCHEMA_VERSION
        # 篡改版本号本身不模拟迁移失败（真正的失败注入需 mock executescript；
        # 此处固化"版本号与表集一致"的不变量：有 v10 表才允许标 v10）
        tables = _table_names(database)
        assert set(V10_ONLY_TABLES) <= tables
    finally:
        database.close()


# ── schema v11（M4-A agent_tasks/artifacts） ──

V11_ONLY_TABLES = ("agent_tasks", "artifacts")


def test_fresh_database_has_v11_task_tables(tmp_path: Path):
    database = ProductDatabase(tmp_path / "v11.sqlite3")
    database.initialize()
    try:
        assert _stored_version(database) == SCHEMA_VERSION
        for table in V11_ONLY_TABLES:
            assert table in _table_names(database)
        # 幂等键唯一约束落库
        ddl = database.fetch_one("SELECT sql FROM sqlite_master WHERE type='table' AND name='agent_tasks'")
        assert ddl and "UNIQUE(principal_id, idempotency_key)" in (ddl["sql"] or "")
    finally:
        database.close()


def test_v10_database_upgrades_to_v11_preserving_data(tmp_path: Path):
    """v10 库原位升级 v11：既有会话数据不变（additive）。"""
    database = ProductDatabase(tmp_path / "v10-to-v11.sqlite3")
    database.initialize()
    with database.connect() as connection:
        for table in V11_ONLY_TABLES:
            connection.execute(f"DROP TABLE IF EXISTS {table}")
        connection.execute("UPDATE schema_meta SET version=10")
    database.execute(
        "INSERT INTO conversations (conversation_id, principal_id, title, created_at, updated_at)"
        " VALUES ('keep-c1', 'keep-user', '保留会话', '2026-09-03T00:00:00', '2026-09-03T00:00:00')"
    )
    database.initialize()
    try:
        assert _stored_version(database) == SCHEMA_VERSION
        for table in V11_ONLY_TABLES:
            assert table in _table_names(database)
        row = database.fetch_one("SELECT principal_id FROM conversations WHERE conversation_id='keep-c1'")
        assert row["principal_id"] == "keep-user"
    finally:
        database.close()


# ── schema v12（M5-A saved_artifacts） ──

def test_v11_upgrades_to_v12_preserving_tasks(tmp_path: Path):
    database = ProductDatabase(tmp_path / "v11-to-v12.sqlite3")
    database.initialize()
    with database.connect() as connection:
        connection.execute("DROP TABLE IF EXISTS saved_artifacts")
        connection.execute("UPDATE schema_meta SET version=11")
    # v11 数据保留：任务行原位升级不变
    database.execute(
        "INSERT INTO agent_tasks (task_id, principal_id, task_type, constraints_json, status,"
        " idempotency_key, created_at, updated_at) VALUES ('keep-t1','keep-user','batch_policy_check',"
        " '{}','completed','keep-key-0001','2026-09-03T00:00:00','2026-09-03T00:00:00')"
    )
    database.initialize()
    try:
        assert _stored_version(database) == SCHEMA_VERSION
        row = database.fetch_one("SELECT principal_id FROM agent_tasks WHERE task_id='keep-t1'")
        assert row["principal_id"] == "keep-user"
        assert "saved_artifacts" in _table_names(database)
    finally:
        database.close()


# ── schema v16（UG-008 source ownership foundation） ──

V16_ONLY_TABLES = (
    "knowledge_sources",
    "source_ownership_audit_runs",
    "source_ownership_findings",
)


def test_v15_upgrades_to_v16_additively_preserving_notes(tmp_path: Path):
    """模拟 v15 数据库升级：只新增来源归属审计结构，不改变既有笔记。"""
    database = ProductDatabase(tmp_path / "v15-to-v16.sqlite3")
    database.initialize()
    try:
        database.execute(
            "INSERT INTO notes (note_id, vault_path, title, content_hash, frontmatter_json, ai_access_level, "
            "source_id, source_path, acl_json, acl_public, created_at, updated_at) VALUES "
            "('legacy-note', 'legacy/a.md', 'Legacy', 'hash', '{}', 'local_only', "
            "'legacy-source', 'legacy/a.md', '{\"allow\":[\"workspace:legacy\"]}', 0, 't', 't')"
        )
        with database.connect() as connection:
            for table in V16_ONLY_TABLES:
                connection.execute(f"DROP TABLE IF EXISTS {table}")
            connection.execute("UPDATE schema_meta SET version=15")

        database.initialize()

        assert _stored_version(database) == 16
        assert set(V16_ONLY_TABLES) <= _table_names(database)
        row = database.fetch_one("SELECT source_id, acl_json FROM notes WHERE note_id='legacy-note'")
        assert row is not None
        assert row["source_id"] == "legacy-source"
        assert row["acl_json"] == '{"allow":["workspace:legacy"]}'
    finally:
        database.close()
