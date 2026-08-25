# UG-008 Knowledge Governance and Lifecycle Filtering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make declared policy status, effective dates, confirmed governance decisions, conflicts, duplicates, ACLs, indexing, retrieval, chat refusal, audit, API, and Web review use one fail-closed governance contract.

**Architecture:** A pure `GovernancePolicy` calculates lifecycle and disposition for an explicit `as_of` date. Schema 9 persists cases, participants, a disposable projection, and immutable events; database-aware services reconcile state and execute human decisions, while indexing and retrieval always recalculate eligibility instead of trusting the projection. Existing schema-8 databases require an explicit dry-run/apply/rollback CLI, and ACL filtering remains ahead of governance filtering.

**Tech Stack:** Python 3.12, dataclasses/enums, FastAPI, Pydantic, SQLite/WAL, FAISS, BM25, React 19, TypeScript 7, Vitest, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-25-ug008-knowledge-governance-design.md`

## Global Constraints

- Do not push, create a PR, merge, delete a worktree, deploy, or publish.
- Do not edit `.env`, secrets, tokens, CI configuration, lint rules, coverage thresholds, or dependency versions.
- Do not apply or roll back schema 9 against a real database. Migration tests use temporary databases only.
- Do not implement OCR, multi-query retrieval, MCP/OIDC changes, UG-007 expansion, source-file editing, `notes`/`document_versions` consolidation, service deletion, a new vector database, semantic auto-resolution, or batch governance operations in UG-008.
- Obtain explicit user authorization immediately before Task 2 because it changes the database schema and adds a migration.
- Every behavior change starts with a failing test, followed by the smallest implementation that passes it.
- Every task ends with focused tests, Ruff on touched Python files, full `pytest`, an independent review, and an English commit.
- Full-repository Ruff debt is reported separately. Do not add ignores, `noqa`, rule reductions, or bypasses.
- `governance_events` must never contain note bodies, titles, relative or absolute paths, ACL payloads, credentials, tokens, or connector secrets.
- ACL prefiltering happens before governance prefiltering, dense search, BM25, fusion, reranking, graph expansion, and model context construction.
- Missing/corrupt governance data or unavailable complete corpus metadata fails closed; it never falls back to ungoverned retrieval.
- `include_historical=True` without an explicit `query_date` is invalid.
- Semantic similarity may propose a case but may not automatically exclude evidence or select a canonical note.

---

## File and Interface Map

### New Python files

- `src/domain/governance.py`: immutable enums and value objects only; no FastAPI, SQLite, or retrieval imports.
- `src/application/governance_policy.py`: pure lifecycle/disposition evaluation and exact-duplicate equivalence.
- `src/application/governance_schema_migration_service.py`: read-only plan, transactional apply, exact-run rollback, and aggregate reports.
- `src/application/governance_reconciliation_service.py`: database reconciliation, deterministic case discovery, idempotent events, and eligibility snapshots.
- `src/application/governance_case_service.py`: ACL-filtered case/event reads and atomic resolve/reject/revoke operations.
- `src/api/routes/knowledge_governance.py`: Pydantic contracts and HTTP mapping only.
- `scripts/migrate_governance_schema.py`: JSON-only operations wrapper around the migration service.

### New tests

- `tests/test_governance_policy.py`
- `tests/test_governance_schema_migration.py`
- `tests/test_governance_reconciliation.py`
- `tests/test_governance_case_service.py`
- `tests/test_governance_indexing.py`
- `tests/test_governance_retrieval.py`
- `tests/test_knowledge_governance_api.py`
- `web/src/lib/knowledge-governance.test.ts`

### Existing files changed by later tasks

- `src/infrastructure/database.py`: distinguish brand-new schema-9 initialization from existing schema-8 startup.
- `src/application/vault_sync_service.py`: strict ISO-date validation and post-commit reconciliation hook.
- `src/application/mindgraph_index_service.py`: governance gate and canonical duplicate indexing.
- `src/application/mindgraph_sync_watcher.py`: reconcile before build and stop activation on reconciliation failure.
- `src/retrieval/types.py`, `src/retrieval/pipeline.py`, `src/retrieval/mindgraph_pipeline.py`: governance prefilter/trace and graph defense.
- `src/infrastructure/retrieval_factory.py`: inject the governance policy and confirmed-decision loader.
- `src/application/policy_conflict_service.py`: compatibility facade over unified deterministic conflict evaluation.
- `src/application/chat_service.py`, `src/domain/models.py`: governed refusal and historical request validation.
- `src/api/auth.py`, `src/api/dependencies.py`, `src/api/main.py`, `src/api/routes/health.py`, `src/api/routes/mindgraph_readonly.py`: composition, role boundary, health, and display.
- `web/src/types.ts`, `web/src/lib/api.ts`, `web/src/components/PolicyGovernance.tsx`, `web/src/pages/KnowledgePage.tsx`, `web/src/styles.css`: progressive governance queue.
- `README.md`, `docs/UPGRADE_PLAN.md`, `docs/PRODUCT_STRATEGY.md`: operator workflow and accurate roadmap status.

---

### Task 1: Pure Governance Domain and Policy

**Files:**
- Create: `src/domain/governance.py`
- Create: `src/application/governance_policy.py`
- Create: `tests/test_governance_policy.py`
- Modify: `src/domain/errors.py`
- Modify: `src/application/vault_sync_service.py:114-142`
- Modify: `tests/test_policy_metadata.py`

**Interfaces:**
- Produces: `GovernancePolicy.evaluate(note: GovernanceNote, *, as_of: date, mode: GovernanceMode, confirmed_decisions: Sequence[ConfirmedGovernanceDecision] = ()) -> GovernanceEvaluation`.
- Produces: `GovernancePolicy.exact_duplicate_equivalent(left: GovernanceNote, right: GovernanceNote) -> bool`.
- Produces: strict `normalize_policy_metadata(fm: Mapping[str, Any]) -> NormalizedPolicyMetadata` used by Vault sync.
- Produces: `GovernanceUnavailableError` with HTTP status 503 and `GovernanceConflictError` with HTTP status 409 in `domain.errors`.
- Consumes: no database state and no wall-clock time; callers provide `as_of` explicitly.

- [ ] **Step 1: Add failing state, date, historical, decision, and duplicate tests**

```python
from datetime import date

import pytest

from application.governance_policy import GovernancePolicy
from domain.governance import (
    ConfirmedGovernanceDecision,
    GovernanceDisposition,
    GovernanceMode,
    GovernanceNote,
    LifecycleState,
)


def note(**overrides) -> GovernanceNote:
    values = {
        "note_id": "policy-v1",
        "source_id": "builtin",
        "owner": "Finance",
        "policy_key": "expense-policy",
        "document_version": "1.0",
        "effective_from": "2026-01-01",
        "effective_to": None,
        "policy_status": "active",
        "metadata_issues": (),
        "workspace": "corp",
        "department": "finance",
        "acl_json": '{"allow":["workspace:corp"]}',
        "acl_public": False,
        "content_hash": "sha256:a",
    }
    values.update(overrides)
    return GovernanceNote(**values)


@pytest.mark.parametrize(
    ("as_of", "state", "disposition", "reason"),
    [
        (date(2025, 12, 31), LifecycleState.NOT_YET_EFFECTIVE, GovernanceDisposition.EXCLUDED, "not_yet_effective"),
        (date(2026, 1, 1), LifecycleState.CURRENT, GovernanceDisposition.ELIGIBLE, "eligible_current_version"),
        (date(2026, 12, 31), LifecycleState.CURRENT, GovernanceDisposition.ELIGIBLE, "eligible_current_version"),
        (date(2027, 1, 1), LifecycleState.EXPIRED, GovernanceDisposition.EXCLUDED, "effective_period_ended"),
    ],
)
def test_current_mode_date_boundaries(as_of, state, disposition, reason):
    candidate = note(effective_to="2026-12-31")
    result = GovernancePolicy().evaluate(candidate, as_of=as_of, mode=GovernanceMode.CURRENT)
    assert result.lifecycle_state is state
    assert result.disposition is disposition
    assert reason in result.reason_codes


def test_invalid_date_is_unresolved_not_an_exception():
    result = GovernancePolicy().evaluate(
        note(effective_from="2026-13-01"),
        as_of=date(2026, 8, 25),
        mode=GovernanceMode.CURRENT,
    )
    assert result.disposition is GovernanceDisposition.UNRESOLVED
    assert result.reason_codes == ("invalid_effective_date",)


def test_confirmed_duplicate_alias_is_never_eligible():
    decision = ConfirmedGovernanceDecision(
        note_id="policy-v1",
        disposition=GovernanceDisposition.DUPLICATE_ALIAS,
        reason_code="confirmed_duplicate_alias",
        canonical_note_id="policy-canonical",
    )
    result = GovernancePolicy().evaluate(
        note(),
        as_of=date(2026, 8, 25),
        mode=GovernanceMode.CURRENT,
        confirmed_decisions=(decision,),
    )
    assert not result.eligible
    assert result.canonical_note_id == "policy-canonical"


def test_historical_mode_accepts_proven_superseded_interval():
    result = GovernancePolicy().evaluate(
        note(policy_status="superseded", effective_to="2026-06-30"),
        as_of=date(2026, 4, 1),
        mode=GovernanceMode.HISTORICAL,
    )
    assert result.eligible
    assert result.reason_codes == ("eligible_historical_version",)


def test_exact_duplicate_requires_security_and_source_equivalence():
    policy = GovernancePolicy()
    assert policy.exact_duplicate_equivalent(note(note_id="a"), note(note_id="b"))
    assert not policy.exact_duplicate_equivalent(
        note(note_id="a"),
        note(note_id="b", acl_json='{ "allow": ["workspace:other"] }'),
    )
    assert not policy.exact_duplicate_equivalent(note(note_id="a"), note(note_id="b", source_id="connector-b"))
```

Also parameterize `draft`, `archived`, `superseded`, compatibility `expired`, `unspecified`, missing required fields, reversed ranges, malformed `metadata_issues_json`, ACL JSON normalization, and historical terminal status without a provable interval.

- [ ] **Step 2: Run the new tests and verify the missing-module failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_governance_policy.py tests/test_policy_metadata.py -q --no-cov
```

Expected: collection fails because `domain.governance` and `application.governance_policy` do not exist.

- [ ] **Step 3: Implement immutable domain types**

```python
class GovernanceMode(str, Enum):
    CURRENT = "current"
    HISTORICAL = "historical"


class LifecycleState(str, Enum):
    NOT_YET_EFFECTIVE = "not_yet_effective"
    CURRENT = "current"
    EXPIRED = "expired"
    HISTORICAL = "historical"
    UNRESOLVED = "unresolved"


class GovernanceDisposition(str, Enum):
    ELIGIBLE = "eligible"
    EXCLUDED = "excluded"
    UNRESOLVED = "unresolved"
    CONFLICT_BLOCKED = "conflict_blocked"
    DUPLICATE_ALIAS = "duplicate_alias"


@dataclass(frozen=True, slots=True)
class GovernanceNote:
    note_id: str
    source_id: str
    owner: str | None
    policy_key: str | None
    document_version: str | None
    effective_from: str | None
    effective_to: str | None
    policy_status: str
    metadata_issues: tuple[str, ...]
    workspace: str | None
    department: str | None
    acl_json: str
    acl_public: bool
    content_hash: str


@dataclass(frozen=True, slots=True)
class NormalizedPolicyMetadata:
    owner: str | None
    policy_key: str | None
    document_version: str | None
    effective_from: str | None
    effective_to: str | None
    policy_status: str
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConfirmedGovernanceDecision:
    note_id: str
    disposition: GovernanceDisposition
    reason_code: str
    canonical_note_id: str | None = None


@dataclass(frozen=True, slots=True)
class GovernanceEvaluation:
    note_id: str
    lifecycle_state: LifecycleState
    disposition: GovernanceDisposition
    eligible: bool
    reason_codes: tuple[str, ...]
    canonical_note_id: str | None = None
```

- [ ] **Step 4: Implement the pure evaluator and strict metadata normalization**

Parse dates with `date.fromisoformat`, canonicalize ACL JSON with sorted keys and sorted unique allow/deny lists, and return reason codes rather than raising on source metadata errors. Replace the lexicographic range check in `_policy_metadata` with the shared normalizer. Change `_policy_metadata` to return `NormalizedPolicyMetadata`; its named attributes preserve the contract used by `_upsert_note` without importing `vault_sync_service` from the policy module.

```python
class GovernancePolicy:
    REQUIRED_FIELDS = ("owner", "policy_key", "document_version", "effective_from")

    def evaluate(self, note, *, as_of, mode, confirmed_decisions=()):
        decision = next((item for item in confirmed_decisions if item.note_id == note.note_id), None)
        if decision is not None:
            return self._from_confirmed_decision(note.note_id, decision)
        parsed = self._validated_interval(note)
        if parsed.error_reason:
            return self._unresolved(note.note_id, parsed.error_reason)
        return self._evaluate_interval(note, parsed, as_of=as_of, mode=mode)

    def exact_duplicate_equivalent(self, left, right):
        return self._duplicate_fingerprint(left) == self._duplicate_fingerprint(right)
```

Add `GovernanceUnavailableError(ProductError)` with code `governance_unavailable` and status 503, and `GovernanceConflictError(ConflictError)` with code `governance_conflict`.

- [ ] **Step 5: Run focused tests and touched-file Ruff**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_governance_policy.py tests/test_policy_metadata.py -q --no-cov
.\.venv\Scripts\python.exe -m ruff check src/domain/governance.py src/domain/errors.py src/application/governance_policy.py src/application/vault_sync_service.py tests/test_governance_policy.py tests/test_policy_metadata.py
```

Expected: all commands pass.

- [ ] **Step 6: Run full pytest, obtain an independent review, fix findings, and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest
git add src/domain/governance.py src/domain/errors.py src/application/governance_policy.py src/application/vault_sync_service.py tests/test_governance_policy.py tests/test_policy_metadata.py
git commit -m "feat: define knowledge governance policy"
```

Review must specifically check date boundaries, historical semantics, reason-code stability, and that the domain layer imports neither SQLite nor FastAPI.

---

### Task 2: Schema 9 and Explicit Migration CLI

**Files:**
- Create: `src/application/governance_schema_migration_service.py`
- Create: `scripts/migrate_governance_schema.py`
- Create: `tests/test_governance_schema_migration.py`
- Modify: `src/infrastructure/database.py:13-340`
- Modify: `README.md`
- Modify: `docs/UPGRADE_PLAN.md`

**Interfaces:**
- Produces: `GovernanceSchemaMigrationService.plan() -> GovernanceMigrationReport` using a read-only connection.
- Produces: `GovernanceSchemaMigrationService.apply() -> GovernanceMigrationReport` using `BEGIN IMMEDIATE`.
- Produces: `GovernanceSchemaMigrationService.rollback(run_id: str) -> GovernanceMigrationReport` with exact-run and unused-table checks.
- Produces: `GovernanceMigrationReport(mode: str, status: str, current_version: int, target_version: int, object_count: int, run_id: str | None)` as a frozen dataclass in the migration service module.
- Produces: CLI modes `--dry-run` (default), `--apply`, and `--rollback RUN_ID`; stdout is one aggregate JSON object.
- Consumes: schema-8 `ProductDatabase` layout and `Settings.DATABASE_PATH`.

- [ ] **Step 1: Request explicit schema-change authorization**

Before editing any file in this task, present the exact five tables, immutable triggers, version transition `8 -> 9`, and rollback refusal rule to the user. Proceed only after explicit approval. Approval authorizes implementation and temporary-database tests, not migration of `data/product/product.sqlite3` or any production database.

- [ ] **Step 2: Add failing new-database, dry-run, apply, immutability, and rollback tests**

```python
def test_existing_schema_8_initialize_does_not_create_governance_tables(schema8_db):
    ProductDatabase(schema8_db).initialize()
    assert schema_version(schema8_db) == 8
    assert "governance_cases" not in table_names(schema8_db)


def test_new_database_initializes_at_schema_9(tmp_path):
    path = tmp_path / "new.sqlite3"
    ProductDatabase(path).initialize()
    assert schema_version(path) == 9
    assert GOVERNANCE_TABLES <= table_names(path)


def test_dry_run_is_read_only(schema8_db):
    before = schema_fingerprint(schema8_db)
    report = GovernanceSchemaMigrationService(schema8_db).plan()
    assert report.mode == "dry_run"
    assert report.current_version == 8
    assert schema_fingerprint(schema8_db) == before


def test_apply_and_exact_run_rollback(schema8_db):
    service = GovernanceSchemaMigrationService(schema8_db)
    applied = service.apply()
    assert schema_version(schema8_db) == 9
    assert applied.run_id
    rolled_back = service.rollback(applied.run_id)
    assert rolled_back.status == "rolled_back"
    assert schema_version(schema8_db) == 8
    assert table_names(schema8_db) & GOVERNANCE_TABLES == set()
    assert "schema_migration_runs" in table_names(schema8_db)


def test_rollback_refuses_after_governance_event(schema8_db):
    service = GovernanceSchemaMigrationService(schema8_db)
    applied = service.apply()
    insert_safe_governance_event(schema8_db)
    with pytest.raises(GovernanceMigrationError, match="governance tables are in use"):
        service.rollback(applied.run_id)


def test_governance_events_are_append_only(schema9_db):
    insert_safe_governance_event(schema9_db)
    with pytest.raises(sqlite3.IntegrityError):
        execute(schema9_db, "UPDATE governance_events SET action='changed'")
    with pytest.raises(sqlite3.IntegrityError):
        execute(schema9_db, "DELETE FROM governance_events")
```

Add subprocess tests proving CLI errors are redacted JSON, exit non-zero, and never contain database paths, note IDs, content, ACL JSON, or traceback text.

- [ ] **Step 3: Run tests and verify failures against schema 8**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_governance_schema_migration.py -q --no-cov
```

Expected: failures for missing migration service/schema objects while existing schema-8 initialization remains unchanged.

- [ ] **Step 4: Implement reusable governance DDL and safe initialization boundary**

Define `SCHEMA_VERSION = 9`, retain `SOURCE_OWNERSHIP_SCHEMA_VERSION = 8`, and isolate governance DDL in `_create_governance_schema(connection)`. At the start of `initialize`, determine whether `schema_meta` existed before this call. Call governance DDL only for a genuinely new database or a database already at version 9; never call it for an existing version-8 database.

DDL must include:

```sql
CREATE TABLE governance_cases (
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
);
CREATE TABLE governance_case_notes (
  case_id TEXT NOT NULL,
  note_id TEXT NOT NULL,
  participant_role TEXT NOT NULL
    CHECK(participant_role IN ('candidate','canonical','alias','superseded')),
  PRIMARY KEY(case_id, note_id),
  FOREIGN KEY(case_id) REFERENCES governance_cases(case_id),
  FOREIGN KEY(note_id) REFERENCES notes(note_id)
);
CREATE TABLE governance_note_state (
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
);
CREATE TABLE governance_events (
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
);
CREATE TABLE schema_migration_runs (
  run_id TEXT PRIMARY KEY,
  migration_name TEXT NOT NULL,
  previous_version INTEGER NOT NULL,
  target_version INTEGER NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('completed','rolled_back')),
  object_count INTEGER NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT NOT NULL,
  rolled_back_at TEXT
);
CREATE TRIGGER governance_events_no_update
BEFORE UPDATE ON governance_events BEGIN
  SELECT RAISE(ABORT, 'governance_events are append-only');
END;
CREATE TRIGGER governance_events_no_delete
BEFORE DELETE ON governance_events BEGIN
  SELECT RAISE(ABORT, 'governance_events are append-only');
END;
```

Use CHECK constraints for case status, participant role, event source, lifecycle state, and disposition. Add indexes for case status/policy key, participant note ID, event case/note/policy/time, and projection disposition.

- [ ] **Step 5: Implement the migration service and JSON CLI**

Dry-run opens `file:<quoted-path>?mode=ro`, sets `PRAGMA query_only=ON`, validates version 8, and reports aggregate object counts. Apply creates the ledger and governance objects, inserts the completed run, and updates `schema_meta` in one `BEGIN IMMEDIATE` transaction. Rollback verifies the exact completed run and zero rows in all four governance tables, drops their indexes/triggers/tables, updates version to 8, and retains the ledger.

```python
def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = GovernanceSchemaMigrationService(Path(get_settings().DATABASE_PATH))
    try:
        report = service.rollback(args.rollback) if args.rollback else service.apply() if args.apply else service.plan()
    except GovernanceMigrationError as exc:
        print(json.dumps({"ok": False, "error": exc.code}, sort_keys=True))
        return exc.exit_code
    print(json.dumps({"ok": True, **report.to_dict()}, sort_keys=True))
    return 0
```

- [ ] **Step 6: Document operator commands and explicit non-automatic migration**

Document the default dry-run, `--apply`, exact-run rollback, rollback refusal after use, schema-8 service-unavailable state, and the prohibition on applying to real data during development. Do not mark UG-008 done.

- [ ] **Step 7: Validate, independently review, fix findings, and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_governance_schema_migration.py -q --no-cov
.\.venv\Scripts\python.exe -m ruff check src/infrastructure/database.py src/application/governance_schema_migration_service.py scripts/migrate_governance_schema.py tests/test_governance_schema_migration.py
.\.venv\Scripts\python.exe -m pytest
git add src/infrastructure/database.py src/application/governance_schema_migration_service.py scripts/migrate_governance_schema.py tests/test_governance_schema_migration.py README.md docs/UPGRADE_PLAN.md
git commit -m "feat: add governed schema migration"
```

Review must verify dry-run byte-for-byte non-mutation, no startup upgrade from schema 8, atomic apply, exact-run rollback, retained audit ledger, and redacted JSON errors.

---

### Task 3: Reconciliation, Projection, and Deterministic Case Discovery

**Files:**
- Create: `src/application/governance_reconciliation_service.py`
- Create: `tests/test_governance_reconciliation.py`
- Modify: `src/domain/governance.py`
- Modify: `src/application/governance_policy.py`

**Interfaces:**
- Produces: `GovernanceReconciliationService.reconcile(*, note_ids: Collection[str] | None = None, as_of: date | None = None) -> ReconciliationResult`.
- Produces: `GovernanceReconciliationService.reconcile_in_transaction(connection: sqlite3.Connection, *, note_ids: Collection[str], as_of: date) -> ReconciliationResult` for case decisions that already hold `BEGIN IMMEDIATE`.
- Produces: `GovernanceReconciliationService.evaluate_notes(notes: Sequence[Mapping[str, Any]], *, as_of: date, mode: GovernanceMode) -> dict[str, GovernanceEvaluation]`.
- Produces: `GovernanceReconciliationService.confirmed_decisions(note_ids: Collection[str]) -> dict[str, tuple[ConfirmedGovernanceDecision, ...]]`.
- Produces: `ReconciliationResult(evaluated, changed, pending, cases_created, events_appended, evaluated_on)`.
- Consumes: Task 1 policy and Task 2 schema 9.

- [ ] **Step 1: Add failing reconciliation tests**

```python
def test_reconcile_is_idempotent(schema9_database, governed_note):
    service = GovernanceReconciliationService(schema9_database, GovernancePolicy())
    first = service.reconcile(as_of=date(2026, 8, 25))
    second = service.reconcile(as_of=date(2026, 8, 25))
    assert first.changed == 1
    assert first.events_appended == 1
    assert second.changed == 0
    assert second.events_appended == 0


def test_date_rollover_changes_projection_once(schema9_database, expiring_note):
    service = GovernanceReconciliationService(schema9_database, GovernancePolicy())
    service.reconcile(as_of=date(2026, 8, 25))
    changed = service.reconcile(as_of=date(2026, 8, 26))
    repeated = service.reconcile(as_of=date(2026, 8, 26))
    assert changed.pending == 1
    assert changed.events_appended == 1
    assert repeated.events_appended == 0


def test_new_calendar_day_without_state_change_refreshes_only_evaluated_on(schema9_database, governed_note):
    service = GovernanceReconciliationService(schema9_database, GovernancePolicy())
    service.reconcile(as_of=date(2026, 8, 25))
    before_events = event_count_for_note(schema9_database, governed_note.note_id)
    result = service.reconcile(as_of=date(2026, 8, 26))
    assert result.changed == 0
    assert result.pending == 0
    assert result.events_appended == 0
    assert event_count_for_note(schema9_database, governed_note.note_id) == before_events
    assert projection_for(schema9_database, governed_note.note_id)["evaluated_on"] == "2026-08-26"


def test_overlapping_versions_create_one_stable_proposed_case(schema9_database, overlapping_notes):
    service = GovernanceReconciliationService(schema9_database, GovernancePolicy())
    first = service.reconcile(as_of=date(2026, 8, 25))
    second = service.reconcile(as_of=date(2026, 8, 25))
    assert first.cases_created == 1
    assert second.cases_created == 0


def test_equivalent_checksum_notes_auto_confirm_alias(schema9_database, equivalent_notes):
    service = GovernanceReconciliationService(schema9_database, GovernancePolicy())
    result = service.reconcile(as_of=date(2026, 8, 25))
    case = only_case(schema9_database)
    assert result.cases_created == 1
    assert case["status"] == "confirmed"
    assert case["canonical_note_id"] == min(note.note_id for note in equivalent_notes)


def test_same_checksum_with_different_acl_is_only_proposed(schema9_database, acl_divergent_notes):
    GovernanceReconciliationService(schema9_database, GovernancePolicy()).reconcile(as_of=date(2026, 8, 25))
    assert only_case(schema9_database)["status"] == "proposed"


def test_event_payload_does_not_contain_sensitive_fields(schema9_database, governed_note):
    GovernanceReconciliationService(schema9_database, GovernancePolicy()).reconcile(as_of=date(2026, 8, 25))
    serialized = json.dumps(schema9_database.fetch_all("SELECT * FROM governance_events"))
    for forbidden in ("body", "title", "vault_path", "acl_json", "secret", "token"):
        assert forbidden not in serialized.lower()
```

- [ ] **Step 2: Run tests and verify the missing-service failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_governance_reconciliation.py -q --no-cov
```

- [ ] **Step 3: Implement note conversion, decision fingerprints, and projection changes**

`decision_fingerprint` is SHA-256 of canonical JSON containing normalized governance metadata, sorted confirmed-decision IDs/states, and the derived evaluation output. Do not include raw `as_of`: a date that does not cross a lifecycle boundary is not a governance state change. `evaluated_on` records the latest evaluation date; refreshing it alone does not append an event or mark indexing pending.

```python
def _decision_fingerprint(note, evaluation, decision_ids):
    payload = {
        "note_id": note.note_id,
        "metadata": governance_metadata_dict(note),
        "decisions": sorted(decision_ids),
        "evaluation": evaluation.to_dict(),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
```

One `BEGIN IMMEDIATE` transaction updates projections, inserts only safe automatic events, creates idempotent cases, and marks notes `pending` only when index membership changes. Public `reconcile(as_of=None)` resolves `date.today()` exactly once at the service boundary; `reconcile_in_transaction` requires an explicit date and never opens a nested transaction.

- [ ] **Step 4: Implement deterministic conflict and duplicate discovery**

Group governance-complete notes by `policy_key`; compare parsed closed/open intervals without version-string sorting. Use `rule_key = sha256(case_type + sorted participant IDs + normalized relevant metadata)`. A rejected unchanged rule key remains rejected. Different evidence yields a new rule key. Exact duplicates auto-confirm only when `GovernancePolicy.exact_duplicate_equivalent` returns true; semantic candidates are never auto-confirmed or auto-blocking.

- [ ] **Step 5: Validate transaction rollback on event failure**

Add a test that installs a temporary trigger rejecting one event insert, runs reconciliation, and asserts that projection, case, event, and `notes.index_status` changes all rolled back.

- [ ] **Step 6: Run validation, independent review, fixes, and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_governance_policy.py tests/test_governance_reconciliation.py -q --no-cov
.\.venv\Scripts\python.exe -m ruff check src/domain/governance.py src/application/governance_policy.py src/application/governance_reconciliation_service.py tests/test_governance_reconciliation.py
.\.venv\Scripts\python.exe -m pytest
git add src/domain/governance.py src/application/governance_policy.py src/application/governance_reconciliation_service.py tests/test_governance_reconciliation.py
git commit -m "feat: reconcile governed knowledge state"
```

Review must check idempotency, date rollover, rule-key stability, exact-duplicate security equivalence, transaction rollback, and event privacy.

---

### Task 4: Human Governance Decisions and Immutable Audit

**Files:**
- Create: `src/application/governance_case_service.py`
- Create: `tests/test_governance_case_service.py`
- Modify: `src/application/access_control.py`
- Modify: `src/domain/governance.py`

**Interfaces:**
- Produces: `GovernanceCaseService.list_cases(*, access_scope, status=None, limit=200) -> list[GovernanceCaseView]`.
- Produces: `GovernanceCaseService.get_case(case_id, *, access_scope) -> GovernanceCaseView | None` where hidden and absent both return `None`.
- Produces: `GovernanceCaseService.list_events(*, access_scope, case_id=None, limit=200) -> list[GovernanceEventView]` with the same hidden-participant rules.
- Produces: `GovernanceCaseService.resolve(case_id, *, expected_status, decision, canonical_note_id, actor, roles, access_scope, request_id) -> GovernanceCaseView`.
- Produces: `GovernanceCaseService.revoke(case_id, *, expected_status, actor, roles, access_scope, request_id) -> GovernanceCaseView`.
- Consumes: `GovernanceReconciliationService` for post-decision evaluation inside the same connection/transaction.

Define frozen `GovernanceCaseView` and `GovernanceEventView` dataclasses in `domain.governance`; their fields are IDs, enums, reason codes, safe evidence IDs, hashes/scores, capabilities, and timestamps only.

```python
@dataclass(frozen=True, slots=True)
class GovernanceCapabilities:
    can_resolve: bool
    can_revoke: bool


@dataclass(frozen=True, slots=True)
class GovernanceParticipantView:
    note_id: str
    participant_role: str
    document_version: str | None
    effective_from: str | None
    effective_to: str | None


@dataclass(frozen=True, slots=True)
class GovernanceCaseView:
    case_id: str
    case_type: str
    policy_key: str | None
    status: str
    canonical_note_id: str | None
    reason_code: str
    evidence_ids: tuple[str, ...]
    participants: tuple[GovernanceParticipantView, ...]
    created_at: str
    updated_at: str
    resolved_at: str | None
    capabilities: GovernanceCapabilities


@dataclass(frozen=True, slots=True)
class GovernanceEventView:
    event_id: str
    case_id: str | None
    note_id: str | None
    policy_key: str | None
    actor: str
    action: str
    previous_state: Mapping[str, str | int | float | bool | None]
    new_state: Mapping[str, str | int | float | bool | None]
    reason_code: str
    evidence_ids: tuple[str, ...]
    source: str
    request_id: str | None
    created_at: str
```

- [ ] **Step 1: Add failing authorization, ACL, CAS, atomicity, and privacy tests**

```python
def test_governance_write_requires_allowed_role(case_service, proposed_case):
    with pytest.raises(GovernanceAuthorizationError):
        case_service.resolve(
            proposed_case,
            expected_status="proposed",
            decision="confirm",
            canonical_note_id=None,
            actor="reader",
            roles=("read",),
            access_scope=finance_scope(),
            request_id="req-1",
        )


def test_hidden_case_is_indistinguishable_from_missing(case_service, hidden_case):
    assert case_service.get_case(hidden_case, access_scope=finance_scope()) is None
    assert case_service.get_case("missing", access_scope=finance_scope()) is None


def test_resolve_uses_compare_and_swap(case_service, proposed_case):
    case_service.resolve(
        proposed_case,
        expected_status="proposed",
        decision="reject",
        canonical_note_id=None,
        actor="reviewer",
        roles=("governance_reviewer",),
        access_scope=finance_scope(),
        request_id="req-1",
    )
    with pytest.raises(GovernanceConflictError):
        case_service.resolve(
            proposed_case,
            expected_status="proposed",
            decision="confirm",
            canonical_note_id=None,
            actor="reviewer",
            roles=("governance_reviewer",),
            access_scope=finance_scope(),
            request_id="req-2",
        )


def test_event_failure_rolls_back_decision(case_service, proposed_case, reject_event_trigger):
    with pytest.raises(GovernancePersistenceError):
        case_service.resolve(
            proposed_case,
            expected_status="proposed",
            decision="reject",
            canonical_note_id=None,
            actor="reviewer",
            roles=("governance_reviewer",),
            access_scope=finance_scope(),
            request_id="req-event-failure",
        )
    assert load_case_status(proposed_case) == "proposed"
    assert event_count(proposed_case) == 0


def test_revoke_appends_history_and_marks_participants_pending(case_service, confirmed_case):
    result = case_service.revoke(
        confirmed_case,
        expected_status="confirmed",
        actor="admin-user",
        roles=("admin",),
        access_scope=finance_scope(),
        request_id="req-revoke",
    )
    assert result.status == "revoked"
    assert event_actions(confirmed_case) == ["confirmed", "revoked"]
    assert participant_index_statuses(confirmed_case) == {"pending"}
```

- [ ] **Step 2: Run focused tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_governance_case_service.py -q --no-cov
```

- [ ] **Step 3: Implement ACL-filtered reads and role defense**

Accept only roles `admin` or `governance_reviewer`. Load all participants, check each with `note_acl_matches`, and return no case details unless every participant that would be disclosed is visible. List/event responses include IDs, state enums, reason codes, hashes/scores, and timestamps only.

- [ ] **Step 4: Implement one atomic human-decision transaction**

```sql
BEGIN IMMEDIATE;
SELECT status FROM governance_cases WHERE case_id = ?;
UPDATE governance_cases
SET status=?, canonical_note_id=?, resolved_at=?, resolved_by=?, request_id=?, updated_at=?
WHERE case_id=? AND status=?;
-- require rowcount == 1
INSERT INTO governance_events (
  event_id, case_id, note_id, policy_key, actor, action,
  previous_state_json, new_state_json, reason_code,
  evidence_ids_json, source, request_id, created_at
) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, 'human_review', ?, ?);
UPDATE notes
SET index_status='pending'
WHERE note_id IN ({participant_placeholders});
COMMIT;
```

Validate the canonical note is a participant before mutation. Reject actor fields supplied by callers at the service/API contract. Revoke writes a new event and never deletes or updates an existing event.

- [ ] **Step 5: Validate, independently review, fix, and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_governance_case_service.py tests/test_access_control.py -q --no-cov
.\.venv\Scripts\python.exe -m ruff check src/domain/governance.py src/application/governance_case_service.py src/application/access_control.py tests/test_governance_case_service.py
.\.venv\Scripts\python.exe -m pytest
git add src/domain/governance.py src/application/governance_case_service.py src/application/access_control.py tests/test_governance_case_service.py
git commit -m "feat: control knowledge governance decisions"
```

Review must check hidden-participant non-disclosure, any-of role enforcement, CAS behavior, event immutability, actor derivation boundary, and transaction rollback.

---

### Task 5: Sync, Reconciliation, and Index Eligibility

**Files:**
- Create: `tests/test_governance_indexing.py`
- Modify: `src/application/vault_sync_service.py`
- Modify: `src/application/directory_connector_service.py`
- Modify: `src/application/mindgraph_index_service.py`
- Modify: `src/application/mindgraph_sync_watcher.py`
- Modify: `src/api/dependencies.py`
- Modify: `tests/test_mindgraph_index_consistency.py`
- Modify: `tests/test_mindgraph_demo.py`
- Modify: `tests/test_directory_connector.py`

**Interfaces:**
- Consumes: `GovernanceReconciliationService.reconcile` and `evaluate_notes`.
- Produces: `MindGraphIndexService(db: ProductDatabase, vault_root: Path, index_root: Path, provider: Any | None = None, on_activated: Callable[[], None] | None = None, governance_reconciler: GovernanceReconciliationService | None = None)`; the production container must supply `governance_reconciler` and construction without it is allowed only in isolated legacy tests.
- Produces: index manifest fields `governance_as_of`, `governance_policy_version`, `eligible_note_count`, `excluded_reason_counts`, and canonical alias provenance.
- Preserves: connector source ownership, no-prune-on-scan-failure, `force=True` after prune, and atomic `CURRENT` activation.

- [ ] **Step 1: Add failing sync/index boundary tests**

```python
def test_index_excludes_draft_expired_superseded_and_unresolved_notes(governance_index_service):
    manifest = governance_index_service.build(force=True)
    chunks = active_chunks(manifest)
    assert {chunk["document_id"] for chunk in chunks} == {"current-note"}


def test_equivalent_aliases_index_once_and_retain_safe_citation_ids(governance_index_service):
    manifest = governance_index_service.build(force=True)
    chunks = active_chunks(manifest)
    canonical = [chunk for chunk in chunks if chunk["document_id"] == "canonical-note"]
    assert canonical
    assert canonical[0]["metadata"]["equivalent_note_ids"] == ["alias-note", "canonical-note"]


def test_reconciliation_failure_blocks_new_index_activation(index_service, failing_reconciler):
    previous = index_service.current_version()
    with pytest.raises(GovernanceUnavailableError):
        index_service.build(force=True)
    assert index_service.current_version() == previous


def test_failed_connector_scan_neither_prunes_reconciles_nor_builds(connector_with_fail_if_called_services):
    connector = connector_with_fail_if_called_services
    result = connector.sync()
    assert result["pruned"] == 0
    assert result["status"] == "failed"


def test_watcher_reconciles_before_build(watcher_with_order_checking_services):
    result = watcher_with_order_checking_services.run_once()
    assert result["build"]["status"] == "validated"
```

`connector_with_fail_if_called_services` injects reconciler/index doubles that raise `AssertionError` on any call, so the failed-scan path proves they were not reached without asserting on mock call history. `watcher_with_order_checking_services` uses a reconciler that creates a transaction-visible marker and an index service that refuses to build unless that marker exists; a validated result therefore proves ordering through behavior.

- [ ] **Step 2: Run focused tests and confirm current indexing admits ineligible notes**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_governance_indexing.py tests/test_mindgraph_index_consistency.py tests/test_directory_connector.py -q --no-cov
```

- [ ] **Step 3: Wire reconciliation after successful sync commits**

Add the reconciler dependency to production composition. Vault and connector sync call reconciliation only after the existing source-ownership transaction commits successfully. Scan/read failure returns without prune, reconciliation, or index build. Reconciliation failure preserves correct synced rows but prevents index activation and returns a controlled failure summary.

- [ ] **Step 4: Gate index construction with fresh policy evaluation**

Before `_load_note_chunks`, evaluate every note for the build date with confirmed decisions. Include only `eligible` canonical notes. Alias provenance contains note IDs only and is sorted; it does not copy path, title, ACL, or body. Add governance fields to chunks so dense/sparse metadata can be cross-checked, but treat those fields as defensive metadata rather than the final authority.

```python
evaluations = self.governance_reconciler.evaluate_notes(notes, as_of=build_date, mode=GovernanceMode.CURRENT)
eligible_notes = [
    note for note in notes
    if evaluations[note["note_id"]].disposition is GovernanceDisposition.ELIGIBLE
]
```

- [ ] **Step 5: Preserve atomic failure behavior and schedule date transitions**

The watcher runs reconciliation every cycle. A changed lifecycle membership marks notes pending and causes one rebuild. Repeated polls on the same date append no event and perform no rebuild. Failed build keeps the previous `CURRENT` and restores prior note index states.

- [ ] **Step 6: Validate, independently review, fix, and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_governance_indexing.py tests/test_mindgraph_index_consistency.py tests/test_mindgraph_demo.py tests/test_directory_connector.py tests/test_source_ownership.py -q --no-cov
.\.venv\Scripts\python.exe -m ruff check src/application/vault_sync_service.py src/application/directory_connector_service.py src/application/mindgraph_index_service.py src/application/mindgraph_sync_watcher.py src/api/dependencies.py tests/test_governance_indexing.py tests/test_mindgraph_index_consistency.py tests/test_mindgraph_demo.py tests/test_directory_connector.py
.\.venv\Scripts\python.exe -m pytest
git add src/application/vault_sync_service.py src/application/directory_connector_service.py src/application/mindgraph_index_service.py src/application/mindgraph_sync_watcher.py src/api/dependencies.py tests/test_governance_indexing.py tests/test_mindgraph_index_consistency.py tests/test_mindgraph_demo.py tests/test_directory_connector.py
git commit -m "feat: gate indexing by knowledge governance"
```

Review must check post-commit sequencing, no-prune failure behavior, canonical alias safety, fresh date evaluation, and previous-index preservation.

---

### Task 6: Retrieval, Graph, Version Conflict, and Chat Refusal

**Files:**
- Create: `tests/test_governance_retrieval.py`
- Modify: `src/retrieval/types.py`
- Modify: `src/retrieval/pipeline.py`
- Modify: `src/retrieval/mindgraph_pipeline.py`
- Modify: `src/infrastructure/retrieval_factory.py`
- Modify: `src/application/policy_conflict_service.py`
- Modify: `src/application/chat_service.py`
- Modify: `src/domain/models.py`
- Modify: `tests/test_retrieval.py`
- Modify: `tests/test_retrieval_acl_prefilter.py`
- Modify: `tests/test_policy_conflicts.py`
- Modify: `tests/test_api.py`
- Modify: `tests/test_sse_streaming.py`

**Interfaces:**
- Produces: `GovernancePrefilterResult(allowed_chunk_ids, corpus_count, eligible_count, excluded_reason_counts, as_of, mode)`.
- Produces: `RetrievalPipeline(dense: DenseRetriever, sparse: SparseRetriever, fusion: FusionStrategy, reranker: Reranker | None = None, candidate_count: int = 20, rerank_top_n: int = 10, final_top_k: int = 5, governance_policy: GovernancePolicy | None = None, governance_decision_loader: Callable[[Collection[str]], Mapping[str, Sequence[ConfirmedGovernanceDecision]]] | None = None)`; MindGraph production construction supplies both and raises if governance is unavailable.
- Produces: `applied_filters["governance_prefilter"]` aggregate trace contract.
- Preserves: `PolicyConflictService.find_for_policy_keys(policy_keys: set[str], *, as_of: str | None, include_historical: bool, access_scope: dict | None = None) -> list[dict[str, Any]]` as a compatibility facade.

- [ ] **Step 1: Add failing request validation and retrieval-order tests**

```python
def test_historical_flag_requires_query_date():
    with pytest.raises(ValidationError):
        ChatRequest(question="旧制度是什么", include_historical=True)


def test_acl_and_governance_prefilters_precede_search(order_checking_pipeline):
    trace = order_checking_pipeline.retrieve("报销上限", "hybrid", access_scope=finance_scope())
    assert trace.actual_strategy == "hybrid"
    assert trace.applied_filters["governance_prefilter"]["eligible_count"] == 1


def test_expired_chunk_in_stale_index_never_reaches_retrievers(pipeline_with_expired_chunk):
    trace = pipeline_with_expired_chunk.retrieve("额度", "hybrid", access_scope=finance_scope())
    assert "expired::0" not in pipeline_with_expired_chunk.dense.seen_allowed_ids
    assert "expired::0" not in pipeline_with_expired_chunk.sparse.seen_allowed_ids
    assert trace.applied_filters["governance_prefilter"]["excluded_reason_counts"] == {
        "effective_period_ended": 1
    }


def test_hidden_conflict_participant_is_not_disclosed(chat_service_with_sentinel_provider):
    chat_service = chat_service_with_sentinel_provider
    result = chat_service.answer(request(), access_scope=finance_scope())
    assert result.result_state is ResultState.conflicting_evidence
    assert result.answer == CONFLICTING
    assert "hidden-note" not in json.dumps(result.model_dump())


def test_graph_expansion_cannot_reintroduce_governance_excluded_note(graph_pipeline):
    trace = graph_pipeline.retrieve("例外", "hybrid", access_scope=finance_scope())
    assert all(item.chunk.document_id != "expired-related-note" for item in trace.final_selected_chunks)


def test_canonical_citation_retains_acl_equivalent_alias_ids(chat_service):
    result = chat_service.answer(request(), access_scope=finance_scope())
    citation = next(item for item in result.citations if item.document_id == "canonical-note")
    assert citation.equivalent_document_ids == ["alias-note", "canonical-note"]
```

Also cover empty scope, public scope, wildcard, deny, incomplete corpus metadata, dense/sparse governance metadata disagreement, duplicate chunk IDs, current unique selection, zero eligible versions, historical date selection, and overlap refusal before Provider invocation in both answer and SSE paths.

The order-checking dense and sparse retrievers raise if their allowed-ID set contains an ACL-denied or governance-excluded chunk; successful retrieval plus the aggregate trace proves both prefilters ran before search. The sentinel Provider returns the unique text `PROVIDER_WAS_CALLED`; deterministic refusal tests assert that text is absent and the exact refusal state/text is returned, testing the service result rather than mock call counts.

- [ ] **Step 2: Run focused tests and confirm governance prefilter is absent**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_governance_retrieval.py tests/test_retrieval_acl_prefilter.py tests/test_policy_conflicts.py tests/test_api.py tests/test_sse_streaming.py -q --no-cov
```

- [ ] **Step 3: Add governance prefilter after ACL prefilter and before search**

Use complete dense/sparse chunk maps already loaded for ACL. First compute ACL-allowed IDs. Evaluate only ACL-visible metadata with the pure policy and freshly loaded confirmed decisions. Intersect the two allowed-ID sets before calling retrievers. Missing decision storage or corrupt case state raises `GovernanceUnavailableError`.

```python
acl_ids, acl_counts, normalized_scope = self._access_prefilter(access_scope)
governance = self._governance_prefilter(
    allowed_chunk_ids=acl_ids,
    as_of=resolved_date,
    mode=GovernanceMode.HISTORICAL if query_date else GovernanceMode.CURRENT,
)
allowed_ids = governance.allowed_chunk_ids
dense_results = self.dense.search(query, self.top_k, allowed_chunk_ids=allowed_ids)
sparse_results = self.sparse.search(query, self.top_k, allowed_chunk_ids=allowed_ids)
```

Trace only exact date/mode, aggregate counts/reasons, and reconciliation/index identifiers. Do not include excluded IDs, titles, paths, ACLs, or content.

- [ ] **Step 4: Replace graph-local lifecycle logic with the shared policy**

Remove date/status reimplementation from `_visible_for_trace`. Graph targets must pass the same ACL and governance evaluator used by base retrieval. Preserve defense-in-depth checks after expansion and prevent metadata disagreement from being hidden by a confirmed edge.

- [ ] **Step 5: Make conflict and insufficient-evidence decisions precede Provider calls**

`PolicyConflictService` delegates interval evaluation to `GovernancePolicy` and retains its public method while returning privacy-safe conflict summaries. Chat answer and stream paths use the same helper so zero eligible versions returns `insufficient_evidence`, overlap returns `conflicting_evidence`, and neither path calls the Provider. Add `equivalent_document_ids: list[str]` to `Citation`; populate it only from exact-duplicate aliases that already passed identical normalized ACL, workspace, department, lifecycle, version, and source-ownership checks.

- [ ] **Step 6: Validate, independently review, fix, and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_governance_retrieval.py tests/test_retrieval.py tests/test_retrieval_acl_prefilter.py tests/test_policy_conflicts.py tests/test_api.py tests/test_sse_streaming.py -q --no-cov
.\.venv\Scripts\python.exe -m ruff check src/retrieval/types.py src/retrieval/pipeline.py src/retrieval/mindgraph_pipeline.py src/infrastructure/retrieval_factory.py src/application/policy_conflict_service.py src/application/chat_service.py src/domain/models.py tests/test_governance_retrieval.py tests/test_retrieval.py tests/test_retrieval_acl_prefilter.py tests/test_policy_conflicts.py tests/test_api.py tests/test_sse_streaming.py
.\.venv\Scripts\python.exe -m pytest
git add src/retrieval/types.py src/retrieval/pipeline.py src/retrieval/mindgraph_pipeline.py src/infrastructure/retrieval_factory.py src/application/policy_conflict_service.py src/application/chat_service.py src/domain/models.py tests/test_governance_retrieval.py tests/test_retrieval.py tests/test_retrieval_acl_prefilter.py tests/test_policy_conflicts.py tests/test_api.py tests/test_sse_streaming.py
git commit -m "feat: enforce governance before retrieval"
```

Review must trace unauthorized/ineligible data through every stage and verify no fallback, no hidden participant leak, no Provider call on refusal, and identical answer/SSE behavior.

---

### Task 7: Controlled API, Progressive Web Queue, Health, and Final Acceptance

**Files:**
- Create: `src/api/routes/knowledge_governance.py`
- Create: `tests/test_knowledge_governance_api.py`
- Create: `web/src/lib/knowledge-governance.test.ts`
- Modify: `src/api/auth.py`
- Modify: `src/api/dependencies.py`
- Modify: `src/api/main.py`
- Modify: `src/api/routes/health.py`
- Modify: `src/api/routes/mindgraph_readonly.py`
- Modify: `web/src/types.ts`
- Modify: `web/src/lib/api.ts`
- Modify: `web/src/lib/api.test.ts`
- Modify: `web/src/components/PolicyGovernance.tsx`
- Modify: `web/src/pages/KnowledgePage.tsx`
- Modify: `web/src/styles.css`
- Modify: `README.md`
- Modify: `docs/UPGRADE_PLAN.md`
- Modify: `docs/PRODUCT_STRATEGY.md`

**Interfaces:**
- Produces: `require_any_role(*roles: str)` FastAPI dependency.
- Produces: `GET /api/v1/knowledge-governance/cases`, `GET /cases/{case_id}`, `POST /cases/{case_id}/resolve`, `POST /cases/{case_id}/revoke`, and `GET /events`.
- Produces: Web `GovernanceCase`, `GovernanceEvent`, and `GovernanceCapabilities` types plus `api.governanceCases`, `api.governanceEvents`, `api.resolveGovernanceCase`, and `api.revokeGovernanceCase`.
- Consumes: Task 4 service and existing principal/access-audit helpers.

- [ ] **Step 1: Add failing API contract, authorization, actor, privacy, and health tests**

```python
def test_resolve_rejects_actor_override(client, reviewer_headers, proposed_case):
    response = client.post(
        f"/api/v1/knowledge-governance/cases/{proposed_case}/resolve",
        headers=reviewer_headers,
        json={
            "expected_status": "proposed",
            "decision": "confirm",
            "resolved_by": "spoofed-admin",
        },
    )
    assert response.status_code == 422


def test_reader_cannot_resolve_and_denial_is_audited(client, reader_headers, proposed_case, db):
    response = client.post(
        f"/api/v1/knowledge-governance/cases/{proposed_case}/resolve",
        headers=reader_headers,
        json={"expected_status": "proposed", "decision": "reject"},
    )
    assert response.status_code == 403
    assert latest_access_audit(db)["decision"] == "deny"


def test_hidden_case_returns_404(client, reviewer_headers, hidden_case):
    response = client.get(
        f"/api/v1/knowledge-governance/cases/{hidden_case}",
        headers=reviewer_headers,
    )
    assert response.status_code == 404


def test_event_response_contains_no_sensitive_fields(client, admin_headers):
    response = client.get("/api/v1/knowledge-governance/events", headers=admin_headers)
    serialized = json.dumps(response.json()).lower()
    for forbidden in ("body", "title", "vault_path", "acl_json", "token", "secret"):
        assert forbidden not in serialized


def test_health_reports_governance_readiness_without_private_ids(client):
    body = client.get("/api/v1/health").json()
    assert set(body["governance"]) >= {
        "schema_ready",
        "last_reconciled_at",
        "last_reconciliation_status",
        "pending_case_count",
        "active_index_governed",
    }
```

- [ ] **Step 2: Add failing Web API and view-model tests**

```typescript
import { describe, expect, it } from "vitest";

import { governanceCaseView } from "./knowledge-governance";

describe("governanceCaseView", () => {
  it("uses safe labels and exposes actions only when capability allows", () => {
    const view = governanceCaseView({
      case_id: "case-1",
      case_type: "version_overlap",
      status: "proposed",
      reason_code: "overlapping_effective_versions",
      participants: [{ note_id: "note-1", version: "1.0", effective_from: "2026-01-01", effective_to: null }],
      capabilities: { can_resolve: false, can_revoke: false },
    });
    expect(view.reasonLabel).toBe("有效期重叠");
    expect(view.actions).toEqual([]);
  });
});
```

Mock `fetch` in `web/src/lib/api.test.ts` and assert resolve/revoke bodies contain expected state and decision only, never `actor` or `resolved_by`.

- [ ] **Step 3: Run API and Web tests and verify missing routes/types**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_knowledge_governance_api.py tests/test_api.py -q --no-cov
Push-Location web
corepack pnpm test
Pop-Location
```

- [ ] **Step 4: Implement any-of role dependency and route mapping**

```python
def require_any_role(*roles: str) -> Callable:
    allowed = frozenset(roles)

    async def role_checker(principal: dict = Depends(get_required_principal)) -> dict:
        if allowed.isdisjoint(principal.get("roles", [])):
            raise AuthorizationError("Missing required governance role")
        return principal

    return role_checker
```

The router derives `actor` from the principal, scope from the request, and request ID from middleware state. Pydantic uses `extra="forbid"`. Map invalid payload/canonical to 422, missing authentication to 401, unauthorized role to 403 plus access audit, hidden case to 404, stale expected status to 409, and unavailable/corrupt governance to 503. Do not add a batch endpoint.

- [ ] **Step 5: Compose services and add privacy-safe health/display**

Register `knowledge_governance.router` separately from evaluation `governance.router`. Schema 8 must let health start but report governance unavailable; routes and governed MindGraph retrieval return controlled 503, not an ungoverned result. Extend note display with lifecycle/disposition/reason codes calculated for the response date; do not expose hidden participants.

- [ ] **Step 6: Implement the progressive Web queue**

Extend `PolicyGovernance.tsx` with reason labels and an exported queue component. `KnowledgePage` initially shows only a pending-count summary; expanding loads visible cases. Details show safe IDs, versions, effective dates, and reason labels. Buttons render only from server capabilities. Resolve/reject/revoke refresh both the queue and note ledger. Error states preserve the current list and show retry guidance.

- [ ] **Step 7: Validate Python and Web, then correct documentation status**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_knowledge_governance_api.py tests/test_api.py tests/test_governance_case_service.py -q --no-cov
.\.venv\Scripts\python.exe -m ruff check src/api/auth.py src/api/dependencies.py src/api/main.py src/api/routes/health.py src/api/routes/mindgraph_readonly.py src/api/routes/knowledge_governance.py tests/test_knowledge_governance_api.py
Push-Location web
corepack pnpm typecheck
corepack pnpm test
corepack pnpm build
Pop-Location
```

Only after all Task 1-7 gates pass, update roadmap language from planned/partial to implemented. Documentation must still state that no real schema-9 migration was applied and that GraphRAG remains controlled one-hop relation expansion rather than a complete knowledge-graph engine.

- [ ] **Step 8: Run final repository verification**

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src scripts tests --select F821,F822,F823,E902
.\.venv\Scripts\python.exe -m ruff check src scripts tests
Push-Location web
corepack pnpm typecheck
corepack pnpm test
corepack pnpm build
Pop-Location
git status --short
```

Acceptance interpretation:

- Full pytest and fatal Ruff gate must pass.
- Web typecheck, tests, and build must pass.
- Full Ruff historical violations are counted and reported; they do not fail UG-008 unless a touched file introduced a violation.
- Existing missing-`ZHIPU_API_KEY` and Starlette/httpx warnings are reported as warnings, not described as success and not suppressed through `.env` or dependency/CI changes.
- Worktree may contain only intended UG-008 changes before the final commit.

- [ ] **Step 9: Obtain final independent review, fix every accepted finding, rerun all gates, and commit**

The reviewer receives the spec, this plan, the complete branch diff from `a6fa7de`, all focused/full test output, Ruff output, Web output, and the explicit no-real-migration/no-push constraints. Review priorities are ACL leaks, lifecycle disagreements, event privacy, transaction atomicity, startup migration, Provider-before-refusal errors, stale-index behavior, and false completion claims.

```powershell
git add src/api/auth.py src/api/dependencies.py src/api/main.py src/api/routes/health.py src/api/routes/mindgraph_readonly.py src/api/routes/knowledge_governance.py tests/test_knowledge_governance_api.py web/src/types.ts web/src/lib/api.ts web/src/lib/api.test.ts web/src/lib/knowledge-governance.test.ts web/src/components/PolicyGovernance.tsx web/src/pages/KnowledgePage.tsx web/src/styles.css README.md docs/UPGRADE_PLAN.md docs/PRODUCT_STRATEGY.md
git commit -m "feat: expose governed knowledge operations"
```

After the final commit, rerun `git status --short` and `git log -8 --oneline`. Do not push. Capture only confirmed, non-sensitive decisions and verified state with `codex-memory capture`; if capture fails, report the failure rather than editing the Vault manually.
