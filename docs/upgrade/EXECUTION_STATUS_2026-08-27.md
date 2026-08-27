# Public-data roadmap execution status — 2026-08-27

## Completed locally

- Mattermost, GitLab, and Basecamp public handbook content converted to Markdown under `knowledge/external/public/`.
- Added six additional public pages: two Mattermost, two GitLab, and two Basecamp pages.
- Public-page manifest with source URL, license, byte length, and SHA-256 is stored at `data-sources/handbooks/public-pages-manifest.json`.
- All public sources synced into SQLite; 404 notes total, with six new pages pending then indexed successfully.
- Current FAISS index: `mg-20260827T092533Z-db09b80a`; 581 chunks total, 500 public-handbook chunks.
- Incremental build reused 472 embeddings and generated 109 new embeddings.
- Frozen golden dataset remains 54 approved cases, version `2.2.0`.
- Generated 10 new public-handbook candidate cases at `evaluation/datasets/public_handbook_candidates_v1.jsonl`; all are `validation_status=pending` and pass the candidate contract. They were not promoted to approved Golden.
- Frozen local hybrid results after corpus expansion: Recall@5 0.8551, MRR 0.7717, Precision@5 0.2217.
- Graph ON/OFF: Recall@5 0.8551 in both modes; MRR 0.7717 vs 0.7742. The ON run enabled Graph for all 46 answer cases, observed expansion in 35 cases, and added 73 candidates (activation rate 0.7609). This is an observed local result, not a completed stratified Phase 5 gate or a default-enable recommendation.
- External approved subset is now 8 cases (Mattermost 4 + Basecamp 4), with Recall@5 0.50 in both graph modes.
- Mattermost-only diagnostic: BM25 0/4, Dense 1/4, Hybrid 1/4. The low score cannot be attributed solely to the Chinese embedding model.
- Full regression: 282 passed, 2 skipped. Critical Ruff checks passed.

## Not completed because local prerequisites are absent

- Tesseract executable and `pytesseract` are not available, so pure-image OCR was not executed.
- Docker Desktop engine is not running, so Keycloak was not started and JWK rotation E2E was not executed.
- k6 executable is not installed and no personal deployment URL was supplied, so deployment load testing was not executed.

## Reproduction entry points

```text
python scripts/fetch_public_handbook_pages.py
python scripts/ingest_public_handbooks.py
python scripts/generate_public_candidates.py
python scripts/ingest_knowledge.py
python scripts/build_index.py
python scripts/run_external_eval2.py
python -m pytest -q --no-cov
ruff check src/ scripts/ tests/ --config pyproject.toml --select F821,F822,F823,E902
```

No real company data or production claims are made by this report.
