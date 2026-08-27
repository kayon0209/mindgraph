# Public Data Sources — Licenses & Attribution

> This file records every public-data file staged under `data-sources/` and
> ingested under `knowledge/external/`. Only files with an explicit public
> license or public-handbook publication are eligible for indexing.

| # | File (staged) | Ingested as | Source URL | Publisher | License | Ingest date | Hash (sha256 8) |
|---|---|---|---|---|---|---|---|
| 1 | `data-sources/handbooks/mattermost/how-to-get-paid.html` | `knowledge/external/public/mattermost.md` | https://handbook.mattermost.com/operations/finance/staff-member-expenses/how-to-get-paid | Mattermost, Inc. | **CC BY-SA 4.0** (Mattermost Handbook — https://handbook.mattermost.com) | 2026-08-27 | `04dd4b03` |
| 2 | `data-sources/handbooks/gitlab/expenses.html` | `knowledge/external/public/gitlab.md` | https://handbook.gitlab.com/handbook/finance/expenses | GitLab Inc. | **CC BY-SA 4.0** (GitLab Handbook — https://handbook.gitlab.com) | 2026-08-27 | `65d6cca1` |
| 3 | `data-sources/ocr/chinese-gov/guowuyuan-gongbao-202524.pdf` | staging only (OCR target, not indexed as knowledge) | https://www.gov.cn (State Council Gazette) | Gov.cn | Public government gazette (no private data) | 2026-08-27 | — |
| 4 | `data-sources/ocr/chinese-gov/ziran-ziyuan-tingsheng-guiding.pdf` | staging only | https://www.mnr.gov.cn | Ministry of Natural Resources | Public regulation (no private data) | 2026-08-27 | — |

Removed:
- `data-sources/handbooks/basecamp/basecamp-benefits.html` — GitHub HTML error page (no valid content), removed before ingestion. Will re-fetch from `https://raw.githubusercontent.com/basecamp/handbook/...` when network allows.

Notes:
- Basecamp Handbook itself is **MIT** (https://github.com/basecamp/handbook) — eligible when raw markdown is available.
- All ingested public markdown files keep `source_url` in frontmatter and are reproducible from `data-sources/` originals via `scripts/ingest_public_handbooks.py`.
- No private / company-internal data is included. All facts are verifiable against the public URLs above.
