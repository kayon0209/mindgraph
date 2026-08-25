from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sqlite3
import time
from typing import Any

logger = logging.getLogger("mindgraph.database")

SCHEMA_VERSION = 9
SOURCE_OWNERSHIP_SCHEMA_VERSION = 8

GOVERNANCE_TABLES = (
    "governance_cases",
    "governance_case_notes",
    "governance_note_state",
    "governance_events",
)
GOVERNANCE_SCHEMA_OBJECTS = frozenset(
    {
        *GOVERNANCE_TABLES,
        "schema_migration_runs",
        "governance_events_no_replace",
        "governance_events_no_update",
        "governance_events_no_delete",
        "idx_governance_cases_status_policy",
        "idx_governance_case_notes_note",
        "idx_governance_events_case",
        "idx_governance_events_note",
        "idx_governance_events_policy",
        "idx_governance_events_created_at",
        "idx_governance_note_state_disposition",
    }
)
GOVERNANCE_SCHEMA_OBJECT_COUNT = len(GOVERNANCE_SCHEMA_OBJECTS)

_GOVERNANCE_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS governance_cases (
      case_id TEXT PRIMARY KEY,
      case_type TEXT NOT NULL,
      policy_key TEXT,
      status TEXT NOT NULL CHECK(status IN ('proposed','confirmed','rejected','revoked')),
      canonical_note_id TEXT,
      reason_code TEXT NOT NULL,
      rule_key TEXT NOT NULL,
      evidence_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      resolved_at TEXT,
      resolved_by TEXT,
      request_id TEXT,
      UNIQUE(case_type, rule_key),
      FOREIGN KEY(canonical_note_id) REFERENCES notes(note_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS governance_case_notes (
      case_id TEXT NOT NULL,
      note_id TEXT NOT NULL,
      participant_role TEXT NOT NULL
        CHECK(participant_role IN ('candidate','canonical','alias','superseded')),
      PRIMARY KEY(case_id, note_id),
      FOREIGN KEY(case_id) REFERENCES governance_cases(case_id),
      FOREIGN KEY(note_id) REFERENCES notes(note_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS governance_note_state (
      note_id TEXT PRIMARY KEY,
      evaluated_on TEXT NOT NULL,
      lifecycle_state TEXT NOT NULL
        CHECK(lifecycle_state IN ('not_yet_effective','current','expired','historical','unresolved')),
      disposition TEXT NOT NULL
        CHECK(disposition IN ('eligible','excluded','unresolved','conflict_blocked','duplicate_alias')),
      reason_codes_json TEXT NOT NULL,
      decision_fingerprint TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      FOREIGN KEY(note_id) REFERENCES notes(note_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS governance_events (
      event_id TEXT PRIMARY KEY,
      case_id TEXT,
      note_id TEXT,
      policy_key TEXT,
      actor TEXT NOT NULL,
      action TEXT NOT NULL
        CHECK(action IN ('state_changed','proposed','confirmed','rejected','revoked')),
      previous_state_json TEXT NOT NULL,
      new_state_json TEXT NOT NULL,
      reason_code TEXT NOT NULL,
      evidence_ids_json TEXT NOT NULL,
      source TEXT NOT NULL
        CHECK(source IN ('ingestion_rule','lifecycle_rule','human_review')),
      request_id TEXT,
      created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schema_migration_runs (
      run_id TEXT PRIMARY KEY,
      migration_name TEXT NOT NULL,
      previous_version INTEGER NOT NULL,
      target_version INTEGER NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('completed','rolled_back')),
      object_count INTEGER NOT NULL,
      started_at TEXT NOT NULL,
      completed_at TEXT NOT NULL,
      rolled_back_at TEXT
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS governance_events_no_replace
    BEFORE INSERT ON governance_events
    WHEN EXISTS (
      SELECT 1 FROM governance_events WHERE event_id = NEW.event_id
    ) BEGIN
      SELECT RAISE(ABORT, 'governance_events are append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS governance_events_no_update
    BEFORE UPDATE ON governance_events BEGIN
      SELECT RAISE(ABORT, 'governance_events are append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS governance_events_no_delete
    BEFORE DELETE ON governance_events BEGIN
      SELECT RAISE(ABORT, 'governance_events are append-only');
    END
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_governance_cases_status_policy
    ON governance_cases(status, policy_key)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_governance_case_notes_note
    ON governance_case_notes(note_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_governance_events_case
    ON governance_events(case_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_governance_events_note
    ON governance_events(note_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_governance_events_policy
    ON governance_events(policy_key)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_governance_events_created_at
    ON governance_events(created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_governance_note_state_disposition
    ON governance_note_state(disposition)
    """,
)


def _create_governance_schema(connection: sqlite3.Connection) -> None:
    """Create schema-9 governance objects without committing the caller's transaction."""
    for statement in _GOVERNANCE_SCHEMA_STATEMENTS:
        connection.execute(statement)


class ProductDatabase:
    """生产级 SQLite 数据库封装。

    特性:
    - WAL 模式(提升并发读写性能)
    - 自动 WAL checkpoint
    - 连接超时与重试
    - 慢查询日志
    - 外键约束
    """

    _MAX_RETRIES = 3
    _RETRY_DELAY = 0.1
    _SLOW_QUERY_THRESHOLD_MS: float = 500.0

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._SLOW_QUERY_THRESHOLD_MS = float(os.getenv("SLOW_QUERY_THRESHOLD_MS", "500"))
        except (ValueError, TypeError):
            self._SLOW_QUERY_THRESHOLD_MS = 500.0

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            timeout=10.0,  # 10秒连接超时
        )
        connection.row_factory = sqlite3.Row
        # 启用 WAL 模式 + 外键约束
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA cache_size=-20000")  # 20MB
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")  # 5秒忙等待
        return connection

    def _log_slow_query(self, sql: str, elapsed_ms: float) -> None:
        if elapsed_ms > self._SLOW_QUERY_THRESHOLD_MS:
            logger.warning(
                "slow_query",
                extra={"sql": sql[:200], "elapsed_ms": round(elapsed_ms, 3)},
            )

    def _cursor_with_retry(self) -> sqlite3.Connection:
        """带重试的数据库连接获取(返回普通连接, 调用方负责关闭)。"""
        last_error: Exception | None = None
        for attempt in range(self._MAX_RETRIES):
            try:
                conn = self.connect()
                return conn
            except sqlite3.OperationalError as exc:
                last_error = exc
                if "database is locked" in str(exc).lower() and attempt < self._MAX_RETRIES - 1:
                    logger.warning("database_locked_retry", extra={"attempt": attempt + 1})
                    time.sleep(self._RETRY_DELAY * (attempt + 1))
                    continue
                raise
        raise last_error  # type: ignore[misc]

    def close(self) -> None:
        """关闭数据库连接并执行 WAL checkpoint。"""
        try:
            with self.connect() as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                logger.info("database_closed_with_checkpoint")
        except Exception as exc:
            logger.warning("database_close_warning", extra={"error": str(exc)})

    def initialize(self) -> None:
        with self.connect() as connection:
            schema_meta_existed = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_meta'"
            ).fetchone() is not None
            initial_schema_row = (
                connection.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
                if schema_meta_existed
                else None
            )
            initial_schema_version = (
                int(initial_schema_row[0]) if initial_schema_row is not None else None
            )
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS query_logs (
                    request_id TEXT PRIMARY KEY, question TEXT, question_hash TEXT NOT NULL,
                    answer TEXT, result_state TEXT NOT NULL, requested_strategy TEXT NOT NULL,
                    actual_strategy TEXT NOT NULL, trace_json TEXT NOT NULL, citations_json TEXT NOT NULL,
                    timing_json TEXT NOT NULL, usage_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS feedback (
                    feedback_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                    rating TEXT NOT NULL, reason_codes_json TEXT NOT NULL, comment TEXT,
                    created_at TEXT NOT NULL, FOREIGN KEY(request_id) REFERENCES query_logs(request_id)
                );
                CREATE TABLE IF NOT EXISTS bad_cases (
                    bad_case_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                    question TEXT, answer TEXT, retrieved_chunks_json TEXT NOT NULL,
                    error_category TEXT NOT NULL, status TEXT NOT NULL, reviewer_note TEXT,
                    resolution TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evaluation_runs (
                    run_id TEXT PRIMARY KEY, status TEXT NOT NULL, dataset_name TEXT NOT NULL,
                    dataset_version TEXT NOT NULL, retrieval_strategy TEXT NOT NULL, chat_model TEXT,
                    started_at TEXT, finished_at TEXT, configuration_json TEXT NOT NULL,
                    summary_metrics_json TEXT NOT NULL, category_metrics_json TEXT NOT NULL,
                    failed_cases_json TEXT NOT NULL, result_files_json TEXT NOT NULL,
                    progress_messages_json TEXT NOT NULL, error TEXT
                );
                CREATE TABLE IF NOT EXISTS document_versions (
                    document_id TEXT PRIMARY KEY, logical_document_id TEXT NOT NULL, version TEXT NOT NULL,
                    title TEXT NOT NULL, file_type TEXT NOT NULL, knowledge_category TEXT NOT NULL,
                    authority_level TEXT NOT NULL, effective_date TEXT, expiration_date TEXT, status TEXT NOT NULL,
                    checksum TEXT NOT NULL, supersedes_version TEXT, source_path TEXT NOT NULL,
                    parsing_diagnostics_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    indexed_at TEXT, created_by TEXT, UNIQUE(logical_document_id, version)
                );
                CREATE TABLE IF NOT EXISTS index_builds (
                    index_version TEXT PRIMARY KEY, status TEXT NOT NULL, manifest_json TEXT NOT NULL,
                    previous_index_version TEXT, created_at TEXT NOT NULL, activated_at TEXT, failure_reason TEXT
                );
                CREATE TABLE IF NOT EXISTS index_audit (
                    audit_id TEXT PRIMARY KEY, action TEXT NOT NULL, from_version TEXT, to_version TEXT,
                    operator TEXT, reason TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS access_audit (
                    audit_id TEXT PRIMARY KEY,
                    request_id TEXT,
                    actor TEXT,
                    action TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_access_audit_action ON access_audit(action);
                CREATE INDEX IF NOT EXISTS idx_access_audit_resource ON access_audit(resource);
                CREATE TABLE IF NOT EXISTS embedding_cache (
                    model_name TEXT NOT NULL, model_revision TEXT, chunk_checksum TEXT NOT NULL,
                    dimension INTEGER NOT NULL, embedding_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(model_name, model_revision, chunk_checksum)
                );
                CREATE TABLE IF NOT EXISTS datasets (
                    dataset_id TEXT NOT NULL, version TEXT NOT NULL, dataset_type TEXT NOT NULL,
                    purpose TEXT NOT NULL, created_at TEXT NOT NULL, case_count INTEGER NOT NULL,
                    category_distribution_json TEXT NOT NULL, annotation_status TEXT NOT NULL,
                    change_history_json TEXT NOT NULL, PRIMARY KEY(dataset_id, version)
                );
                CREATE TABLE IF NOT EXISTS annotations (
                    annotation_id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, dataset_version TEXT NOT NULL,
                    case_id TEXT NOT NULL, payload_json TEXT NOT NULL, reviewer TEXT,
                    review_status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS human_reviews (
                    review_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, case_id TEXT NOT NULL,
                    reviewer TEXT NOT NULL, scores_json TEXT NOT NULL, reason TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS prompts (
                    prompt_id TEXT NOT NULL, version TEXT NOT NULL, content TEXT NOT NULL,
                    checksum TEXT NOT NULL, created_at TEXT NOT NULL, change_notes TEXT NOT NULL,
                    status TEXT NOT NULL, PRIMARY KEY(prompt_id, version)
                );
                CREATE TABLE IF NOT EXISTS notes (
                    note_id TEXT PRIMARY KEY,
                    vault_path TEXT NOT NULL UNIQUE,
                    source_id TEXT NOT NULL DEFAULT 'builtin',
                    title TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    frontmatter_json TEXT NOT NULL DEFAULT '{}',
                    ai_access_level TEXT NOT NULL DEFAULT 'local_only',
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    index_status TEXT NOT NULL DEFAULT 'pending',
                    index_version TEXT,
                    workspace TEXT,
                    department TEXT,
                    acl_json TEXT NOT NULL DEFAULT '{}',
                    acl_public INTEGER NOT NULL DEFAULT 0,
                    policy_key TEXT,
                    owner TEXT,
                    document_version TEXT,
                    effective_from TEXT,
                    effective_to TEXT,
                    policy_status TEXT NOT NULL DEFAULT 'unspecified',
                    metadata_issues_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_indexed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS note_relations (
                    relation_id TEXT PRIMARY KEY,
                    source_note_id TEXT NOT NULL,
                    target_note_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    direction TEXT NOT NULL DEFAULT 'outgoing',
                    status TEXT NOT NULL DEFAULT 'proposed',
                    evidence_chunk_id TEXT,
                    confidence REAL NOT NULL DEFAULT 0.0,
                    model_version TEXT,
                    prompt_version TEXT,
                    proposed_at TEXT NOT NULL,
                    resolved_at TEXT,
                    resolved_by TEXT,
                    FOREIGN KEY(source_note_id) REFERENCES notes(note_id),
                    FOREIGN KEY(target_note_id) REFERENCES notes(note_id)
                );
                CREATE INDEX IF NOT EXISTS idx_notes_status ON notes(index_status);
                CREATE INDEX IF NOT EXISTS idx_note_relations_source ON note_relations(source_note_id);
                CREATE INDEX IF NOT EXISTS idx_note_relations_status ON note_relations(status);
                CREATE TABLE IF NOT EXISTS connector_syncs (
                    connector_id TEXT PRIMARY KEY,
                    connector_type TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    workspace TEXT,
                    department TEXT,
                    status TEXT NOT NULL,
                    file_count INTEGER NOT NULL DEFAULT 0,
                    added INTEGER NOT NULL DEFAULT 0,
                    updated INTEGER NOT NULL DEFAULT 0,
                    pruned INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_connector_syncs_source ON connector_syncs(source_path);
                CREATE INDEX IF NOT EXISTS idx_connector_syncs_status ON connector_syncs(status);
                CREATE TABLE IF NOT EXISTS acl_backfill_runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    rolled_back_at TEXT,
                    item_count INTEGER NOT NULL DEFAULT 0,
                    changed_count INTEGER NOT NULL DEFAULT 0,
                    unresolved_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS acl_backfill_items (
                    run_id TEXT NOT NULL,
                    note_id TEXT NOT NULL,
                    old_workspace TEXT,
                    old_department TEXT,
                    old_acl_json TEXT NOT NULL,
                    old_acl_public INTEGER NOT NULL,
                    planned_workspace TEXT,
                    planned_department TEXT,
                    planned_acl_json TEXT NOT NULL,
                    planned_acl_public INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, note_id),
                    FOREIGN KEY(run_id) REFERENCES acl_backfill_runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_acl_backfill_items_note
                    ON acl_backfill_items(note_id);
            """)
            migrate_source_ownership = (
                initial_schema_version is None
                or initial_schema_version < SOURCE_OWNERSHIP_SCHEMA_VERSION
            )
            self._ensure_columns(connection, "query_logs", {
                "index_version": "TEXT", "prompt_version": "TEXT",
                "requested_provider": "TEXT", "actual_provider": "TEXT",
                "query_date": "TEXT", "category_filter_json": "TEXT NOT NULL DEFAULT '[]'",
            })
            self._ensure_columns(connection, "evaluation_runs", {
                "index_version": "TEXT", "prompt_version": "TEXT", "provider": "TEXT",
            })
            self._ensure_columns(connection, "document_versions", {
                "workspace": "TEXT",
                "department": "TEXT",
                "acl_json": "TEXT NOT NULL DEFAULT '{}'",
                "acl_public": "INTEGER NOT NULL DEFAULT 0",
            })
            self._ensure_columns(connection, "notes", {
                "source_id": "TEXT NOT NULL DEFAULT 'builtin'",
                "workspace": "TEXT",
                "department": "TEXT",
                "acl_json": "TEXT NOT NULL DEFAULT '{}'",
                "acl_public": "INTEGER NOT NULL DEFAULT 0",
                "policy_key": "TEXT",
                "owner": "TEXT",
                "document_version": "TEXT",
                "effective_from": "TEXT",
                "effective_to": "TEXT",
                "policy_status": "TEXT NOT NULL DEFAULT 'unspecified'",
                "metadata_issues_json": "TEXT NOT NULL DEFAULT '[]'",
            })
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_notes_policy_lifecycle "
                "ON notes(policy_key, policy_status, effective_from, effective_to)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_notes_workspace ON notes(workspace)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_notes_department ON notes(department)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_notes_acl_public ON notes(acl_public)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_notes_source_id ON notes(source_id)"
            )
            if migrate_source_ownership:
                connection.execute(
                    """
                    UPDATE notes
                    SET source_id = COALESCE(
                        (
                            SELECT connector_id
                            FROM connector_syncs
                            WHERE status = 'completed'
                              AND substr(
                                  replace(notes.vault_path, '\\', '/'),
                                  1,
                                  length(connector_id) + 1
                              ) = connector_id || '/'
                            ORDER BY finished_at DESC
                            LIMIT 1
                        ),
                        'builtin'
                    )
                    """
                )
            if not schema_meta_existed:
                _create_governance_schema(connection)
                connection.execute("INSERT INTO schema_meta(version) VALUES (?)", (SCHEMA_VERSION,))
            elif initial_schema_version == SCHEMA_VERSION:
                _create_governance_schema(connection)
            elif initial_schema_row is None:
                connection.execute(
                    "INSERT INTO schema_meta(version) VALUES (?)",
                    (SOURCE_OWNERSHIP_SCHEMA_VERSION,),
                )
            elif initial_schema_version < SOURCE_OWNERSHIP_SCHEMA_VERSION:
                connection.execute(
                    "UPDATE schema_meta SET version=?",
                    (SOURCE_OWNERSHIP_SCHEMA_VERSION,),
                )

    @staticmethod
    def _ensure_columns(connection: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
        # PRAGMA 不支持参数化查询 —— 但 table 名来自我们自己的代码, 非用户输入
        # 仅允许字母/数字/下划线组成的表名
        if not table.replace("_", "").isalnum():
            raise ValueError(f"Invalid table name: {table}")
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for name, declaration in columns.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE \"{table}\" ADD COLUMN \"{name}\" {declaration}")

    def mark_abandoned_runs_interrupted(self) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE evaluation_runs SET status='interrupted', error='Service restarted before completion' WHERE status IN ('queued','running')"
            )

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        started = time.perf_counter()
        conn = self._cursor_with_retry()
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()
        self._log_slow_query(sql, (time.perf_counter() - started) * 1000)

    def execute_many(self, sql: str, params_list: list[tuple[Any, ...]]) -> None:
        started = time.perf_counter()
        conn = self._cursor_with_retry()
        try:
            conn.executemany(sql, params_list)
            conn.commit()
        finally:
            conn.close()
        self._log_slow_query(sql, (time.perf_counter() - started) * 1000)

    def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        started = time.perf_counter()
        conn = self._cursor_with_retry()
        try:
            row = conn.execute(sql, params).fetchone()
        finally:
            conn.close()
        self._log_slow_query(sql, (time.perf_counter() - started) * 1000)
        return dict(row) if row else None

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        started = time.perf_counter()
        conn = self._cursor_with_retry()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        self._log_slow_query(sql, (time.perf_counter() - started) * 1000)
        return [dict(row) for row in rows]


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(value: str | None, default: Any) -> Any:
    return json.loads(value) if value else default
