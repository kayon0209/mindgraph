# MindGraph Upgrade P0 Blockers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the upgrade branch's fail-open authentication and the directory connector's cross-source deletion and arbitrary source-file mutation risks without changing the database schema.

**Architecture:** Establish one mandatory principal resolver for protected API routers and represent anonymous access with an explicit public-only ACL scope. Contain connector risk by disabling generic global pruning and source-file ID injection for connector scans, and require configured canonical allowed roots before any directory sync. Source-aware deletion remains deferred until a separately approved schema migration adds source ownership.

**Tech Stack:** Python 3.11+, FastAPI dependencies, Pydantic Settings, SQLite, pytest.

**Spec:** `docs/UPGRADE_PLAN.md` UG-003 through UG-006, constrained by the 2026-08-24 upgrade audit findings.

## Global Constraints

- Behavior changes must have a failing regression test before production code changes.
- `AUTH_MODE=api_key` and `AUTH_MODE=bearer` fail closed on missing, invalid, or failed credentials.
- `AUTH_MODE=off` remains an explicit local-development bypass.
- Anonymous/demo access may see only records marked public.
- Connector sync must never prune records outside its owned source; until source ownership exists, connector pruning is disabled.
- Connector sync must not mutate source Markdown files.
- Connector paths must resolve below an explicitly configured allowed root.
- No database schema or data migration is included in this plan.

---

### Task 1: Fail-closed principal resolution and public-only ACL

**Files:**
- Modify: `tests/test_access_control.py`
- Modify: `tests/test_oidc.py`
- Modify: `src/api/auth.py`
- Modify: `src/application/access_control.py`

**Interfaces:**
- Produces: `get_required_principal(request: Request) -> dict`
- Produces: `require_authenticated(principal: dict = Depends(get_required_principal)) -> dict`
- Produces: `public_access_scope() -> dict[str, Any]`
- Changes: `resolve_access_scope(request)` returns `None` only for explicit `AUTH_MODE=off`; otherwise anonymous/demo is public-only and enterprise modes reject invalid credentials.

- [ ] **Step 1: Write failing tests** proving missing and invalid credentials raise `AuthenticationError`, an anonymous public-only scope denies private notes and permits public notes, and a valid OIDC principal satisfies role checks.
- [ ] **Step 2: Run focused tests** with `python -m pytest tests/test_access_control.py tests/test_oidc.py -q --no-cov`; expected failures must show current anonymous `None` scope and API-key-only role resolution.
- [ ] **Step 3: Implement the minimal resolver** so OIDC Bearer and API keys share one mandatory path and authentication failures are not swallowed.
- [ ] **Step 4: Implement explicit public-only scope** and keep `AUTH_MODE=off` as the sole ACL bypass.
- [ ] **Step 5: Re-run focused tests** and verify all pass.

### Task 2: Protect all API routers and restrict connector administration

**Files:**
- Create: `tests/test_auth_boundaries.py`
- Modify: `src/api/main.py`
- Modify: `src/api/routes/connectors.py`

**Interfaces:**
- Consumes: `require_authenticated` and the unified `require_role` from Task 1.
- Changes: health remains public; all other `/api/v1` routers require a valid principal when auth is enabled.
- Changes: directory sync requires the `admin` role instead of generic `write`.

- [ ] **Step 1: Write failing API tests** for unauthenticated notes, MCP, governance mutation, and connector sync requests in `AUTH_MODE=api_key`; each must return 401. Add a role test proving `write` cannot invoke directory sync.
- [ ] **Step 2: Run the focused API test file** and verify current routes fail open.
- [ ] **Step 3: Register health separately** and attach `Depends(require_authenticated)` to protected routers; change connector sync to `require_role("admin")`.
- [ ] **Step 4: Re-run focused and existing API/auth tests** and verify pass.

### Task 3: Contain connector source ownership and path risks

**Files:**
- Modify: `tests/test_directory_connector.py`
- Modify: `src/application/vault_sync_service.py`
- Modify: `src/application/directory_connector_service.py`
- Modify: `src/api/dependencies.py`
- Modify: `src/infrastructure/settings.py`

**Interfaces:**
- Changes: `VaultSyncService.scan_vault(*, prune_missing: bool = True) -> VaultScanResult`.
- Changes: `DirectoryConnectorService(..., allowed_roots: tuple[Path, ...])` resolves and validates all sources.
- Changes: connector-created `VaultSyncService` uses `write_ids=False` and `scan_vault(prune_missing=False)`.
- Produces: `Settings.connector_allowed_root_list -> tuple[Path, ...]` parsed from `CONNECTOR_ALLOWED_ROOTS`.

- [ ] **Step 1: Write failing tests** proving connector B does not delete connector A or the built-in vault, source Markdown bytes are unchanged, paths outside allowed roots are rejected, and a symlink escape is rejected when supported by the platform.
- [ ] **Step 2: Run connector tests** and verify they fail because global prune, ID injection, or unrestricted paths are still active.
- [ ] **Step 3: Add the optional prune flag** to VaultSyncService without changing existing vault-sync default behavior.
- [ ] **Step 4: Make connector scans non-mutating and non-pruning**, validate canonical paths against configured roots, and fail closed when no roots are configured.
- [ ] **Step 5: Wire settings into the service container** and re-run connector tests.

### Task 4: Regression verification and documentation truthfulness

**Files:**
- Modify: `docs/UPGRADE_PLAN.md`
- Modify: `.env.example`

**Interfaces:**
- Documents: `CONNECTOR_ALLOWED_ROOTS` and the temporary no-prune connector behavior.
- Changes plan status: enterprise ACL/connectors/OIDC cannot remain unconditional `Done`; describe the contained state and remaining schema-migration work.

- [ ] **Step 1: Run focused suites** for auth, ACL, OIDC, MCP, connector, and API boundaries.
- [ ] **Step 2: Run project fatal lint gate**: `python -m ruff check src scripts tests --select F821,F822,F823,E902`.
- [ ] **Step 3: Run the full non-slow test suite** with coverage disabled for fast diagnosis, then the configured CI pytest command if the suite is green.
- [ ] **Step 4: Update documentation and `.env.example`** only after behavior is verified.
- [ ] **Step 5: Inspect `git diff --check` and `git status`**, ensuring no generated data, secrets, or environment files are included.

## Self-Review

- Spec coverage: Tasks 1-2 close the P0 authentication boundary; Task 3 contains both connector deletion and arbitrary-write risks; Task 4 makes capability claims match reality.
- Deliberate gap: source-aware deletion, ACL backfill, and note source ownership require a database schema/data migration and are excluded until explicitly approved.
- Type consistency: mandatory principal and public-only scope are defined in Task 1 and consumed by Task 2; connector allowed roots are defined in Task 3 and wired through settings.
- Placeholder scan: no deferred implementation placeholder exists inside this plan; the excluded migration is named explicitly as a separate authorization boundary.
