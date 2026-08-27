# MindGraph Golden Dataset Card

- Dataset: `evaluation/datasets/mindgraph_golden_v2.jsonl`
- Version: `2.3.0`
- Current size: 54 approved cases
- Canonical SHA-256: `4e5f2d1ef452833e564405e1388aee05faf10447fe685268990b024fa58ec8b6`
- Source: public synthetic `demo-vault/` plus documented public handbook sources
- Runtime/database independence: required
- Splits: `development` and `regression`
- Legacy snapshot: `evaluation/datasets/mindgraph_golden.jsonl`（`2.1.0`）仅供遗留消融入口引用，不作为新样本评审基线

The dataset covers versioning, supersession, approvals, limits, exceptions, cross-policy cases, case reasoning, no-answer, and ambiguity. It is suitable for deterministic contract and regression checks, but it remains below the 60–80 target and does not meet the planned per-category minimums. It is not sufficient for statistical claims or a production Graph gate.

Version `2.3.0` identifies the checked-in 54-case snapshot. Historical commits used `2.2.0` for 12-, 50-, and 54-case snapshots, so version-only historical results remain ambiguous; use the dataset SHA-256 to attribute every new run.

## Label rules

- `answer` requires at least one source path and required facts.
- `abstain` has no gold source path and is evaluated by answer/refusal metrics, not retrieval recall.
- Candidate records remain in `mindgraph_candidates_v2.jsonl` with `source=generated_candidate` and `validation_status=pending`.
- New approved cases must be reviewed without access to system output or retrieval ranking.

## Release condition

The Phase 1 target is 60–80 approved cases with the planned minimum coverage across ACL-restricted, graph-needed, graph-not-needed, conflict, versioned, no-answer, and synonym/abbreviation cases. Until then, reports must explicitly show the version, sample size, SHA-256 and coverage gaps, and must avoid threshold or significance claims.
