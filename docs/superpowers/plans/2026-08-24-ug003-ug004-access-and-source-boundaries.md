# UG-003 and UG-004 Access and Source Boundaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make note provenance durable and make ACL filtering happen before dense retrieval, BM25, fusion, reranking, graph expansion, and model context construction.

**Architecture:** Add a stable `source_id` to notes and migrate existing connector-prefixed notes deterministically. `VaultSyncService` owns a single source per scan and prunes only that source after a complete error-free snapshot, in one SQLite transaction. `RetrievalPipeline` computes visible chunk IDs from metadata before invoking either retriever; FAISS and BM25 then operate only on that allowed set, while existing final filtering remains a defensive check.

**Tech Stack:** Python 3.12, SQLite, FAISS, NumPy, BM25, pytest, Ruff.

**Spec:** `docs/UPGRADE_PLAN.md` (UG-003, UG-004, and cross-item principles 1–5).

## Global Constraints

- Existing public API behavior is preserved except that authorized scopes never receive private candidates at any retrieval stage.
- `AUTH_MODE=off` remains the only explicit ACL bypass; an access scope with no allowed tags must deny non-public data.
- A failed or partial source scan must prune zero rows and must never write connector source files.
- ACL backfill precedence is frontmatter explicit ACL, then controlled directory/default ACL, then explicit public marker; unresolved records become private with an auditable reason.
- Schema changes must be idempotent, transactionally safe where deletions occur, include a repeatable backfill/rollback path, and not contain secrets.
- Tests must be offline and cover normal, failure, and cross-source boundary cases before production code changes.

---

### Task 1: Persist source ownership and migration audit records

**Files:**
- Modify: `src/infrastructure/database.py`
- Create: `src/application/acl_backfill_service.py`
- Create: `tests/test_acl_backfill_service.py`

**Interfaces:**
- Produces `SCHEMA_VERSION = 8` and `notes.source_id TEXT NOT NULL DEFAULT 'builtin'`.
- Produces `AclBackfillService(database, builtin_root).plan() -> dict`, `.apply() -> dict`, and `.rollback(run_id: str) -> dict`.
- `plan()` returns counts plus per-note non-sensitive decisions; `apply()` records original ACL columns before update; `rollback()` restores only records written by the named completed run.

- [ ] **Step 1: Write failing migration/backfill tests**

```python
def test_initialize_backfills_connector_prefixed_note_source_id(tmp_path):
    db = ProductDatabase(tmp_path / "app.db")
    # seed schema-7 note `dir-abc/finance/policy.md` and connector_syncs dir-abc
    db.initialize()
    assert db.fetch_one("SELECT source_id FROM notes WHERE note_id='n1'")["source_id"] == "dir-abc"

def test_backfill_unresolved_note_is_private_and_rollback_restores_original_acl(tmp_path):
    service = AclBackfillService(db, tmp_path / "knowledge")
    planned = service.plan()
    assert planned["unresolved_count"] == 1
    applied = service.apply()
    assert private_note["acl_public"] == 0
    assert json.loads(private_note["acl_json"])["backfill_reason"] == "source_unavailable"
    assert service.rollback(applied["run_id"])["restored"] == 1
```

- [ ] **Step 2: Run the focused test to prove it fails**

Run: `python -m pytest tests/test_acl_backfill_service.py -q --no-cov`

Expected: FAIL because `source_id` and `AclBackfillService` do not exist.

- [ ] **Step 3: Implement schema-8 migration and service**

Add `source_id` through `_ensure_columns`, then during initialization map an existing `vault_path` whose first segment equals a completed `connector_syncs.connector_id` to that connector; all other historical rows remain `builtin`. Add `acl_backfill_runs` and `acl_backfill_items` tables. Each applied item stores the old `workspace`, `department`, `acl_json`, and `acl_public`; rollback uses one transaction and rejects unknown/incomplete runs. Frontmatter is parsed only from a resolved file beneath the built-in root or its recorded connector root. Do not use a path outside its root. If no reliable source exists, write a private ACL such as:

```python
{"allow": [], "backfill_reason": "source_unavailable"}
```

Never mark an unresolved record public. Record only IDs, source IDs, reasons, and counts in audit data—not note bodies.

- [ ] **Step 4: Run focused tests and static check**

Run: `python -m pytest tests/test_acl_backfill_service.py -q --no-cov`

Run: `python -m ruff check src/infrastructure/database.py src/application/acl_backfill_service.py tests/test_acl_backfill_service.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py src/application/acl_backfill_service.py tests/test_acl_backfill_service.py
git commit -m "feat: add acl ownership backfill"
```

### Task 2: Scope scanning and prune to the owned source

**Files:**
- Modify: `src/application/vault_sync_service.py`
- Modify: `src/application/directory_connector_service.py`
- Modify: `src/application/mindgraph_index_service.py`
- Test: `tests/test_directory_connector.py`
- Create: `tests/test_source_ownership.py`

**Interfaces:**
- `VaultSyncService(..., source_id: str = "builtin")` stores that `source_id` in every upsert.
- `_prune_missing(current_paths)` deletes only `WHERE source_id=?`, using a single `db.connect()` transaction for relations and notes.
- `DirectoryConnectorService.sync()` calls its connector scanner with `source_id=connector_id`, enables prune only after its complete no-error scan, and forces an index rebuild when `pruned > 0`.
- `MindGraphIndexService` includes `source_id` in chunk metadata and resolves connector roots by `notes.source_id`, not by string guessing from an arbitrary path.

- [ ] **Step 1: Write failing cross-source and failure tests**

```python
def test_connector_prune_deletes_only_notes_owned_by_same_connector(tmp_path):
    # connector-a no longer has a.md; connector-b and builtin have matching paths
    result = connector_a.sync(source_a)
    assert result["pruned"] == 1
    assert note_ids(db, "connector-b") == {"b1"}
    assert note_ids(db, "builtin") == {"local1"}

def test_connector_partial_read_failure_prunes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(VaultSyncService, "_process_file", fail_for_one_file)
    result = connector.sync(source)
    assert result["errors"]
    assert result["pruned"] == 0

def test_prune_relation_and_note_deletes_are_atomic(tmp_path, monkeypatch):
    db.execute(
        "CREATE TRIGGER fail_owned_note_delete BEFORE DELETE ON notes "
        "WHEN OLD.note_id='owned' BEGIN SELECT RAISE(ABORT, 'forced failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        sync._prune_missing(set())
    assert db.fetch_one("SELECT 1 FROM notes WHERE note_id='owned'")
    assert db.fetch_one("SELECT 1 FROM note_relations WHERE relation_id='rel-1'")
```

- [ ] **Step 2: Run tests to prove they fail**

Run: `python -m pytest tests/test_source_ownership.py tests/test_directory_connector.py -q --no-cov`

Expected: FAIL because connector pruning is disabled and source ownership does not control deletion.

- [ ] **Step 3: Implement source-scoped upsert and safe prune**

Extend the note INSERT/UPDATE SQL so an upsert cannot silently change a note into another source. Reject a cross-source note-ID collision instead of overwriting it. Select candidate notes only from the active `source_id`, and delete associated relations and notes in one connection transaction. Connector sync must mark the run failed and retain all source rows if scanning has any error. Include source ID in index chunk metadata; keep the existing canonical-root and symlink containment checks.

- [ ] **Step 4: Run focused tests and static check**

Run: `python -m pytest tests/test_source_ownership.py tests/test_directory_connector.py tests/test_mindgraph_index_consistency.py -q --no-cov`

Run: `python -m ruff check src/application/vault_sync_service.py src/application/directory_connector_service.py src/application/mindgraph_index_service.py tests/test_source_ownership.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/application/vault_sync_service.py src/application/directory_connector_service.py src/application/mindgraph_index_service.py tests/test_directory_connector.py tests/test_source_ownership.py
git commit -m "feat: scope connector pruning by source"
```

### Task 3: Prefilter ACL before candidate generation

**Files:**
- Modify: `src/retrieval/types.py`
- Modify: `src/retrieval/dense.py`
- Modify: `src/retrieval/sparse.py`
- Modify: `src/retrieval/pipeline.py`
- Modify: `src/retrieval/mindgraph_pipeline.py`
- Test: `tests/test_retrieval.py`
- Create: `tests/test_retrieval_acl_prefilter.py`

**Interfaces:**
- Extend `DenseRetriever.search(query, top_k, allowed_chunk_ids: set[str] | None = None)` and the sparse protocol equivalently; `None` means no filtering.
- Add `RetrievalPipeline._visible_chunk_ids(access_scope) -> set[str] | None` and pass the same set to dense and sparse before fusion.
- `RetrievalTrace.candidate_counts` includes only visible candidates; `applied_filters` and warnings contain only count/reason metadata, never rejected chunk text or paths.

- [ ] **Step 1: Write failing prefilter regression tests**

```python
def test_acl_prefilter_prevents_private_chunks_from_dense_sparse_and_fusion():
    trace = pipeline.retrieve("travel policy", "hybrid", access_scope=finance_scope)
    assert ids(trace.dense_results) == {"finance-1"}
    assert ids(trace.sparse_results) == {"finance-1"}
    assert ids(trace.fused_results) == {"finance-1"}
    assert ids(trace.final_selected_chunks) == {"finance-1"}

def test_authorized_chunk_beyond_global_top_k_is_retrieved_after_prefilter():
    # twenty higher-scoring private chunks must not crowd out one authorized chunk
    assert ids(trace.final_selected_chunks) == {"allowed"}

def test_graph_expansion_keeps_acl_defense_in_depth():
    assert "private-related" not in ids(trace.final_selected_chunks)
```

- [ ] **Step 2: Run tests to prove they fail**

Run: `python -m pytest tests/test_retrieval_acl_prefilter.py -q --no-cov`

Expected: FAIL because dense and BM25 currently search the global index before ACL filtering.

- [ ] **Step 3: Implement minimal strict filtering**

`RetrievalPipeline` must inspect loaded chunk metadata using `chunk_acl_matches` before calling either retriever. FAISS may retrieve all positions then filter to `allowed_chunk_ids` before selecting `top_k`; this is intentional for the current SQLite + NumPy/FAISS baseline and avoids cross-tenant candidate competition. BM25 scores only allowed indices. Fusion and reranking receive only prefiltered candidates. Keep `_filter_by_access` and graph `_visible_for_trace` unchanged as defensive checks. Do not log rejected metadata beyond aggregate counts/reasons.

- [ ] **Step 4: Run focused tests and static check**

Run: `python -m pytest tests/test_retrieval.py tests/test_retrieval_acl_prefilter.py tests/test_access_control.py -q --no-cov`

Run: `python -m ruff check src/retrieval/types.py src/retrieval/dense.py src/retrieval/sparse.py src/retrieval/pipeline.py src/retrieval/mindgraph_pipeline.py tests/test_retrieval_acl_prefilter.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/retrieval/types.py src/retrieval/dense.py src/retrieval/sparse.py src/retrieval/pipeline.py src/retrieval/mindgraph_pipeline.py tests/test_retrieval.py tests/test_retrieval_acl_prefilter.py
git commit -m "feat: prefilter retrieval candidates by acl"
```

### Task 4: Expose repeatable backfill operations and end-to-end acceptance

**Files:**
- Create: `scripts/backfill_note_acl.py`
- Modify: `README.md`
- Modify: `docs/UPGRADE_PLAN.md`
- Test: `tests/test_acl_backfill_service.py`

**Interfaces:**
- CLI: `python scripts/backfill_note_acl.py --dry-run`, `--apply`, and `--rollback RUN_ID`; default is dry-run.
- `--apply` writes only via `AclBackfillService.apply()` and prints JSON counts without note bodies.
- `--rollback` requires an exact run ID and does not modify records from another run.

- [ ] **Step 1: Write failing CLI behavior tests**

```python
def test_acl_backfill_cli_defaults_to_dry_run(monkeypatch, capsys):
    main([])
    assert json.loads(capsys.readouterr().out)["mode"] == "dry_run"

def test_acl_backfill_cli_requires_exact_run_id_for_rollback():
    with pytest.raises(SystemExit):
        main(["--rollback"])
```

- [ ] **Step 2: Run the CLI test to prove it fails**

Run: `python -m pytest tests/test_acl_backfill_service.py -q --no-cov`

Expected: FAIL because the CLI does not exist.

- [ ] **Step 3: Implement CLI and document operations**

Use `argparse` mutually exclusive flags. Initialize the database using the existing runtime database location resolution, but never read `.env` in tests. Document the dry-run → reviewed apply → run-ID rollback procedure and explicitly state that production data requires an operator review of unresolved/private counts. Update roadmap statuses only to `Done` when all task acceptance tests pass; otherwise retain `Partial` and list the remaining concrete external verification.

- [ ] **Step 4: Run full acceptance**

Run: `python -m pytest -q`

Run: `python -m ruff check src evaluation tests`

Expected: full pytest PASS; record existing unrelated Ruff baseline separately, but no new violation in touched files.

- [ ] **Step 5: Commit**

```bash
git add scripts/backfill_note_acl.py README.md docs/UPGRADE_PLAN.md tests/test_acl_backfill_service.py
git commit -m "docs: document acl backfill operations"
```
