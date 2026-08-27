# Public Data Fact Review — 2026-08-27

## Scope

This review covers the two public handbook pages that were actually converted to Markdown and indexed. Basecamp was not included because the downloaded GitHub HTML was an error page and the raw file could not be fetched in the current network environment.

## Review ledger

| Source | Facts reviewed | Verification method | License record | Decision |
|---|---:|---|---|---|
| Mattermost Handbook — How to get paid | 4 Golden cases / 13 required facts | Direct comparison against downloaded HTML and converted Markdown; page sections and effective date preserved | `data-sources/LICENSES.md` | Approved for development-only evaluation |
| GitLab Handbook — Expenses | Content indexed; no Golden case promoted | Direct source download retained; converted Markdown capped at 80,000 chars and marked as truncated when applicable | `data-sources/LICENSES.md` | Indexed as public reference; not promoted to Golden until section-level review |
| Basecamp Handbook — Benefits and Perks | 0 | Downloaded HTML was a GitHub error/login page; raw GitHub fetch failed due to network DNS | `data-sources/LICENSES.md` | Not indexed; pending valid raw source |

## Mattermost fact ledger

1. United States and Canada expense reimbursement uses Airbase and ACH or wire payment; reimbursement is no later than the 15th or the end of the month.
2. United Kingdom expense reimbursement uses Airbase and requires banking information registration.
3. Germany expense reimbursement uses Airbase and requires banking information registration.
4. Rest-of-world contractors/vendors require a primary banking institution; payment currency follows the agreement; the account must be in the contracting individual/entity name; Airbase is used for invoice processing and reimbursement.

Each fact is represented in the four `ext-mattermost-*` Golden records with source path `external/public/mattermost.md` and source URL in frontmatter.

## Restrictions

- These facts describe Mattermost's published handbook, not a generic or current company policy.
- The source page can change; `ingest_date`, URL, original staged HTML, converted Markdown, and content hashes are retained.
- The external subset is a development signal, not a statistically independent production benchmark.
- GitLab and Basecamp content must not be promoted to approved Golden records without section-level fact review.
