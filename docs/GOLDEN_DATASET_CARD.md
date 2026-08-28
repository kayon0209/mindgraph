# MindGraph Golden Dataset Card

- Dataset: `evaluation/datasets/mindgraph_golden_v2.jsonl`
- Version: `2.4.0`
- Current size: 90 approved cases
- Canonical SHA-256: `17f92116e47b70b0f54a60858b8dc82edcd4a199fd1410c3bd8f9b1dceedd95a`
- Source: public synthetic `demo-vault/` plus documented public handbook sources
- Runtime/database independence: required
- Splits: `development` and `regression`
- Legacy snapshot: `evaluation/datasets/mindgraph_golden.jsonl`（`2.1.0`）仅供遗留消融入口引用，不作为新样本评审基线

The dataset covers versioning, supersession, approvals, limits, exceptions, cross-policy cases, case reasoning, no-answer, ambiguity, multi-condition, exact facts, ACL-restricted, synonym/abbreviation, graph-needed, and graph-control cases. It now meets the planned per-category minimum coverage (2026-08-27 expansion, +36 cases); it remains a local development/regression set and does not by itself support statistical significance or production gate claims.

Version `2.4.0` identifies the checked-in 90-case snapshot. Historical commits used `2.2.0` for 12-, 50-, and 54-case snapshots and `2.3.0` for the 54-case snapshot, so version-only historical results remain ambiguous; use the dataset SHA-256 to attribute every new run.

## Label rules

- `answer` requires at least one source path and required facts.
- `abstain` has no gold source path and is evaluated by answer/refusal metrics, not retrieval recall.
- Candidate records remain in `mindgraph_candidates_v2.jsonl` with `source=generated_candidate` and `validation_status=pending`.
- New approved cases must be reviewed without access to system output or retrieval ranking.

## Release condition

The Phase 1 target is 60–80 approved cases with the planned minimum coverage. The checked-in `2.4.0` snapshot (90 cases) meets the planned per-category minimums; reports must still show the version, sample size, SHA-256 and per-category breakdown, and must avoid threshold or significance claims beyond local development/regression scope.
