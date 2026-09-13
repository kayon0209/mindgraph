# MindGraph Golden Dataset Card

- Dataset: `evaluation/datasets/mindgraph_golden_v2.jsonl`
- Version: `2.4.0`
- Current size: 90 approved cases
- Canonical SHA-256: `17f92116e47b70b0f54a60858b8dc82edcd4a199fd1410c3bd8f9b1dceedd95a`
- **Digest method: `canonical-jsonl-source-line-v1`**（权威口径，见下节；引用 SHA 时必须同时写出 method）
- Source: public synthetic `demo-vault/` plus documented public handbook sources
- Runtime/database independence: required
- Splits: `development` and `regression`
- Legacy snapshot: `evaluation/datasets/mindgraph_golden.jsonl`（`2.1.0`）仅供遗留消融入口引用，不作为新样本评审基线

The dataset covers versioning, supersession, approvals, limits, exceptions, cross-policy cases, case reasoning, no-answer, ambiguity, multi-condition, exact facts, ACL-restricted, synonym/abbreviation, graph-needed, and graph-control cases. It now meets the planned per-category minimum coverage (2026-08-27 expansion, +36 cases); it remains a local development/regression set and does not by itself support statistical significance or production gate claims.

Version `2.4.0` identifies the checked-in 90-case snapshot. Historical commits used `2.2.0` for 12-, 50-, and 54-case snapshots and `2.3.0` for the 54-case snapshot, so version-only historical results remain ambiguous; use the dataset SHA-256 to attribute every new run.

## Digest 口径（必须显式声明，否则跨平台不可比）

同一个文件在不同实现下会算出**至少四个不同的 SHA-256**。引用任一摘要时必须同时给出 method，否则「数值对得上」是偶然。

| method | 算法 | 本文件的值 |
|---|---|---|
| `canonical-jsonl-source-line-v1`（权威） | `_jsonl_records()` 注入 `_source_line` → `json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)` → `\n` 连接 + 尾换行 → UTF-8 → SHA-256 | `17f92116e47b70b0f54a60858b8dc82edcd4a199fd1410c3bd8f9b1dceedd95a` |
| 裸 canonical JSONL（不注入 `_source_line`） | 同上但缺 `_source_line` | `86edc6482f5aeefe240702ebbbe7716a5e4d1617500f35a97f3ba7f2c3e1ee00`（**非权威，勿用**） |
| raw bytes（工作区） | 直接对文件字节哈希；`core.autocrlf=true` 下为 CRLF | `d6f77544ab0f9d6328209d0cb5e509ed0ec123fc5882e5c652b591ade2abf70d`（**平台相关**） |
| git blob | 对 `HEAD:<path>` 的 blob 字节哈希（LF） | `b32eb4936e4f011a8383350170200a18177c920e2088ffff468e8b604cf56da9` |

- 权威口径由 `evaluation/mindgraph_retrieval_eval.py` 的 `dataset_sha256()` 实现；`scripts/freeze_baseline.py` 把它写成基线里的 `dataset.sha256_method`。
- `evaluation/manifest.py` 的 `sha256_file()` 属 **raw bytes** 口径，在 `core.autocrlf=true` 的 Windows 与 Linux CI 上会得出不同值 —— 跨机比对 baseline 前必须先核对 method。

## Label rules

- `answer` requires at least one source path and required facts.
- `abstain` has no gold source path and is evaluated by answer/refusal metrics, not retrieval recall.
- Candidate records remain in `mindgraph_candidates_v2.jsonl` with `source=generated_candidate` and `validation_status=pending`.
- New approved cases must be reviewed without access to system output or retrieval ranking.

## Release condition

The Phase 1 target is 60–80 approved cases with the planned minimum coverage. The checked-in `2.4.0` snapshot (90 cases) meets the planned per-category minimums; reports must still show the version, sample size, SHA-256 **and its digest method**, and per-category breakdown, and must avoid threshold or significance claims beyond local development/regression scope.
