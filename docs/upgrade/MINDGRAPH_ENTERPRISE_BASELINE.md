# MindGraph Enterprise Baseline Audit

> Phase 0 baseline snapshot for the Cursor upgrade plan. This document records the current repository reality without changing business behavior.

## 1. Snapshot metadata

- Repository: `d:/demo/mindgraph`
- Current branch: `main`
- HEAD commit: `9180917 Fix MindGraph retrieval evaluation contract`
- Remote: `origin git@github.com:kayon0209/mindgraph.git`
- Audit date: `2026-08-26`
- Host OS: Windows 10.0.26220
- Python: `3.12.0`
- Ruff: `0.16.3`
- Node: `v24.19.0`
- pnpm: `11.19.0` via `corepack`

## 2. Working tree status at audit start

The repository had pre-existing untracked entries before any Phase 0 work:

- `.release-fix/`
- `MindGraph_Cursor升级执行计划.md` (now removed after audit; it was an execution-plan artifact, not a product asset)

No tracked file modifications were present at audit start.

## 3. Current default entry points

### HTTP API

- Main app: `src/api/main.py`
- API prefix: `/api/v1`
- Public health route: `/api/v1/health`
- Auth-protected routes: chat, connectors, knowledge, evaluation, feedback, governance, mindgraph chat, mindgraph readonly, MCP
- Root response returns service metadata for `MindGraph`

### Chat flow

Current runtime chain for normal question answering:

1. `src/api/routes/chat.py` or `src/api/routes/mindgraph_chat.py`
2. `src/application/chat_service.py`
3. `src/application/adaptive_retrieval_router.py`
4. `src/application/query_understanding.py`
5. `src/infrastructure/retrieval_factory.py`
6. `src/retrieval/pipeline.py`
7. `src/retrieval/mindgraph_pipeline.py`
8. `src/application/policy_conflict_service.py`
9. LLM provider from `src/infrastructure/*_provider.py`
10. Citation serialization and persistence in `query_logs`

### Retrieval routing behavior

- `AdaptiveRetrievalRouter.decide()` is the current deterministic router.
- It emits `RetrievalRouteDecision` with `mode`, `route`, `selected_strategy`, `graph_enabled`, `search_query`, `reasons`, and cost/latency tiers.
- `QueryUnderstandingService.plan()` post-processes the route to produce deterministic query variants.
- The router does not use a learned model; it uses explicit term heuristics, document-title detection, structured-term detection, and a clarification heuristic for compound questions.

### Retrieval and graph flow

- `create_mindgraph_retrieval_pipeline()` constructs the current active retrieval path.
- `RetrievalPipeline.retrieve()` applies:
  - strategy validation,
  - dense and/or sparse retrieval,
  - RRF fusion for hybrid paths,
  - lifecycle filters (`document_status`, `effective_date`, `expiration_date`),
  - category filters,
  - ACL filtering through `application.access_control.chunk_acl_matches`,
  - optional rerank fallback/degradation handling.
- `MindGraphRetrievalPipeline.retrieve()` adds a one-hop expansion layer only when graph is enabled and the strategy is `hybrid` or `hybrid_rerank`.
- Graph expansion reads only `note_relations.status='confirmed'` via `MindGraphGraphStore.related_note_ids()`.
- `PolicyConflictService.find_for_policy_keys()` runs after citation creation and stops generation on overlapping valid versions.

### MCP flow

- HTTP MCP route: `src/api/routes/mcp.py`
- JSON-RPC handler: `src/mcp_server.py`
- Tool execution path:
  1. request parse
  2. tool lookup
  3. access-scope resolution
  4. tool-specific ACL filtering and audit write
  5. JSON response serialization
- Tools are read-only and currently include list/get/search/evaluation/relation inspection.

### Evaluation flow

Two evaluation stacks exist today:

1. Legacy general runner:
   - `evaluation/runner.py`
   - Uses `rag_engine.py`, `config.py`, `evaluation/test_cases.py`, and `evaluation/scorer.py`
   - Still present for historical regression compatibility

2. Deterministic MindGraph golden-set evaluator:
   - `evaluation/mindgraph_retrieval_eval.py`
   - `tests/test_mindgraph_retrieval_eval.py`
   - Uses JSONL golden cases and returns structural retrieval metrics without invoking models

## 4. Current data and dataset baselines

### Public / frozen evaluation data

- `evaluation/datasets/mindgraph_golden_v2.jsonl`: 12 approved cases（版本 `2.2.0`）
- `evaluation/datasets/mindgraph_golden.jsonl`: 12 cases（`2.1.0` 遗留快照，仅 legacy 消融入口引用）
- `evaluation/datasets/mindgraph_routing.jsonl`: 12 cases
- `evaluation/datasets/expense_qa_v1.jsonl`: 34 cases

### Schema and validation baseline

- Golden schema: `evaluation/datasets/mindgraph_golden_v2.schema.json`
- Current tests enforce:
  - case-id uniqueness,
  - dataset-version consistency,
  - answer/abstain evidence rules,
  - stable JSONL loading error messages,
  - abstain cases excluded from retrieval scoring.

## 5. Current retrieval and governance baseline

### Retrieval strategies

- Valid strategies in `src/retrieval/pipeline.py`:
  - `dense`
  - `bm25`
  - `hybrid`
  - `hybrid_rerank`
- Default rerank is disabled unless configured.
- `RETRIEVAL_CANDIDATE_COUNT`, `RETRANK_TOP_N`, and `RRF_CONSTANT` are read from environment settings.

### ACL and lifecycle behavior

- ACL trimming is performed before answer generation.
- Current filters consider:
  - workspace / department / user scope,
  - `document_status`,
  - `effective_from` / `effective_to`,
  - `policy_status` and policy conflict checks.
- Confirmed graph relations do not bypass ACL or lifecycle checks.

## 6. Verification results for this baseline

### Python checks

- `python -m ruff check src scripts tests --select F821,F822,F823,E902`
  - Result: PASS
- `python -m pytest`
  - Result: PASS
  - Summary: `248 passed, 2 skipped`
  - Coverage: `65.66%`
- `python scripts/validate_mindgraph_offline.py`
  - Result: PASS
  - Offline validation succeeded with fake embeddings and fake LLM in a temporary workspace

### Web checks

- `corepack enable; corepack prepare pnpm@11.19.0 --activate`
  - Result: PASS
- `cd web; pnpm install --frozen-lockfile`
  - Result: PASS
- `cd web; pnpm typecheck`
  - Result: PASS
- `cd web; pnpm test`
  - Result: PASS
  - Summary: `4 files`, `12 tests`
- `cd web; pnpm build`
  - Result: PASS

## 7. Call-chain map

### 7.1 API request path

```text
HTTP request
→ FastAPI app (`src/api/main.py`)
→ route module (`src/api/routes/*.py`)
→ auth/access scope (`src/api/auth.py`, `src/api/dependencies.py`)
→ application service (`src/application/chat_service.py`, etc.)
→ retrieval pipeline (`src/infrastructure/retrieval_factory.py`)
→ retrieval core (`src/retrieval/pipeline.py`)
→ optional graph expansion (`src/retrieval/mindgraph_pipeline.py`)
→ conflict check (`src/application/policy_conflict_service.py`)
→ LLM provider
→ citations / trace / persistence
```

### 7.2 Query understanding and routing

```text
ChatRequest
→ AdaptiveRetrievalRouter.decide()
→ QueryUnderstandingService.plan()
→ pipeline.retrieve()
```

### 7.3 ACL and graph ordering

```text
retrieve candidates
→ lifecycle filters
→ ACL filter
→ confirmed graph expansion
→ answer generation
```

### 7.4 MCP path

```text
JSON-RPC request
→ /api/v1/mcp or stdio server
→ handle_jsonrpc()
→ _call_tool()
→ ACL scope resolution
→ tool-specific query
→ audit log
```

### 7.5 Evaluation path

```text
Golden JSONL
→ dataset validation
→ deterministic retrieval evaluation
→ summary metrics / failure attribution
```

## 8. Differences versus the upgrade plan

This repository already exceeds some Phase 0 assumptions in the upgrade plan:

- `src/application/adaptive_retrieval_router.py` already exists and is exercised by tests.
- `src/retrieval/mindgraph_pipeline.py` already exists and performs confirmed one-hop expansion.
- `src/mcp_server.py` already exposes read-only tools.
- `evaluation/mindgraph_retrieval_eval.py` already provides deterministic contract tests for the golden set.
- The current golden set size is 12, not the larger enterprise target in later phases.

The plan’s Phase 0 requirement to inspect current structure still applies, but the implementation work for later phases should extend these existing modules rather than create parallel replacements.

## 9. Phase mapping for later work

| Planned phase | Existing files to extend first |
|---|---|
| Phase 1 Golden governance | `evaluation/datasets/*`, `evaluation/mindgraph_retrieval_eval.py`, `tests/test_mindgraph_retrieval_eval.py` |
| Phase 2 unified eval | `evaluation/runner.py`, `evaluation/mindgraph_retrieval_eval.py`, `evaluation/routing_eval.py`, `evaluation/answer_eval.py`, `scripts/run_ablation.py` |
| Phase 3 adaptive router | `src/application/adaptive_retrieval_router.py`, `src/application/query_understanding.py`, `tests/test_adaptive_router.py`, `evaluation/routing_eval.py` |
| Phase 4 typed graph | `src/application/mindgraph_graph_store.py`, `src/retrieval/mindgraph_pipeline.py`, graph-related tests |
| Phase 5 graph gate | `scripts/run_ablation.py`, evaluation tests, retrieval tests |
| Phase 6 evidence UI | `web/src/*`, API routes serving citations / traces / graph evidence |
| Phase 7 MCP hardening | `src/mcp_server.py`, `src/api/routes/mcp.py`, ACL and audit tests |
| Phase 8 docs and release pack | `README.md`, `README.zh-CN.md`, `docs/*` |

## 10. Baseline conclusion

- The repository is in a healthy baseline state for Phase 0.
- No business behavior was changed during this audit.
- The current implementation already contains the expected core chains for API, adaptive routing, hybrid retrieval, confirmed graph expansion, MCP, and deterministic evaluation.
- The next phase should only begin after explicit user confirmation.
