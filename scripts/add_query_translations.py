"""Add human-translated English `query_translations` to external_policy golden cases.

Provenance: manually translated by the project owner on 2026-08-27 against the
English handbook sources (mattermost.md / basecamp.md). Purpose: enable the
cross-language query-variant retrieval path (RRF merge in retrieval layer).
"""
import json
from pathlib import Path

GOLDEN = Path("evaluation/datasets/mindgraph_golden_v2.jsonl")

TRANSLATIONS = {
    "ext-mattermost-pay-usca-2026-08-27": [
        "How are Mattermost US and Canada staff members paid and reimbursed for expenses, and by when?",
    ],
    "ext-mattermost-pay-uk-2026-08-27": [
        "How are Mattermost UK staff members paid and reimbursed for expenses?",
    ],
    "ext-mattermost-pay-de-2026-08-27": [
        "How are Mattermost Germany staff members paid and reimbursed for expenses?",
    ],
    "ext-mattermost-pay-row-2026-08-27": [
        "For Mattermost contractors or vendors outside the US, Canada, UK and Germany, what bank account and currency requirements apply to reimbursement payments?",
    ],
    "ext-basecamp-medical-75-2026-08-27": [
        "What percentage of the medical insurance premium does 37signals pay for US employees under the Blue Cross Blue Shield PPO plan?",
    ],
    "ext-basecamp-expense-receipt-75-2026-08-27": [
        "For 37signals expense reimbursement via Airbase, above what purchase amount must employees upload receipts?",
    ],
    "ext-basecamp-401k-match-6pct-2026-08-27": [
        "In the 37signals 401K plan (Vanguard), what percentage does the company match of employee contributions, up to what percent of salary?",
    ],
    "ext-basecamp-family-leave-16wk-2026-08-27": [
        "How many weeks of parental leave at 100% pay can a 37signals employee take as the primary caregiver of a new child?",
    ],
}


def main() -> None:
    rows = [json.loads(l) for l in GOLDEN.read_text(encoding="utf-8").splitlines() if l.strip()]
    updated = 0
    for r in rows:
        if r.get("query_type") != "external_policy":
            continue
        if r["case_id"] in TRANSLATIONS and not r.get("query_translations"):
            r["query_translations"] = TRANSLATIONS[r["case_id"]]
            r["notes"] = (r.get("notes", "") + " | query_translations: human-translated EN (2026-08-27)").strip(" |")
            updated += 1
    GOLDEN.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    print(f"updated={updated} total={len(rows)}")
    missing = [r["case_id"] for r in rows if r.get("query_type") == "external_policy" and not r.get("query_translations")]
    print("external without translations:", missing)


if __name__ == "__main__":
    main()
