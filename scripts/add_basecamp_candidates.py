"""Append 4 synthetic Basecamp external-policy candidates (status=pending).

对齐 golden 中已 approved 的 Mattermost 4 条 schema（external_policy /
gold_vault_paths=external/public/basecamp.md / human-validated-from-data-source）。
approved 由用户人工审核后翻（诚信流程，与 Mattermost 一致）。
"""
import json
from pathlib import Path

DS = Path("evaluation/datasets/mindgraph_candidates_v2.jsonl")

CASES = [
    {
        "case_id": "ext-basecamp-medical-75-2026-08-27",
        "question": "37signals 美国员工的医疗保险（Blue Cross Blue Shield PPO）中，公司支付保费的百分之多少？",
        "category": "external_policy",
        "query_type": "external_policy",
        "split": "development",
        "expected_behavior": "answer",
        "gold_vault_paths": ["external/public/basecamp.md"],
        "required_facts": ["75% of the premium", "employee pays the other 25%"],
        "forbidden_facts": [],
        "dataset_version": "2.2.0",
        "label_source": "human-validated-from-data-source",
        "source": "basecamp/handbook benefits-and-perks.md",
        "validation_status": "pending",
        "expected_route": "factual",
        "graph_needed": False,
        "difficulty": "easy",
        "acl_context": {"roles": ["employee"]},
        "notes": "Source: data-sources/handbooks/basecamp/benefits-and-perks.md; Health Insurance - Medical Insurance section",
    },
    {
        "case_id": "ext-basecamp-expense-receipt-75-2026-08-27",
        "question": "37signals 员工使用 Airbase 进行费用报销时，单笔超过多少美元需要上传收据？",
        "category": "external_policy",
        "query_type": "external_policy",
        "split": "development",
        "expected_behavior": "answer",
        "gold_vault_paths": ["external/public/basecamp.md"],
        "required_facts": ["$75", "uploading receipts", "Airbase"],
        "forbidden_facts": [],
        "dataset_version": "2.2.0",
        "label_source": "human-validated-from-data-source",
        "source": "basecamp/handbook benefits-and-perks.md",
        "validation_status": "pending",
        "expected_route": "factual",
        "graph_needed": False,
        "difficulty": "easy",
        "acl_context": {"roles": ["employee"]},
        "notes": "Source: data-sources/handbooks/basecamp/benefits-and-perks.md; Expense Account section",
    },
    {
        "case_id": "ext-basecamp-401k-match-6pct-2026-08-27",
        "question": "37signals 美国员工的 401K 退休计划（Vanguard）中，公司按员工缴纳额的多少比例匹配、最高不超过工资的百分之几？",
        "category": "external_policy",
        "query_type": "external_policy",
        "split": "development",
        "expected_behavior": "answer",
        "gold_vault_paths": ["external/public/basecamp.md"],
        "required_facts": ["100% of what you contribute", "up to 6% of your salary", "Vanguard"],
        "forbidden_facts": [],
        "dataset_version": "2.2.0",
        "label_source": "human-validated-from-data-source",
        "source": "basecamp/handbook benefits-and-perks.md",
        "validation_status": "pending",
        "expected_route": "factual",
        "graph_needed": False,
        "difficulty": "medium",
        "acl_context": {"roles": ["employee"]},
        "notes": "Source: data-sources/handbooks/basecamp/benefits-and-perks.md; Retirement Plan section",
    },
    {
        "case_id": "ext-basecamp-family-leave-16wk-2026-08-27",
        "question": "37signals 员工成为新生儿主要照料者（primary caregiver）时，可享受多少周 100% 薪资的育儿假？",
        "category": "external_policy",
        "query_type": "external_policy",
        "split": "development",
        "expected_behavior": "answer",
        "gold_vault_paths": ["external/public/basecamp.md"],
        "required_facts": ["16 weeks", "100% pay", "primary caregiver"],
        "forbidden_facts": [],
        "dataset_version": "2.2.0",
        "label_source": "human-validated-from-data-source",
        "source": "basecamp/handbook benefits-and-perks.md",
        "validation_status": "pending",
        "expected_route": "factual",
        "graph_needed": False,
        "difficulty": "medium",
        "acl_context": {"roles": ["employee"]},
        "notes": "Source: data-sources/handbooks/basecamp/benefits-and-perks.md; Family Leave section",
    },
]


def main() -> None:
    rows = [json.loads(l) for l in DS.read_text(encoding="utf-8").splitlines() if l.strip()]
    existing = {r["case_id"] for r in rows}
    added = 0
    for c in CASES:
        if c["case_id"] in existing:
            print(f"skip (dup): {c['case_id']}")
            continue
        rows.append(c)
        added += 1
    DS.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    print(f"added={added} total={len(rows)}")
    print("pending external cases:", sum(1 for r in rows if r.get("query_type") == "external_policy" and r.get("validation_status") == "pending"))


if __name__ == "__main__":
    main()
