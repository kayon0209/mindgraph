# Public-data roadmap execution status — 2026-08-27

## Completed locally

- Mattermost, GitLab, and Basecamp public handbook content converted to Markdown under `knowledge/external/public/`.
  - Basecamp was fetched via the `api.github.com` contents API fallback (raw.githubusercontent.com DNS is unreachable
    in this environment); raw file retained at `data-sources/handbooks/basecamp/benefits-and-perks.md` (13,718 bytes),
    verified as clean markdown, not an HTML wrapper.
- All three Markdown sources synced into the local SQLite notes table (`policy_status=active` for all three) and indexed in FAISS.
- Current index: `mg-20260827T074106Z-84956855`; 303 chunks total, 222 public-handbook chunks.
- Golden dataset contains 50 validated records, including four Mattermost public-source cases.
- Public fact review and license ledger recorded in `docs/upgrade/PUBLIC_DATA_FACT_REVIEW_2026-08-27.md` and `data-sources/LICENSES.md`.
- Reproducible retrieval report generated in `docs/upgrade/RETRIEVAL_PUBLIC_DATA_REPORT_2026-08-27.md`.
- Overall local hybrid results: Recall@5 0.8889, MRR 0.8143, Precision@5 0.2333.
- External Mattermost subset: Recall@5 0.25, MRR 0.25. This is a recorded failure signal; no quality claim is made.
- Graph ON/OFF had no difference on this corpus: Recall@5 0.8889 and MRR 0.8143 in both modes.
- Full regression: 282 passed, 2 skipped. Critical Ruff checks passed.
- `scripts/ingest_public_handbooks.py` made idempotent: regenerating preserves the existing `mindgraph_id` and emits
  `status: active`, so re-running produces byte-identical files (verified `git diff` clean for gitlab/mattermost).
  `scripts/fetch_basecamp_raw.py` retries raw.githubusercontent.com first, then falls back to the `api.github.com`
  contents API.

## Not completed because local prerequisites are absent

- Tesseract executable and `pytesseract` are not available, so pure-image OCR was not executed.
- Docker Desktop engine is not running, so Keycloak was not started and JWK rotation E2E was not executed.
- k6 executable is not installed and no personal deployment URL was supplied, so deployment load testing was not executed.

## Reproduction entry points (verified 2026-08-27)

```text
python scripts/fetch_basecamp_raw.py          # raw 优先，失败自动走 api.github.com 兜底
python scripts/ingest_public_handbooks.py
python scripts/ingest_knowledge.py
python scripts/build_index.py
python scripts/run_external_eval2.py
python -m pytest -q --no-cov
ruff check src/ scripts/ tests/ --config pyproject.toml --select F821,F822,F823,E902
```

No real company data or production claims are made by this report.
