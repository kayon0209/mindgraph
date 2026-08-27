# Public Data Sources — Licenses & Attribution

> This file records every public-data file staged under `data-sources/` and
> ingested under `knowledge/external/`. Only files with an explicit public
> license or public-handbook publication are eligible for indexing.

| # | File (staged) | Ingested as | Source URL | Publisher | License | Ingest date | Hash (sha256 8) |
|---|---|---|---|---|---|---|---|
| 1 | `data-sources/handbooks/mattermost/how-to-get-paid.html` | `knowledge/external/public/mattermost.md` | https://handbook.mattermost.com/operations/finance/staff-member-expenses/how-to-get-paid | Mattermost, Inc. | **CC BY-SA 4.0** (Mattermost Handbook — https://handbook.mattermost.com) | 2026-08-27 | `04dd4b03` |
| 2 | `data-sources/handbooks/gitlab/expenses.html` | `knowledge/external/public/gitlab.md` | https://handbook.gitlab.com/handbook/finance/expenses | GitLab Inc. | **CC BY-SA 4.0** (GitLab Handbook — https://handbook.gitlab.com) | 2026-08-27 | `65d6cca1` |
| 3 | `data-sources/handbooks/basecamp/benefits-and-perks.md` | `knowledge/external/public/basecamp.md` | https://github.com/basecamp/handbook/blob/master/benefits-and-perks.md (fetched via `api.github.com` contents API) | Basecamp / 37signals | **MIT** (https://github.com/basecamp/handbook) | 2026-08-27 | `e0e48490` |
| 4 | `data-sources/ocr/chinese-gov/guowuyuan-gongbao-202524.pdf` | staging only (OCR target, not indexed as knowledge) | https://www.gov.cn (State Council Gazette) | Gov.cn | Public government gazette (no private data) | 2026-08-27 | — |
| 5 | `data-sources/ocr/chinese-gov/ziran-ziyuan-tingsheng-guiding.pdf` | staging only | https://www.mnr.gov.cn | Ministry of Natural Resources | Public regulation (no private data) | 2026-08-27 | — |

Notes:

- `data-sources/handbooks/basecamp/basecamp-benefits.html` is the GitHub blob view of the same page (323 KB, contains the real content embedded in the logged-out page). The clean raw Markdown above was fetched through the `api.github.com` contents API because raw.githubusercontent.com is unreachable in this environment.
- All ingested public markdown files keep `source_url` in frontmatter and are reproducible from `data-sources/` originals via `scripts/ingest_public_handbooks.py`.
- No private / company-internal data is included. All facts are verifiable against the public URLs above.
