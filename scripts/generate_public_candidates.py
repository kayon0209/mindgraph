"""Generate pending evaluation candidates from indexed public handbook pages."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOT = ROOT / "knowledge/external/public"
OUT = ROOT / "evaluation/datasets/public_handbook_candidates_v1.jsonl"


def case_id(question: str) -> str:
    return "pub-" + hashlib.sha256(question.encode()).hexdigest()[:12]


def add(rows: list[dict], question: str, path: str, facts: list[str], source: str, translation: str) -> None:
    rows.append({
        "case_id": case_id(question),
        "question": question,
        "category": "external_policy",
        "query_type": "external_policy",
        "split": "development",
        "expected_behavior": "answer",
        "gold_vault_paths": [path],
        "required_facts": facts,
        "forbidden_facts": [],
        "dataset_version": "2.3.0",
        "label_source": "generated-from-public-source",
        "source": "generated_candidate",
        "source_detail": source,
        "validation_status": "pending",
        "expected_route": "factual",
        "graph_needed": False,
        "difficulty": "medium",
        "acl_context": {"roles": ["employee"]},
        "notes": "Generated from public handbook content; human validation required before promotion.",
        "evaluation_date": "2026-08-27",
        "historical_vault_paths": [],
        "query_translations": [translation],
    })


def main() -> None:
    rows: list[dict] = []
    add(rows, "Mattermost 员工购买超过 1000 美元的物品后，离职时对该物品有什么要求？", "external/public/mattermost-spend-company-money.md", ["1000 USD or over", "company property", "return the item(s) if you leave"], "Mattermost Handbook - How to spend company money", "For a Mattermost purchase costing 1000 USD or more, what happens when the employee leaves?")
    add(rows, "Mattermost 员工提交费用报告应使用什么系统，且费用最迟不能超过多久？", "external/public/mattermost-spend-company-money.md", ["Use Airbase to file your expense report", "Do not submit expenses older than three months"], "Mattermost Handbook - How to spend company money", "Which system should Mattermost staff use for expense reports, and how old may an expense be at most?")
    add(rows, "Mattermost 公司信用卡交易需要保留什么凭证，并上传到哪里？", "external/public/mattermost-corporate-card.md", ["corresponding receipt", "description of the business expense", "Upload the receipts into Airbase"], "Mattermost Handbook - Corporate credit card policy", "What documentation is required for Mattermost corporate card transactions and where should receipts be uploaded?")
    add(rows, "Mattermost 发现公司信用卡有欺诈性扣款时，员工应立即联系谁并向谁提交争议？", "external/public/mattermost-corporate-card.md", ["Finance", "American Express or Airbase", "send a copy of the dispute to Finance"], "Mattermost Handbook - Corporate credit card policy", "What should a Mattermost cardholder do after discovering a fraudulent charge?")
    add(rows, "GitLab 的 Global Travel and Expense Policy 适用于哪些人员？", "external/public/gitlab-travel-expense.md", ["Global Travel and Expense Policy"], "GitLab Handbook - Global Travel and Expense Policy", "Who is covered by GitLab's Global Travel and Expense Policy?")
    add(rows, "GitLab 费用政策中，超过 90 天提交的费用会怎样？", "external/public/gitlab-travel-expense.md", ["90 days"], "GitLab Handbook - Global Travel and Expense Policy", "What happens to a GitLab expense submitted after 90 days?")
    add(rows, "GitLab 的 All-Remote 工作方式强调员工在哪些地点工作？", "external/public/gitlab-remote-work.md", ["all-remote", "remote"], "GitLab Handbook - All-Remote Work", "What does GitLab's all-remote work model mean about where employees work?")
    add(rows, "GitLab 远程工作政策对异步沟通有什么要求？", "external/public/gitlab-remote-work.md", ["asynchronous"], "GitLab Handbook - All-Remote Work", "What does GitLab's remote-work guidance say about asynchronous communication?")
    add(rows, "Basecamp Handbook 的 Getting Started 页面建议新员工首先了解什么？", "external/public/basecamp-holidays.md", ["Getting Started"], "Basecamp Handbook - Holidays", "What does the Basecamp Handbook Holidays page recommend new employees learn first?")
    add(rows, "Basecamp 的 Remote Work 页面描述了团队如何协作？", "external/public/basecamp-remote-work.md", ["Remote Work"], "Basecamp Handbook - Remote Work", "How does the Basecamp Handbook describe remote work?")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} pending candidates -> {OUT}")


if __name__ == "__main__":
    main()
