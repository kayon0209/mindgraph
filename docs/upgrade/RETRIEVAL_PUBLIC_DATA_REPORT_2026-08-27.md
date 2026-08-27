# Public Data Retrieval Report — 2026-08-27

## Corpus

- Knowledge root: `knowledge/`
- Indexed public sources: Mattermost Handbook, GitLab Handbook, and Basecamp / 37signals Handbook
- Basecamp source: clean raw Markdown fetched through the `api.github.com` contents API fallback and retained at `data-sources/handbooks/basecamp/benefits-and-perks.md`
- Current FAISS index: `mg-20260827T074106Z-84956855`
- Total indexed chunks: 303
- Public handbook chunks: 222
- Golden dataset: 50 cases, version `2.2.0`
- Evaluation strategy: hybrid
- Top-k: 5
- Embedding: local `BAAI/bge-small-zh-v1.5`, dimension 512

## Results

| Mode | Recall@5 | MRR | Precision@5 |
|---|---:|---:|---:|
| Graph off | 0.8889 | 0.8143 | 0.2333 |
| Graph on | 0.8889 | 0.8143 | 0.2333 |

The serialized results are:

- `evaluation/results/retrieval_external_graph_off.json`
- `evaluation/results/retrieval_external_graph_on.json`

## Public Mattermost subset

| Mode | Cases | Recall@5 | MRR |
|---|---:|---:|---:|
| Graph off | 4 | 0.2500 | 0.2500 |
| Graph on | 4 | 0.2500 | 0.2500 |

This low external-subset score is a useful failure signal, not a reason to claim success. Three of four Chinese queries did not retrieve the English Mattermost source in the current local embedding/query configuration. The ROW query retrieved it because it contains more distinctive entity terms.

## Failure classification

Overall missed cases:

- `MG-ENT-002`: version/supersession evidence not fully in Top-5
- `MG-ENT-010`: case reasoning evidence not fully in Top-5
- `cand-graph-5-38de0a2abc`: version relation query
- `ext-mattermost-pay-usca-2026-08-27`
- `ext-mattermost-pay-uk-2026-08-27`
- `ext-mattermost-pay-de-2026-08-27`

## Interpretation

- The report is reproducible and generated against the local indexed corpus after Basecamp was added.
- It is not a production-quality benchmark because the 50 cases include development cases and the external subset is only four cases.
- The graph switch produced no measurable difference on this corpus; Graph should remain opt-in.
- Adding Basecamp increased the corpus to 303 chunks but did not alter the existing Mattermost external failure signal.
- The external handbook results justify adding multilingual aliases or translated query variants before promoting the external corpus to a quality gate.
