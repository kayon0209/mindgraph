from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator

import sqlite3

from infrastructure.sqlite_runtime import require_safe_sqlite_runtime

logger = logging.getLogger("mindgraph.database")

SCHEMA_VERSION = 15


class ProductDatabase:
    """生产级 SQLite 数据库封装。

    特性:
    - WAL 模式（提升并发读写性能）
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
        # 线程级连接复用：此前每条 SQL 新开连接（含 5 条 PRAGMA），高频请求下
        # 开销显著；SQLite 连接线程绑定时用 threading.local 缓存即可。
        self._local = threading.local()
        try:
            self._SLOW_QUERY_THRESHOLD_MS = float(os.getenv("SLOW_QUERY_THRESHOLD_MS", "500"))
        except (ValueError, TypeError):
            self._SLOW_QUERY_THRESHOLD_MS = 500.0

    def connect(self) -> sqlite3.Connection:
        require_safe_sqlite_runtime()
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

    def _thread_connection(self) -> sqlite3.Connection:
        """获取当前线程的复用连接（惰性建立，PRAGMA 只设一次）。"""
        connection: sqlite3.Connection | None = getattr(self._local, "connection", None)
        if connection is None:
            connection = self.connect()
            self._local.connection = connection
        return connection

    def _cursor_with_retry(self) -> sqlite3.Connection:
        """带重试的数据库连接获取（返回可复用连接，调用方不再手动关闭）。"""
        last_error: Exception | None = None
        for attempt in range(self._MAX_RETRIES):
            try:
                return self._thread_connection()
            except sqlite3.OperationalError as exc:
                last_error = exc
                if "database is locked" in str(exc).lower() and attempt < self._MAX_RETRIES - 1:
                    logger.warning("database_locked_retry", extra={"attempt": attempt + 1})
                    time.sleep(self._RETRY_DELAY * (attempt + 1))
                    continue
                raise
        raise last_error  # type: ignore[misc]

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """多语句原子事务：全部成功才提交，任一失败整体回滚。

        用于跨语句业务写入（如批量关系状态变更），避免中途失败留下半成品状态。
        """
        connection = self._cursor_with_retry()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def close(self) -> None:
        """关闭线程本地连接并执行 WAL checkpoint。"""
        connection: sqlite3.Connection | None = getattr(self._local, "connection", None)
        if connection is not None:
            try:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception as exc:
                logger.warning("database_close_warning", extra={"error": str(exc)})
            finally:
                try:
                    connection.close()
                except Exception as exc:
                    logger.warning("database_connection_close_warning", extra={"error": str(exc)})
                finally:
                    self._local.connection = None
        logger.info("database_closed_with_checkpoint")

    def initialize(self) -> None:
        with closing(self.connect()) as connection, connection:
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
                    title TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    frontmatter_json TEXT NOT NULL DEFAULT '{}',
                    ai_access_level TEXT NOT NULL DEFAULT 'local_only',
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    index_status TEXT NOT NULL DEFAULT 'pending',
                    index_version TEXT,
                    workspace TEXT,
                    department TEXT,
                    source_id TEXT,
                    source_path TEXT,
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
                CREATE TABLE IF NOT EXISTS concept_signals (
                    term TEXT PRIMARY KEY,
                    seen_count INTEGER NOT NULL DEFAULT 1,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    sample_question_hash TEXT
                );
                CREATE TABLE IF NOT EXISTS concept_mine_runs (
                    run_id TEXT PRIMARY KEY,
                    trigger TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    questions_scanned INTEGER NOT NULL DEFAULT 0,
                    proposed_created INTEGER NOT NULL DEFAULT 0,
                    gap_terms INTEGER NOT NULL DEFAULT 0
                );
            """)
            self._ensure_columns(connection, "query_logs", {
                "index_version": "TEXT", "prompt_version": "TEXT",
                "requested_provider": "TEXT", "actual_provider": "TEXT",
                "query_date": "TEXT", "category_filter_json": "TEXT NOT NULL DEFAULT '[]'",
                # ── schema v13（安全审查 F1）：问答归属列——feedback 工具 preview
                # 按归属校验，杜绝跨主体枚举 request_id 窥探他人问答。
                "principal_id": "TEXT",
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
                "workspace": "TEXT",
                "department": "TEXT",
                "source_id": "TEXT",
                "source_path": "TEXT",
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
            self._ensure_columns(connection, "note_relations", {
                "evidence_span": "TEXT",
                "evidence_section": "TEXT",
                "source_document_version": "TEXT",
                "effective_from": "TEXT",
                "effective_to": "TEXT",
                "extraction_method": "TEXT",
            })
            # ── schema v10（M3 服务端会话，ADR-003/实施方案 §6.1 修订版） ──
            # 只新增表与索引，不改既有表；owner 校验一律用稳定 principal_id，
            # 不用展示名。默认不保存完整工具参数/结果（脱敏字段承载）。
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    workspace TEXT,
                    department TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    retention_until TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    sequence_no INTEGER NOT NULL,
                    request_id TEXT,
                    role TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
                    content TEXT NOT NULL,
                    tool_call_id TEXT,
                    citations_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    UNIQUE(conversation_id, sequence_no),
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
                );
                CREATE TABLE IF NOT EXISTS tool_call_log (
                    tool_call_id TEXT PRIMARY KEY,
                    conversation_id TEXT,
                    request_id TEXT,
                    principal_id TEXT,
                    tool_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    arguments_redacted_json TEXT NOT NULL DEFAULT '{}',
                    arguments_hash TEXT,
                    result_summary_json TEXT NOT NULL DEFAULT '{}',
                    error_code TEXT,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
                );
                CREATE INDEX IF NOT EXISTS idx_conversations_owner
                    ON conversations(principal_id, status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_messages_conversation
                    ON messages(conversation_id, sequence_no);
                CREATE INDEX IF NOT EXISTS idx_tool_call_log_conversation
                    ON tool_call_log(conversation_id, started_at);
                CREATE INDEX IF NOT EXISTS idx_tool_call_log_request
                    ON tool_call_log(request_id);
                CREATE TABLE IF NOT EXISTS agent_tasks (
                    task_id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    workspace TEXT,
                    department TEXT,
                    conversation_id TEXT,
                    task_type TEXT NOT NULL,
                    constraints_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'queued',
                    result_state TEXT,
                    idempotency_key TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    cancel_requested_at TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(principal_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    owner_principal_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content_json TEXT NOT NULL DEFAULT '{}',
                    visibility TEXT NOT NULL DEFAULT 'private',
                    evidence_snapshot_json TEXT NOT NULL DEFAULT '[]',
                    citations_json TEXT NOT NULL DEFAULT '[]',
                    checksum TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES agent_tasks(task_id)
                );
                -- schema v15：task_id 唯一 claim 用于 at-least-once worker 的副作用围栏。
                -- 保留历史 artifacts（即使旧版本曾产生重复），只为每个 task 选定一个 canonical artifact。
                CREATE TABLE IF NOT EXISTS artifact_task_claims (
                    task_id TEXT PRIMARY KEY,
                    artifact_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES agent_tasks(task_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_tasks_owner
                    ON agent_tasks(principal_id, status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_agent_tasks_status_lease
                    ON agent_tasks(status, lease_expires_at);
                CREATE INDEX IF NOT EXISTS idx_artifacts_owner
                    ON artifacts(owner_principal_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_artifacts_task
                    ON artifacts(task_id);
                -- ── schema v12（M5-A：用户显式保存的私有证据存档，独立于任务 artifact 生命周期） ──
                CREATE TABLE IF NOT EXISTS saved_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    owner_principal_id TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'chat_evidence_snapshot',
                    title TEXT NOT NULL,
                    content_json TEXT NOT NULL DEFAULT '{}',
                    visibility TEXT NOT NULL DEFAULT 'private',
                    request_id TEXT,
                    conversation_id TEXT,
                    evidence_snapshot_json TEXT NOT NULL DEFAULT '[]',
                    citations_json TEXT NOT NULL DEFAULT '[]',
                    checksum TEXT NOT NULL,
                    idempotency_key TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(owner_principal_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS idx_saved_artifacts_owner
                    ON saved_artifacts(owner_principal_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_saved_artifacts_request
                    ON saved_artifacts(request_id);
                -- ── schema v14（additive；clarification_requests 表保留，
                --      当前无业务消费方；不得降低 schema 版本或 DROP 此表） ──
                CREATE TABLE IF NOT EXISTS clarification_requests (
                    clarification_id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    conversation_id TEXT,
                    original_request_hash TEXT NOT NULL,
                    questions_json TEXT NOT NULL DEFAULT '[]',
                    context_hash TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_clarification_requests_owner
                    ON clarification_requests(principal_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_clarification_requests_consume
                    ON clarification_requests(clarification_id, consumed_at);
            """)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_notes_policy_lifecycle "
                "ON notes(policy_key, policy_status, effective_from, effective_to)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_notes_workspace ON notes(workspace)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_notes_source ON notes(source_id, source_path)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_notes_department ON notes(department)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_notes_acl_public ON notes(acl_public)"
            )
            row = connection.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
            connection.execute(
                "INSERT OR IGNORE INTO artifact_task_claims (task_id, artifact_id, created_at) "
                "SELECT task_id, MIN(artifact_id), MIN(created_at) FROM artifacts GROUP BY task_id"
            )
            if row is None:
                connection.execute("INSERT INTO schema_meta(version) VALUES (?)", (SCHEMA_VERSION,))
            elif row[0] < SCHEMA_VERSION:
                connection.execute("UPDATE schema_meta SET version=?", (SCHEMA_VERSION,))

    @staticmethod
    def _ensure_columns(connection: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
        # PRAGMA 不支持参数化查询 —— 但 table 名来自我们自己的代码，非用户输入
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

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        """执行写语句并返回受影响行数（并发 claim/幂等判定依赖该返回值）。

        写锁竞争通用缓解（运行时缺陷修复）：WAL 单写者模型下，请求线程与
        worker 线程的写写在 busy_timeout 内可能解不开（database is locked
        直接打穿到请求 500）。此处对 locked 做短指数退避重试（与既有
        _cursor_with_retry 的连接级重试互补，这是语句级）。
        """
        started = time.perf_counter()
        conn = self._cursor_with_retry()
        last_error: sqlite3.OperationalError | None = None
        for attempt in range(self._MAX_RETRIES):
            try:
                cursor = conn.execute(sql, params)
                conn.commit()
                rowcount = cursor.rowcount if cursor is not None else 0
                self._log_slow_query(sql, (time.perf_counter() - started) * 1000)
                return rowcount if rowcount is not None and rowcount >= 0 else 0
            except sqlite3.OperationalError as exc:
                conn.rollback()
                if "locked" in str(exc).lower() and attempt < self._MAX_RETRIES - 1:
                    last_error = exc
                    time.sleep(self._RETRY_DELAY * (attempt + 1))
                    continue
                raise
        raise last_error  # type: ignore[misc]  # 理论不可达：循环内必 return 或 raise

    def execute_many(self, sql: str, params_list: list[tuple[Any, ...]]) -> None:
        started = time.perf_counter()
        conn = self._cursor_with_retry()
        try:
            conn.executemany(sql, params_list)
            conn.commit()
        except sqlite3.OperationalError:
            conn.rollback()
            raise
        self._log_slow_query(sql, (time.perf_counter() - started) * 1000)

    def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        started = time.perf_counter()
        conn = self._cursor_with_retry()
        row = conn.execute(sql, params).fetchone()
        self._log_slow_query(sql, (time.perf_counter() - started) * 1000)
        return dict(row) if row else None

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        started = time.perf_counter()
        conn = self._cursor_with_retry()
        rows = conn.execute(sql, params).fetchall()
        self._log_slow_query(sql, (time.perf_counter() - started) * 1000)
        return [dict(row) for row in rows]


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(value: str | None, default: Any) -> Any:
    return json.loads(value) if value else default
