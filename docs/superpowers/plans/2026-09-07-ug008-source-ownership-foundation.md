# UG-008 Source Ownership Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish auditable, fail-closed ownership for every directory-originated note before allowing a connector to write or prune it.

**Architecture:** Advance SQLite additively from v15 to v16, preserving `notes.source_id`, `source_path`, `acl_json`, and `acl_public`. A focused application service validates canonical roots and writes redacted dry-run findings; the directory connector uses a two-step audit-then-sync gate, while task-worker directory scans are fenced until they have an equivalent ownership integration.

**Tech Stack:** Python 3.11+, FastAPI/Pydantic, SQLite WAL, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-07-ug008-source-ownership-foundation-design.md`

## Global Constraints

- Advance schema v15 to v16 only additively; do not drop, rename, rewrite, or loosen an existing table or column.
- Use synthetic directories and `tmp_path` SQLite databases only. Do not test a business database or a real Vault.
- Findings never auto-reassign, delete, publish, or replace a note ACL.
- Sync/prune require a registered, active source and its latest clean audit. Directory endpoint default is dry-run.
- Audit records contain identifiers, reason codes, and redacted counts only; never bodies, credentials, connection strings, or full ACL payloads.
- Directory-root agent tasks are a bypass until safely integrated; they must fail closed with `source_registration_required`.
- Each task uses RED → GREEN, its named `--no-cov` test, the relevant lint gate, and an independent commit.

---

## Planned file structure

| File | Responsibility |
| --- | --- |
| `src/infrastructure/database.py` | Add v16 DDL and indexes. |
| `src/domain/source_ownership.py` | Immutable source/audit values and reason codes. |
| `src/application/source_ownership_service.py` | Registration, audit, and authorization. |
| `src/application/directory_connector_service.py` | Audit-before-sync enforcement. |
| `src/application/task_worker.py` | Close the unregistered directory-write bypass. |
| `src/api/dependencies.py` / `src/api/routes/connectors.py` | Service injection and safe admin endpoint contract. |
| `tests/test_schema_compat.py` | Additive v15→v16 database compatibility. |
| `tests/test_source_ownership.py` | Registry/audit/no-mutation coverage. |
| `tests/test_directory_connector.py` | Connector gate coverage. |
| `tests/test_agent_tasks.py` | Task-worker no-write regression. |
| `tests/test_auth_boundaries.py` | Admin boundary and dry-run default. |

### Task 1: Add the additive v16 persistence contract

**Files:**
- Create: `src/domain/source_ownership.py`
- Modify: `src/infrastructure/database.py`
- Modify: `tests/test_schema_compat.py`

**Interfaces:**
- Consumes: `ProductDatabase.initialize()`, `.execute()`, `.fetch_one()`, and `.fetch_all()`.
- Produces: `RegisteredSource`, `OwnershipFinding`, `OwnershipAuditResult`, and v16 tables for Tasks 2–4.

- [ ] **Step 1: Write the failing schema test**

```python
V16_ONLY_TABLES = ("knowledge_sources", "source_ownership_audit_runs", "source_ownership_findings")

def test_v15_upgrades_to_v16_additively_preserving_notes(tmp_path: Path):
    database = ProductDatabase(tmp_path / "v15-to-v16.sqlite3")
    database.initialize()
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
    assert _stored_version(database) == SCHEMA_VERSION == 16
    assert set(V16_ONLY_TABLES) <= _table_names(database)
    assert database.fetch_one("SELECT source_id FROM notes WHERE note_id='legacy-note'")["source_id"] == "legacy-source"
```

- [ ] **Step 2: Run RED test**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_schema_compat.py -k "v16 or v15_upgrades" --no-cov -q`  
Expected: FAIL because v16 tables and version do not exist.

- [ ] **Step 3: Add domain values and DDL**

Create `src/domain/source_ownership.py` without path I/O or SQL:

```python
from dataclasses import dataclass
from typing import Literal

SOURCE_STATUS = Literal["active", "disabled"]
AUDIT_STATUS = Literal["clean", "needs_review"]

class SourceOwnershipError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code

@dataclass(frozen=True, slots=True)
class RegisteredSource:
    source_id: str
    connector_id: str
    connector_type: str
    root_locator: str
    status: SOURCE_STATUS

@dataclass(frozen=True, slots=True)
class OwnershipFinding:
    reason_code: str
    note_id: str | None
    source_id: str | None
    source_path: str | None

@dataclass(frozen=True, slots=True)
class OwnershipAuditResult:
    audit_run_id: str
    status: AUDIT_STATUS
    source_id: str | None
    finding_count: int
```

Set `SCHEMA_VERSION = 16`; inside the existing additive initialization transaction create exactly:

```sql
CREATE TABLE IF NOT EXISTS knowledge_sources (
 source_id TEXT PRIMARY KEY, connector_id TEXT NOT NULL UNIQUE, connector_type TEXT NOT NULL,
 root_locator TEXT NOT NULL UNIQUE, status TEXT NOT NULL CHECK (status IN ('active','disabled')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_audited_at TEXT
);
CREATE TABLE IF NOT EXISTS source_ownership_audit_runs (
 audit_run_id TEXT PRIMARY KEY, source_id TEXT, mode TEXT NOT NULL CHECK (mode='dry_run'),
 status TEXT NOT NULL CHECK (status IN ('clean','needs_review')), summary_json TEXT NOT NULL,
 started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
 FOREIGN KEY(source_id) REFERENCES knowledge_sources(source_id)
);
CREATE TABLE IF NOT EXISTS source_ownership_findings (
 finding_id TEXT PRIMARY KEY, audit_run_id TEXT NOT NULL, reason_code TEXT NOT NULL,
 note_id TEXT, source_id TEXT, source_path TEXT, metadata_json TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL, FOREIGN KEY(audit_run_id) REFERENCES source_ownership_audit_runs(audit_run_id)
);
CREATE INDEX IF NOT EXISTS idx_source_ownership_runs_source ON source_ownership_audit_runs(source_id, finished_at);
CREATE INDEX IF NOT EXISTS idx_source_ownership_findings_run ON source_ownership_findings(audit_run_id, reason_code);
```

- [ ] **Step 4: Run GREEN test**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_schema_compat.py --no-cov -q`  
Expected: PASS; the v15-shaped database keeps its legacy note and gains only v16 tables.

- [ ] **Step 5: Commit**

```powershell
git add src/domain/source_ownership.py src/infrastructure/database.py tests/test_schema_compat.py; git commit -m "feat: add source ownership schema"
```

### Task 2: Implement registration, redacted dry-run audit, and authorization

**Files:**
- Create: `src/application/source_ownership_service.py`
- Create: `tests/test_source_ownership.py`

**Interfaces:**
- Consumes: Task 1 values and tables, `ProductDatabase.transaction()`.
- Produces: `SourceOwnershipService.register_directory_source()`, `.source_for_connector()`, `.dry_run_audit()`, and `.require_sync_authorized()` for Task 3.

- [ ] **Step 1: Write failing isolated-DB tests**

```python
def test_registration_rejects_nested_root_overlap(tmp_path: Path):
    database = ProductDatabase(tmp_path / "ownership.sqlite3")
    database.initialize()
    root = tmp_path / "sources"
    child = root / "finance"
    child.mkdir(parents=True)
    service = SourceOwnershipService(database)
    service.register_directory_source("connector-root", root)
    with pytest.raises(SourceOwnershipError, match="source_root_overlap"):
        service.register_directory_source("connector-child", child)

def test_dry_run_records_invalid_acl_without_mutating_note(tmp_path: Path):
    database = ProductDatabase(tmp_path / "audit.sqlite3")
    database.initialize()
    root = tmp_path / "source"
    root.mkdir()
    service = SourceOwnershipService(database)
    source = service.register_directory_source("connector-a", root)
    database.execute(
        "INSERT INTO notes (note_id,vault_path,title,content_hash,frontmatter_json,ai_access_level,source_id,source_path,acl_json,acl_public,created_at,updated_at) "
        "VALUES ('n1','connector-a/a.md','A','h','{}','local_only',?,'a.md','{not-json',0,'t','t')",
        (source.source_id,),
    )
    result = service.dry_run_audit(source.source_id)
    assert result.status == "needs_review"
    assert database.fetch_one("SELECT acl_json FROM notes WHERE note_id='n1'")["acl_json"] == "{not-json"
    assert database.fetch_one("SELECT reason_code FROM source_ownership_findings WHERE audit_run_id=?", (result.audit_run_id,))["reason_code"] == "invalid_acl_json"
```

Add separate tests for missing `source_id`, unknown `source_id`, a note path outside its root, connector mismatch, disabled sources, a clean audit, and ledger redaction. Assert ledger rows do not contain `frontmatter_json` or `acl_json`.

- [ ] **Step 2: Run RED test**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_source_ownership.py --no-cov -q`  
Expected: FAIL because `SourceOwnershipService` does not exist.

- [ ] **Step 3: Implement the exact service contract**

```python
class SourceOwnershipService:
    def __init__(self, database: ProductDatabase) -> None: ...
    def register_directory_source(self, connector_id: str, source_path: Path) -> RegisteredSource: ...
    def source_for_connector(self, connector_id: str) -> RegisteredSource | None: ...
    def dry_run_audit(self, source_id: str | None = None) -> OwnershipAuditResult: ...
    def require_sync_authorized(self, connector_id: str, source_path: Path) -> RegisteredSource: ...
```

`register_directory_source()` must resolve an existing directory with `Path.resolve(strict=True)`, store `str(path).casefold()` as `root_locator`, and use the stable caller-provided connector ID as `source_id`. Re-registering that exact connector/root is idempotent; a changed root, duplicated root, parent root, or child root raises `SourceOwnershipError("source_root_overlap")`.

`dry_run_audit()` reads `notes` and registered sources and creates findings for `missing_source_id`, `unknown_source_id`, `source_path_outside_root`, and `invalid_acl_json`. A valid directory-originated `source_path` must use the exact logical form `<source_id>/<relative-posix-path>` with no empty, `.` or `..` components; it is never treated as a host filesystem path. Validate `acl_json` only as a JSON object: do not add source ACL policy in this phase. Persist one run plus its findings atomically with `ProductDatabase.transaction()`, update `last_audited_at`, and return `clean` only with zero findings.

`require_sync_authorized()` compares connector ID, active status, and canonical root then requires the most recent run for that source to be `clean` and no older than that source's `updated_at`; missing, stale, or non-clean audit raises `SourceOwnershipError("audit_required")`.

- [ ] **Step 4: Run GREEN and lint**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_source_ownership.py --no-cov -q
.\.venv\Scripts\python.exe -m ruff check src tests --select F821,F822,F823,E902
```

Expected: both commands PASS; every invalid/ambiguous condition leaves its `notes` ownership and ACL fields unchanged.

- [ ] **Step 5: Commit**

```powershell
git add src/application/source_ownership_service.py tests/test_source_ownership.py; git commit -m "feat: audit source ownership"
```

### Task 3: Gate directory connector writes behind a clean ownership audit

**Files:**
- Modify: `src/application/directory_connector_service.py`
- Modify: `src/api/dependencies.py`
- Modify: `src/api/routes/connectors.py`
- Modify: `tests/test_directory_connector.py`
- Modify: `tests/test_auth_boundaries.py`

**Interfaces:**
- Consumes: Task 2 registration, audit, and authorization methods.
- Produces: a `dry_run` result with zero note mutations, and an explicit `dry_run=False` result that is authorized before upsert, prune, ACL application, or indexing.

- [ ] **Step 1: Write failing connector tests for the two-call protocol**

```python
def test_directory_connector_defaults_to_dry_run_and_writes_no_notes(tmp_path: Path):
    database = ProductDatabase(tmp_path / "connector.sqlite3")
    database.initialize()
    source = _make_single_note_source(tmp_path, "source", "note-a")
    ownership = SourceOwnershipService(database)
    service = DirectoryConnectorService(database, tmp_path, ownership_service=ownership)
    result = service.sync(source, connector_id="connector-a")
    assert result["status"] == "dry_run"
    assert result["audit_status"] == "clean"
    assert database.fetch_one("SELECT COUNT(*) AS count FROM notes")["count"] == 0

def test_directory_connector_needs_clean_audit_before_real_sync(tmp_path: Path):
    database = ProductDatabase(tmp_path / "connector.sqlite3")
    database.initialize()
    source = _make_single_note_source(tmp_path, "source", "note-a")
    ownership = SourceOwnershipService(database)
    service = DirectoryConnectorService(database, tmp_path, ownership_service=ownership)
    service.sync(source, connector_id="connector-a", dry_run=True)
    result = service.sync(source, connector_id="connector-a", dry_run=False)
    assert result["status"] == "completed"
    assert database.fetch_one("SELECT source_id FROM notes WHERE note_id='note-a'")["source_id"] == "connector-a"
```

Also assert overlapping sources, an audit with findings, disabled sources, and connector mismatch cannot create, update, prune, ACL-backfill, or index notes. Add an API test where an authenticated admin omitting `dry_run` gets `status == "dry_run"`; preserve the existing unauthenticated rejection.

- [ ] **Step 2: Run RED tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_directory_connector.py tests\test_auth_boundaries.py --no-cov -q`  
Expected: FAIL because constructor injection and the safe `dry_run` contract do not exist.

- [ ] **Step 3: Implement source-first connector flow**

Add a required `ownership_service: SourceOwnershipService` constructor argument and `dry_run: bool = True` to `DirectoryConnectorService.sync()`. Preserve its current deterministic connector-ID calculation and run this sequence before the existing sync body:

```python
registration = self.ownership_service.register_directory_source(connector_id, source)
if dry_run:
    audit = self.ownership_service.dry_run_audit(registration.source_id)
    return {
        "connector_id": connector_id,
        "source_id": registration.source_id,
        "status": "dry_run",
        "audit_run_id": audit.audit_run_id,
        "audit_status": audit.status,
        "finding_count": audit.finding_count,
    }
self.ownership_service.require_sync_authorized(connector_id, source)
```

Only after authorization may existing `connector_syncs` updates, `VaultSyncService.scan_vault()`, `_apply_connector_acl()`, source-scoped pruning, and optional index builds execute. The existing `VaultSyncService` source-scoped prune remains a second defense.

In `ServiceContainer._init_mindgraph()`, construct `SourceOwnershipService(self.database)` once and inject it into `DirectoryConnectorService`. Add `dry_run: bool = Field(True, description="仅登记来源并执行归属审计，不写入笔记")` to `SyncDirectoryRequest`; pass it to the service. Convert `SourceOwnershipError` to HTTP 400 with its stable code. Access-audit metadata must use connector ID, dry-run, audit status, and finding count only—never source path or raw ACL.

- [ ] **Step 4: Run GREEN and commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_directory_connector.py tests\test_auth_boundaries.py --no-cov -q`  
Expected: PASS; default endpoint calls mutate zero notes, and only an explicit second call after a clean audit can sync/prune that source.

```powershell
git add src/application/directory_connector_service.py src/api/dependencies.py src/api/routes/connectors.py tests/test_directory_connector.py tests/test_auth_boundaries.py; git commit -m "feat: gate connector sync by source audit"
```

### Task 4: Close the task-worker directory-write bypass

**Files:**
- Modify: `src/application/task_worker.py`
- Modify: `tests/test_agent_tasks.py`

**Interfaces:**
- Consumes: the current `directory_delta_sync` constraint and TaskWorker `_fail()` state transition.
- Produces: `source_registration_required` before `VaultSyncService` is constructed, with no `notes` or task-originated `connector_syncs` mutation.

- [ ] **Step 1: Write the failing bypass regression**

Replace the current success expectation for a `directory_root` task with this explicit no-write test:

```python
def test_directory_delta_task_refuses_unregistered_directory_write_path(tmp_path: Path):
    service, worker, database = _build(tmp_path)
    allowed_root = tmp_path / "roots"
    allowed_root.mkdir()
    source = _write_source_vault(allowed_root)
    task = service.submit(
        principal_id="u1", idempotency_key="delta-dir-registration-gate",
        task_type="directory_delta_sync",
        constraints={"since": "2026-09-01T00:00:00", "directory_root": str(source)},
        directory_scan_authorized=True,
    )
    result = worker.run_once()
    assert result is not None and result["status"] == "failed"
    assert result["error_code"] == "source_registration_required"
    assert database.fetch_one("SELECT COUNT(*) AS count FROM notes")["count"] == 0
    assert database.fetch_one("SELECT COUNT(*) AS count FROM connector_syncs WHERE connector_type='agent_task_delta_sync'")["count"] == 0
```

- [ ] **Step 2: Run RED test**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_agent_tasks.py -k "directory_root" --no-cov -q`  
Expected: FAIL because the current worker invokes `VaultSyncService` and writes notes.

- [ ] **Step 3: Fence before scan**

In `TaskWorker._execute_delta_sync()`, once a `directory_root` is supplied, return `_fail(task_id, code="source_registration_required", message="directory_root requires a registered connector source")` before calling `_scan_directory_rows()`. Keep snapshot-mode `directory_delta_sync` unchanged because it only reads already-indexed notes under ACL filtering.

Do not auto-register a task-specific source: the task principal is not a connector owner, and that would recreate the bypass. Remove/replace the task-originated successful scan test; a future phase may design a read-only scanner or explicit admin handoff to a registered connector.

- [ ] **Step 4: Run GREEN and commit**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_agent_tasks.py -k "directory_root or delta_sync" --no-cov -q`  
Expected: PASS; directory-root tasks fail before creating a note or connector audit record, snapshot tasks retain ACL-filtered behavior.

```powershell
git add src/application/task_worker.py tests/test_agent_tasks.py; git commit -m "fix: fence task directory sync writes"
```

### Task 5: Document the operating contract and run acceptance

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/DEPLOYMENT.md`
- Modify: `tests/test_source_ownership.py`

**Interfaces:**
- Consumes: v16 registry, default dry-run endpoint, explicit promotion to `dry_run=false`, and Task 4 rejection.
- Produces: current-state operator guidance; it must not claim deferred governance policy, retrieval/Chat enforcement, cases, reconciliation, rollback, or Web review UI.

- [ ] **Step 1: Write the failing documentation contract test**

```python
def test_operator_docs_state_source_ownership_safety_contract():
    for relative_path in ("README.md", "README.zh-CN.md", "docs/DEPLOYMENT.md"):
        text = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
        assert "schema v16" in text
        assert "dry-run" in text
        assert "clean audit" in text
        assert "directory-root task" in text
```

- [ ] **Step 2: Run RED test**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_source_ownership.py -k "operator_docs" --no-cov -q`  
Expected: FAIL because documentation does not state the v16 source-ownership contract.

- [ ] **Step 3: Update factual operator documentation**

Add a concise v16 section in all three documents. It must say: the directory endpoint defaults to dry-run and only registers/validates the canonical source plus records an audit; it does not create, update, prune, index, or ACL-backfill notes. `dry_run=false` requires a clean audit for the same connector/source. Unknown ownership, overlap, disabled sources, and malformed ACL are fail-closed findings. Directory-root agent tasks are not an import path in this release. Translate those facts accurately in Chinese; do not claim later-phase capability.

- [ ] **Step 4: Run the complete first-phase acceptance gate**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_schema_compat.py tests\test_source_ownership.py tests\test_directory_connector.py tests\test_agent_tasks.py tests\test_auth_boundaries.py --no-cov -q
.\.venv\Scripts\python.exe -m ruff check src scripts tests --select F821,F822,F823,E902
.\.venv\Scripts\python.exe scripts\validate_mindgraph_offline.py
git diff --check
```

Expected: every executed command exits 0. If offline validation lacks a prerequisite, report it as an unexecuted release gate; do not declare the phase ready.

- [ ] **Step 5: Commit**

```powershell
git add README.md README.zh-CN.md docs/DEPLOYMENT.md tests/test_source_ownership.py; git commit -m "docs: describe source ownership operation"
```

## Final acceptance evidence

Before opening a PR, capture each command, selection, exit code, and skipped count. Confirm a temporary v15-shaped SQLite database upgrades to v16 without data loss; every audit finding leaves the corresponding note ownership/ACL fields unchanged; a clean audit precedes the first real connector mutation; cross-source pruning is impossible; and a directory-root task cannot write a note. Do not claim retrieval, Chat, governance case, reconciliation, or administrator review UI coverage: they are deliberately deferred phases.
