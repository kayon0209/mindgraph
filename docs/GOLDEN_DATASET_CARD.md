# MindGraph Golden Dataset Card

- Dataset: `evaluation/datasets/mindgraph_golden_v2.jsonl`
- Version: `2.2.0`
- Current size: 12 approved human-authored cases
- Source: public synthetic `demo-vault/`
- Runtime/database independence: required
- Splits: `development` and `regression`
- Legacy snapshot: `evaluation/datasets/mindgraph_golden.jsonl`（`2.1.0`）仅供遗留消融入口引用，不作为新样本评审基线

The dataset covers versioning, supersession, approvals, limits, exceptions, cross-policy cases, case reasoning, no-answer, and ambiguity. It is suitable for deterministic contract and regression checks, but its current size is not sufficient for statistical claims or a production Graph gate.

## Label rules

- `answer` requires at least one source path and required facts.
- `abstain` has no gold source path and is evaluated by answer/refusal metrics, not retrieval recall.
- Candidate records remain in `mindgraph_candidates_v2.jsonl` with `source=generated_candidate` and `validation_status=pending`.
- New approved cases must be reviewed without access to system output or retrieval ranking.

## Release condition

The Phase 1 target is at least 30 approved cases before advancing, with a preferred target of 60–80 and coverage across ACL-restricted, graph-needed, graph-not-needed, conflict, versioned, no-answer, and synonym/abbreviation cases. Until then, reports must explicitly show the sample size and avoid threshold or significance claims.
