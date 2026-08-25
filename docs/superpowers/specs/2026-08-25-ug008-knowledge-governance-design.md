# UG-008 Knowledge Governance and Lifecycle Filtering Design

**Status:** Discussion decisions incorporated; awaiting written-spec approval; implementation not started

**Date:** 2026-08-25

**Scope:** MindGraph production `notes` ingestion, governance reconciliation, index eligibility, retrieval filtering, conflict refusal, controlled governance operations, audit, and the existing Web knowledge workspace.

## 1. Goal

MindGraph must use the same knowledge-governance rules when content enters `notes`, when an index is built, when retrieval candidates are selected, and when ChatService decides whether evidence is safe to answer from.

The delivered behavior must ensure:

- draft, expired, superseded, archived, unresolved, and confirmed-blocked material does not become default answer evidence;
- a single currently effective version is preferred without guessing from version strings or vector scores;
- overlapping current versions are exposed as conflicts and are not silently ranked away;
- exact duplicates are collapsed only when content and security/governance metadata are equivalent;
- automatic and human governance actions are auditable without storing content, paths, ACL payloads, or secrets;
- governance failure never degrades into ungoverned retrieval.

UG-008 remains `Planned` or `Partial` until every implementation task, full verification, and independent review passes.

## 2. Non-Goals

UG-008 does not implement or change:

- OCR fallback or layout-aware PDF ingestion;
- multi-query retrieval;
- MCP async/privacy work;
- external OIDC IdP acceptance;
- UG-007 dataset expansion or CI thresholds;
- automatic semantic conflict resolution;
- direct editing of source Markdown from the governance UI;
- direct SQLite metadata overrides that source sync would later replace;
- consolidation of `notes` with the historical `document_versions` flow;
- deletion of `DocumentLifecycleService` or `PolicyConflictService`;
- a general event-sourcing platform;
- a new vector database;
- bulk governance decisions;
- automatic application or rollback against a real database;
- push, PR creation, merge, deployment, or publication.

## 3. Design Principles

### 3.1 Keep declared and derived state separate

`policy_status` remains the normalized status declared by the source. Dates produce a derived lifecycle state for a specific `as_of` date. The system does not rewrite Markdown or pretend that a derived date transition was a human source edit.

### 3.2 Use one governance policy everywhere

One pure `GovernancePolicy` evaluator owns status, date, metadata-completeness, and confirmed-decision rules. Ingestion, reconciliation, indexing, retrieval, and chat may consume its result but may not reimplement its rules.

### 3.3 Fail closed

Missing or corrupt governance metadata, missing complete corpus metadata, dense/sparse metadata disagreement, reconciliation failure, or governance-decision corruption must prevent affected material from becoming evidence. None of these failures may downgrade to global or ungoverned retrieval.

### 3.4 Persist decisions, not temporal truth

Human decisions and confirmed automatic duplicate decisions are durable facts. `lifecycle_state` is calculated for an `as_of` date. A materialized note-state projection may be stored to detect changes and schedule indexing, but retrieval must recalculate and cannot trust a stale projection.

### 3.5 Preserve ACL boundaries

ACL prefiltering remains the first candidate boundary. Governance traces and APIs must not reveal hidden note IDs, titles, paths, ACL data, or conflict participants.

## 4. State Model

The system uses three independent dimensions.

### 4.1 Declared status

Allowed normalized `policy_status` values:

- `draft`
- `active`
- `archived`
- `superseded`
- `unspecified`

`expired` remains accepted as a compatibility source value during migration, but policy evaluation treats it as a non-current declaration. New documentation should use dates plus `active`, or a terminal declaration such as `superseded` or `archived`.

### 4.2 Derived lifecycle state

- `not_yet_effective`
- `current`
- `expired`
- `historical`
- `unresolved`

### 4.3 Governance disposition

- `eligible`
- `excluded`
- `unresolved`
- `conflict_blocked`
- `duplicate_alias`

Every evaluation returns stable reason codes. Initial reason codes are:

- `eligible_current_version`
- `eligible_historical_version`
- `declared_draft`
- `declared_archived`
- `declared_superseded`
- `declared_expired`
- `not_yet_effective`
- `effective_period_ended`
- `metadata_incomplete`
- `invalid_effective_date`
- `invalid_effective_range`
- `confirmed_governance_block`
- `confirmed_duplicate_alias`
- `overlapping_effective_versions`
- `governance_metadata_conflict`

Reason codes are API and trace contracts. Implementations may add codes but must not rename existing codes without a versioned contract change.

## 5. Evaluation Rules

`GovernancePolicy.evaluate(note, *, as_of, mode, confirmed_decisions)` returns an immutable `GovernanceEvaluation` containing the three state dimensions, `eligible`, reason codes, and any canonical note ID.

### 5.1 Current mode

The default `as_of` is the service's current local date, serialized into the retrieval trace.

A note is eligible only when all of the following hold:

1. owner, policy key, version, effective-from date, and declared status are valid;
2. declared status is `active`;
3. `effective_from <= as_of`;
4. `effective_to` is absent or `as_of <= effective_to`;
5. no confirmed case blocks the note;
6. the note is not a confirmed duplicate alias.

Date values are parsed with `date.fromisoformat`. Lexicographic string comparison is not a governance rule.

### 5.2 Historical mode

An explicit `query_date` selects historical mode. `include_historical=True` without `query_date` is invalid and must be rejected rather than mixing all historical versions into a current answer.

Historical eligibility rules are:

- `draft` and `unspecified` are never eligible;
- `active`, compatibility `expired`, `superseded`, and `archived` may be eligible only when the effective interval proves validity on `query_date`;
- a terminal declaration without enough date metadata to prove past validity is unresolved;
- current confirmed governance facts continue to apply; UG-008 does not implement bitemporal replay of what reviewers believed in the past.

### 5.3 Version selection

For one `policy_key` and one `as_of` date:

- exactly one eligible version is selected;
- zero eligible versions produces insufficient governed evidence;
- two or more eligible versions produce an overlap conflict;
- authority weight and version-string ordering cannot resolve an overlap;
- authority adjustment remains available only for ranking evidence from different policy families after governance filtering.

## 6. Conflict and Duplicate Rules

### 6.1 Deterministic conflict detection

A deterministic conflict case is proposed when:

- two governance-complete notes share a policy key and have overlapping effective intervals; or
- two notes share `policy_key + document_version` but have different checksums.

When two otherwise eligible, ACL-visible versions overlap for a query, ChatService refuses before calling the Provider. The visible versions and effective dates may be returned. Hidden participants are never disclosed.

Confirmed blocking cases exclude their participants even when another participant is hidden. The caller receives only the generic reason `confirmed_governance_block` unless every disclosed participant passes ACL filtering.

### 6.2 Semantic candidates

Vector similarity or LLM analysis may create a proposed semantic conflict/duplicate case. It cannot automatically exclude content, choose a canonical source, or globally block retrieval. This prevents model errors from becoming a denial-of-service mechanism.

### 6.3 Exact duplicates

Checksum equality alone is insufficient because identical text may have different permissions or lifecycle meaning. Automatic confirmed duplicate collapse requires equality of:

- content checksum;
- policy key and document version;
- effective-from and effective-to dates;
- declared status;
- workspace and department;
- normalized ACL;
- source-ownership consistency.

The canonical member is the stable minimum note ID. This is an implementation identity, not an authority claim. One canonical chunk set is indexed and citations retain all equivalent sources.

If any security or governance metadata differs, the system creates only a proposed duplicate case.

## 7. Architecture and Components

### 7.1 New files

- `src/domain/governance.py`
  - enums and immutable evaluation/case types;
  - no database, FastAPI, or retrieval dependencies.

- `src/application/governance_policy.py`
  - pure declared-status, date, decision, and eligibility evaluation.

- `src/application/governance_reconciliation_service.py`
  - materialized-state reconciliation;
  - deterministic case discovery;
  - idempotent automatic events;
  - note index-state scheduling.

- `src/application/governance_case_service.py`
  - case queries, resolve, reject, and revoke;
  - authorization inputs, ACL checks, compare-and-swap, and atomic audit.

- `src/api/routes/knowledge_governance.py`
  - controlled knowledge-governance APIs, separate from evaluation governance.

- `scripts/migrate_governance_schema.py`
  - dry-run/apply/exact-run rollback for existing schema-8 databases.

### 7.2 Modified production files

- `src/infrastructure/database.py`
  - schema constants and new-database schema;
  - no automatic schema-8 to schema-9 upgrade.

- `src/application/vault_sync_service.py`
  - strict date normalization and governance metadata issues;
  - invokes reconciliation only after successful sync.

- `src/application/mindgraph_index_service.py`
  - governance eligibility gate before chunk creation/indexing.

- `src/application/mindgraph_sync_watcher.py`
  - reconciliation before build and date-boundary detection.

- `src/retrieval/pipeline.py`
  - governance prefilter before dense/BM25 and final defense filter.

- `src/retrieval/types.py`
  - aggregate governance trace fields.

- `src/application/policy_conflict_service.py`
  - compatibility facade over unified policy/case logic.

- `src/application/chat_service.py`
  - governed conflict/insufficient-evidence behavior before Provider calls.

- `src/api/dependencies.py` and `src/api/main.py`
  - service composition and route registration.

- `src/api/routes/mindgraph_readonly.py`
  - lifecycle/disposition display; existing relation endpoints remain separate.

- `web/src/types.ts`, `web/src/lib/api.ts`, `web/src/components/PolicyGovernance.tsx`, and `web/src/pages/KnowledgePage.tsx`
  - progressive governance queue and controlled actions.

`DocumentLifecycleService` is not extended into a second governance authority. `PolicyConflictService` remains as a compatibility wrapper and is not deleted in UG-008.

## 8. Data Model

Schema 9 adds four knowledge-governance tables and one general migration-audit table.

### 8.1 `governance_cases`

```text
case_id TEXT PRIMARY KEY
case_type TEXT NOT NULL
policy_key TEXT
status TEXT NOT NULL
canonical_note_id TEXT
reason_code TEXT NOT NULL
rule_key TEXT NOT NULL
evidence_json TEXT NOT NULL DEFAULT '{}'
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
resolved_at TEXT
resolved_by TEXT
request_id TEXT
UNIQUE(case_type, rule_key)
```

Allowed case status values are `proposed`, `confirmed`, `rejected`, and `revoked`.

`rule_key` is a stable hash of case type, sorted participant IDs, and relevant normalized metadata. An unchanged rejected case does not reappear. Changed evidence produces a different rule key and a new case.

### 8.2 `governance_case_notes`

```text
case_id TEXT NOT NULL
note_id TEXT NOT NULL
participant_role TEXT NOT NULL
PRIMARY KEY(case_id, note_id)
FOREIGN KEY(case_id) REFERENCES governance_cases(case_id)
FOREIGN KEY(note_id) REFERENCES notes(note_id)
```

Participant roles are `candidate`, `canonical`, `alias`, and `superseded`.

### 8.3 `governance_note_state`

```text
note_id TEXT PRIMARY KEY
evaluated_on TEXT NOT NULL
lifecycle_state TEXT NOT NULL
disposition TEXT NOT NULL
reason_codes_json TEXT NOT NULL
decision_fingerprint TEXT NOT NULL
updated_at TEXT NOT NULL
FOREIGN KEY(note_id) REFERENCES notes(note_id)
```

This table is a disposable projection. It is not consulted as the final retrieval authority.

### 8.4 `governance_events`

```text
event_id TEXT PRIMARY KEY
case_id TEXT
note_id TEXT
policy_key TEXT
actor TEXT NOT NULL
action TEXT NOT NULL
previous_state_json TEXT NOT NULL
new_state_json TEXT NOT NULL
reason_code TEXT NOT NULL
evidence_ids_json TEXT NOT NULL
source TEXT NOT NULL
request_id TEXT
created_at TEXT NOT NULL
```

Event sources are `ingestion_rule`, `lifecycle_rule`, and `human_review`. SQLite `BEFORE UPDATE` and `BEFORE DELETE` triggers abort any mutation of this table.

Events contain only IDs, hashes, scores, enum states, reason codes, and timestamps. They do not contain note bodies, titles, relative or absolute paths, ACL payloads, credentials, tokens, or connector secrets.

### 8.5 `schema_migration_runs`

The migration ledger stores migration name, exact run ID, previous/target schema versions, status, aggregate object counts, and timestamps. It contains no database path or business data.

The ledger is operational audit infrastructure, not a governance business table. It is intentionally retained after an allowed rollback so the rollback remains auditable. A schema-8 runtime must tolerate this otherwise-unused table.

## 9. Migration Safety

Existing schema-8 databases are not upgraded by normal application startup.

```powershell
python scripts/migrate_governance_schema.py
python scripts/migrate_governance_schema.py --dry-run
python scripts/migrate_governance_schema.py --apply
python scripts/migrate_governance_schema.py --rollback RUN_ID
```

Rules:

- default is read-only dry-run;
- dry-run reports current/target versions and aggregate DDL object counts;
- apply uses `BEGIN IMMEDIATE`, creates schema-9 objects, records a migration run, and updates `schema_meta` atomically;
- apply does not backfill or rewrite `notes`;
- rollback requires the exact completed run ID;
- rollback refuses after any governance case, participant, note-state, or event business row exists;
- an allowed rollback drops the four unused governance tables and their indexes/triggers, restores the logical application schema version to 8, marks the migration run rolled back, and retains only `schema_migration_runs` as inert operational audit evidence;
- no real apply or rollback is run by tests or implementation automation;
- a brand-new empty database may initialize directly at schema 9;
- a schema-8 runtime reports governance unavailable until an operator applies the migration.

## 10. Processing and Transaction Boundaries

### 10.1 Source sync

1. Vault or connector scan completes under the existing source-ownership transaction rules.
2. Scan failure or partial read failure performs no prune and starts no governance/index work.
3. Successful note changes commit first.
4. Reconciliation runs for changed policy keys and notes.
5. Index build is allowed only after successful reconciliation.

Governance failure does not roll back a correct source sync, but it blocks new index activation.

### 10.2 Reconciliation

For each affected note, reconciliation calculates a canonical JSON decision fingerprint from normalized source metadata, `as_of`, and confirmed decisions.

When the fingerprint is unchanged, no projection write and no event occur. When it changes, one transaction:

1. updates the note-state projection;
2. creates or updates idempotent cases;
3. appends automatic events for actual state changes;
4. marks affected notes `pending` when index membership changes.

The watcher reconciles each cycle. Date boundary changes therefore produce one transition event and schedule one rebuild, not one event per poll.

### 10.3 Human resolution

Resolve/reject/revoke uses one `BEGIN IMMEDIATE` transaction:

1. load current case state;
2. validate actor role and ACL for participants;
3. validate canonical membership when applicable;
4. compare-and-swap the expected case status;
5. update case and participant roles;
6. append a governance event;
7. update affected projections;
8. mark affected notes pending;
9. commit.

An event insert failure rolls back the entire decision. Stale requests return conflict and cannot overwrite later decisions. Revoke appends a new event; it never deletes history.

## 11. Index and Retrieval Integration

Index construction calls `GovernancePolicy` before creating chunks and physically excludes ineligible notes. Existing embedding reuse and atomic `CURRENT.tmp` activation remain unchanged.

Retrieval uses this order:

1. load complete dense and sparse corpus metadata;
2. ACL prefilter;
3. governance prefilter for the request `as_of`;
4. dense and BM25 search with allowed canonical chunk IDs;
5. fusion and optional rerank;
6. graph expansion restricted to ACL- and governance-eligible notes;
7. defense-in-depth ACL and governance filtering;
8. final citations.

If an index still contains a newly expired note, real-time governance prefilter removes it. If a future note becomes current before rebuild, the safe outcome is temporary false refusal rather than using stale evidence; reconciliation schedules the required rebuild.

Dense/sparse duplicate chunk IDs or inconsistent ACL/governance metadata fail closed before search.

## 12. Retrieval Trace and Privacy

`RetrievalTrace.applied_filters.governance_prefilter` contains:

- exact `as_of` date;
- `current` or `historical` mode;
- corpus, eligible, and excluded counts;
- aggregate reason-code counts;
- reconciliation/index version identifiers where available.

It does not contain excluded note IDs, titles, paths, ACLs, or content.

When hidden participants affect a confirmed block, callers receive only `confirmed_governance_block`. Detailed participants are available only to governance actors who pass every applicable ACL check.

## 13. API and Authorization

Knowledge-governance routes are:

- `GET /api/v1/knowledge-governance/cases`
- `GET /api/v1/knowledge-governance/cases/{case_id}`
- `POST /api/v1/knowledge-governance/cases/{case_id}/resolve`
- `POST /api/v1/knowledge-governance/cases/{case_id}/revoke`
- `GET /api/v1/knowledge-governance/events`

Write operations require `admin` or `governance_reviewer`. `AUTH_MODE=off` uses the existing authenticated local-development principal with `admin`. Ordinary and anonymous callers cannot write.

The actor is derived from the authenticated principal. Request bodies reject `resolved_by` or other actor-override fields. Case and event reads are ACL filtered.

Initial implementation does not provide batch resolution.

Expected errors:

- invalid request or historical flag without date: `422`;
- unauthenticated: `401` under enterprise auth modes;
- authenticated without governance role: `403` plus access audit;
- hidden case: `404` to avoid existence disclosure;
- stale expected state: `409`;
- invalid canonical member: `422`;
- governance unavailable or corrupt: controlled service-unavailable response, never ungoverned fallback.

## 14. Web Interaction

The existing Knowledge page gains a progressive governance queue:

- collapsed summary shows pending count;
- expanded list shows ACL-visible proposed cases;
- details show case type, visible versions, effective dates, reason code, and safe evidence IDs;
- authorized actors see confirm, reject, and revoke actions;
- other actors receive read-only UI or no action controls;
- unresolved items direct operators to fix source metadata;
- no body, absolute path, raw ACL, direct SQLite edit, or batch confirmation is exposed.

The existing `PolicyGovernance` component is extended rather than replaced, and no separate administration application is introduced.

## 15. Error Handling and Health

- Reconciliation failure preserves synced notes, blocks new index build, and records safe health state.
- Index build failure preserves the previous active index.
- Retrieval governance-evaluation failure returns governed unavailability and does not search globally.
- Invalid source dates become metadata issues and unresolved state, not uncaught parsing exceptions.
- Corrupt case/event state fails closed and is exposed to operators through health without content details.
- Access denial writes `access_audit` but does not create a governance event.
- Successful automatic/human governance state changes write `governance_events`.

Health reports schema readiness, last reconciliation status/time, pending case count, and whether the active index was built under governance. It does not report private identifiers.

## 16. Test Matrix

### 16.1 Pure policy tests

- every declared status;
- before effective date, boundary date, end boundary, and after end;
- open-ended intervals;
- invalid and reversed dates;
- current and historical modes;
- stable reason codes.

### 16.2 Migration tests

- schema-8 dry-run is read-only;
- apply creates exact schema-9 objects without rewriting notes;
- repeat apply is safely rejected or reported already applied;
- exact-run rollback works only while governance tables are unused;
- stale/unknown rollback IDs fail;
- rollback refuses once audit/business rows exist;
- event update/delete triggers abort.

### 16.3 Ingestion and reconciliation tests

- valid metadata produces eligible projection;
- invalid metadata produces unresolved projection;
- repeat scan produces no duplicate event;
- date rollover produces one event and pending index state;
- unchanged rejected case does not reappear;
- changed evidence creates a new case;
- reconciliation failure blocks index build.

### 16.4 Conflict and duplicate tests

- non-overlapping versions select the correct date-specific version;
- overlapping versions block before Provider invocation;
- same policy/version with different checksum blocks;
- same checksum with different ACL is not collapsed;
- fully equivalent duplicates index once and cite all sources;
- unconfirmed semantic candidates do not globally block.

### 16.5 ACL and retrieval tests

- unauthorized chunks never enter governance candidate search, dense, BM25, fusion, rerank, graph, or context;
- hidden conflict participant details do not leak;
- public-only, empty scope, wildcard, and deny behavior;
- dense/sparse governance metadata disagreement fails closed;
- expired index residue is prefiltered before search;
- current unique version is selected;
- historical query requires and respects `query_date`.

### 16.6 Transaction and API tests

- case update and event insert are atomic;
- event failure rolls back case changes;
- compare-and-swap rejects stale decisions;
- actor spoof fields are rejected;
- role and ACL checks cover normal, denied, and hidden cases;
- event output excludes content, path, ACL, and secret fields;
- revoke appends history and reschedules indexing.

### 16.7 Web tests

- status/reason labels;
- progressive case list and detail;
- action controls follow capability/role response;
- hidden data is not rendered;
- resolve/reject/revoke success and controlled failure states.

### 16.8 Acceptance gates

Each task follows failing test, implementation, focused tests, touched-file Ruff, full pytest, independent review, and commit. Final acceptance also runs the repository fatal Ruff gate. Historical full-Ruff debt is reported separately and is not hidden with ignores, lowered rules, CI changes, or bypasses.

## 17. Rollout and Completion

Implementation is split into six independently reviewable units:

1. domain types and pure governance policy;
2. schema-9 migration CLI and append-only audit schema;
3. reconciliation, cases, idempotent automatic events, and transactions;
4. index, retrieval, graph, and chat integration;
5. controlled API and Web governance queue;
6. end-to-end acceptance, documentation, roadmap status, and independent final review.

No real database migration is applied automatically. No push, PR, merge, deletion, or deployment occurs without later explicit authorization.
