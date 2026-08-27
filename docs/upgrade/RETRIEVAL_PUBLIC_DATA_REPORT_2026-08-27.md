# Public Data Retrieval Report — 2026-08-27

## Corpus

- Knowledge root: `knowledge/`
- Indexed public sources: Mattermost Handbook, GitLab Handbook, and Basecamp / 37signals Handbook
- Additional pages: two per publisher, captured with `scripts/fetch_public_handbook_pages.py`
- Current FAISS index: `mg-20260827T092533Z-db09b80a`
- Total indexed chunks: 581
- Public handbook chunks: 500
- Frozen Golden dataset: 54 approved cases, version `2.2.0`
- New candidate dataset: 10 pending cases, version `2.3.0`; not included in frozen results
- Evaluation strategy: hybrid
- Top-k: 5
- Embedding: local `BAAI/bge-small-zh-v1.5`, dimension 512

## Frozen Golden results after corpus expansion

| Mode | Recall@5 | MRR | Precision@5 |
|---|---:|---:|---:|
| Graph off | 0.8551 | 0.7717 | 0.2217 |
| Graph on | 0.8551 | 0.7742 | 0.2217 |

The corpus expansion changed the baseline from the previous 303-chunk run. Recall and precision decreased because more public chunks create additional retrieval competition. The files are:

- `evaluation/results/retrieval_external_graph_off.json`
- `evaluation/results/retrieval_external_graph_on.json`

## Approved external subset

The approved `external_policy` subset contains 8 cases: 4 Mattermost and 4 Basecamp.

| Mode | Cases | Recall@5 | MRR |
|---|---:|---:|---:|
| Graph off | 8 | 0.5000 | not aggregated in console output |
| Graph on | 8 | 0.5000 | not aggregated in console output |

## Mattermost-only diagnostic

| Strategy | Cases | Recall@5 | MRR | Precision@5 |
|---|---:|---:|---:|---:|
| BM25 | 4 | 0.0000 | 0.0000 | 0.0000 |
| Dense | 4 | 0.2500 | 0.1250 | 0.0500 |
| Hybrid | 4 | 0.2500 | 0.2500 | 0.0500 |

BM25 also fails all four Mattermost cases. Therefore the low score is not proven to be caused only by the Chinese BGE model; Chinese-to-English lexical mismatch and query-to-chunk alignment are also plausible contributors. Multilingual embeddings or translated query variants remain reasonable next experiments, not completed fixes.

## Graph interpretation

Graph ON/OFF does not show Recall@5 gain in this run, while MRR changes from 0.7717 to 0.7742. The OFF report records zero Graph-enabled cases. The ON report enables Graph for all 46 answer cases, observes expansion in 35 cases, and adds 73 candidates (activation rate 0.7609). The database has 6 typed confirmed governance relations, so the same small set of relations is activated broadly. These results prove the switch executed, but they do not satisfy the Phase 5 release gate: the required seven-subset matrix, citation/ACL checks, and latency/cost gate are still absent.

## Candidate governance

The 10 new public cases remain in `evaluation/datasets/public_handbook_candidates_v1.jsonl` with `validation_status=pending` and `source=generated_candidate`. Human source review is required before promotion to the approved Golden dataset.
