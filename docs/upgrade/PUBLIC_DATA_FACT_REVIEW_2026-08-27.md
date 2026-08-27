# Public Data Fact Review — 2026-08-27

## Scope

This review covers the three public handbook pages that were actually converted to Markdown and indexed. Basecamp was initially blocked (raw.githubusercontent.com DNS unreachable), then unblocked by fetching clean raw Markdown through the `api.github.com` contents API and retaining it at `data-sources/handbooks/basecamp/benefits-and-perks.md`.

## Review ledger

| Source | Facts reviewed | Verification method | License record | Decision |
|---|---:|---|---|---|
| Mattermost Handbook — How to get paid | 4 Golden cases / 13 required facts | Direct comparison against downloaded HTML and converted Markdown; page sections and effective date preserved | `data-sources/LICENSES.md` | Approved for development-only evaluation |
| GitLab Handbook — Expenses | Content indexed; no Golden case promoted | Direct source download retained; converted Markdown capped at 80,000 chars and marked as truncated when applicable | `data-sources/LICENSES.md` | Indexed as public reference; not promoted to Golden until section-level review |
| Basecamp Handbook — Benefits and Perks | Content indexed; no Golden case promoted | Clean raw Markdown fetched via `api.github.com` (13,718 bytes), verified non-HTML; converted Markdown retained | `data-sources/LICENSES.md` | Indexed as public reference; not promoted to Golden until section-level review |

## Mattermost fact ledger

1. United States and Canada expense reimbursement uses Airbase and ACH or wire payment; reimbursement is no later than the 15th or the end of the month.
2. United Kingdom expense reimbursement uses Airbase and requires banking information registration.
3. Germany expense reimbursement uses Airbase and requires banking information registration.
4. Rest-of-world contractors/vendors require a primary banking institution; payment currency follows the agreement; the account must be in the contracting individual/entity name; Airbase is used for invoice processing and reimbursement.

Each fact is represented in the four `ext-mattermost-*` Golden records with source path `external/public/mattermost.md` and source URL in frontmatter.

## Basecamp fact ledger (indexed, not yet in Golden)

1. US medical insurance is provided through Blue Cross Blue Shield PPO; the company pays 75% of the premium and the employee pays 25%.
2. US dental and vision insurance are provided through MetLife; the company pays 100% of the premium.
3. Staff outside the US can request reimbursement for 75% of out-of-pocket health insurance payments on their monthly invoice, capped at the amount 37signals pays per US employee per month.
4. 37signals offers a $400,000 life insurance/AD&D policy to all staff through MetLife.

These facts come from `knowledge/external/public/basecamp.md` and are verifiable against the raw source at `data-sources/handbooks/basecamp/benefits-and-perks.md`. They are recorded here as indexed public reference only; none are promoted to approved Golden records without section-level fact review.

## Restrictions

- These facts describe the published public handbooks, not a generic or current company policy.
- The source pages can change; `ingest_date`, URL, original staged HTML/raw Markdown, converted Markdown, and content hashes are retained.
- The external subset is a development signal, not a statistically independent production benchmark.
- GitLab and Basecamp content must not be promoted to approved Golden records without section-level fact review.
