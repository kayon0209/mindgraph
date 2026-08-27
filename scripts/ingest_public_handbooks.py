"""Convert public handbook HTML to Markdown and place under knowledge/external/public/.

Idempotency: preserves existing mindgraph_id from the output file (if it already
has one) and always emits status: active, so re-running is safe.
"""
import re
import hashlib
from pathlib import Path
from bs4 import BeautifulSoup
import html2text

SRC = {
    "mattermost": (
        Path("data-sources/handbooks/mattermost/how-to-get-paid.html"),
        "Mattermost Handbook - How to get paid",
        "https://handbook.mattermost.com/operations/finance/staff-member-expenses/how-to-get-paid",
        "Mattermost",
    ),
    "mattermost-spend-company-money": (
        Path("data-sources/handbooks/mattermost/how-to-spend-company-money.md"),
        "Mattermost Handbook - How to spend company money",
        "https://handbook.mattermost.com/operations/finance/staff-member-expenses/how-to-spend-company-money.md",
        "Mattermost",
    ),
    "mattermost-corporate-card": (
        Path("data-sources/handbooks/mattermost/corporate-credit-card-policy.md"),
        "Mattermost Handbook - Corporate credit card policy",
        "https://handbook.mattermost.com/operations/finance/staff-member-expenses/corporate-credit-card-policy.md",
        "Mattermost",
    ),
    "gitlab-travel-expense": (
        Path("data-sources/handbooks/gitlab/expenses-additional.html"),
        "GitLab Handbook - Global Travel and Expense Policy (additional capture)",
        "https://handbook.gitlab.com/handbook/finance/expenses/",
        "GitLab",
    ),
    "gitlab-remote-work": (
        Path("data-sources/handbooks/gitlab/all-remote.html"),
        "GitLab Handbook - All-Remote Work",
        "https://handbook.gitlab.com/handbook/company/culture/all-remote/",
        "GitLab",
    ),
    "basecamp-holidays": (
        Path("data-sources/handbooks/basecamp/holidays.md"),
        "Basecamp Handbook - Holidays",
        "https://github.com/basecamp/handbook/blob/master/holidays.md",
        "Basecamp",
    ),
    "basecamp-remote-work": (
        Path("data-sources/handbooks/basecamp/remote-work.md"),
        "Basecamp Handbook - Remote Work",
        "https://github.com/basecamp/handbook/blob/master/remote-work.md",
        "Basecamp",
    ),
    "gitlab": (
        Path("data-sources/handbooks/gitlab/expenses.html"),
        "GitLab Handbook - Expenses",
        "https://handbook.gitlab.com/handbook/finance/expenses",
        "GitLab",
    ),
    "basecamp": (
        Path("data-sources/handbooks/basecamp/benefits-and-perks.md"),
        "Basecamp Handbook - Benefits and Perks",
        "https://github.com/basecamp/handbook/blob/master/benefits-and-perks.md",
        "Basecamp",
    ),
}
DST = Path("knowledge/external/public")
DST.mkdir(parents=True, exist_ok=True)


def _existing_mid(out: Path) -> str | None:
    """Extract mindgraph_id from an existing output file, if present."""
    if not out.exists():
        return None
    m = re.search(r"mindgraph_id:\s*([0-9a-f]{32})", out.read_text(encoding="utf-8", errors="ignore"))
    return m.group(1) if m else None


def html_to_md(html: str, title: str, url: str, org: str, existing_id: str | None) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    h = html2text.HTML2Text()
    h.body_width = 0
    h.ignore_links = False
    h.ignore_images = False
    h.unicode_snob = True
    h.single_line_break = False
    body = h.handle(str(main))
    body = re.sub(r"\n{3,}", "\n\n", body)
    body = re.sub(r"\[Skip to [^\]]*\]\([^\)]*\)", "", body)
    body = body.strip()
    if len(body) > 80000:
        body = body[:80000] + "\n\n...[truncated for indexing]..."

    mid = existing_id or hashlib.sha256(body.encode()).hexdigest()[:32]
    front = f"""---
mindgraph_id: {mid}
title: "{title}"
source_url: {url}
source_org: {org}
license: "CC-BY-SA-4.0 (GitLab/Mattermost) / MIT (Basecamp) - see data-sources/LICENSES.md"
ingest_date: 2026-08-27
ingest_tool: scripts/ingest_public_handbooks.py
original_file: data-sources/handbooks/{org.lower()}/...
status: active
---

# {title}

> Source: {url}
> Ingested: 2026-08-27 | Original HTML preserved at data-sources/handbooks/

"""
    return front + body + "\n"


def md_front(title: str, url: str, org: str, orig: str, body: str, existing_id: str | None) -> str:
    mid = existing_id or hashlib.sha256(body.encode()).hexdigest()[:32]
    orig_posix = str(Path(orig).as_posix())  # normalize backslashes on Windows
    return f"""---
mindgraph_id: {mid}
title: "{title}"
source_url: {url}
source_org: {org}
license: "CC-BY-SA-4.0 (GitLab/Mattermost) / MIT (Basecamp) - see data-sources/LICENSES.md"
ingest_date: 2026-08-27
ingest_tool: scripts/ingest_public_handbooks.py
original_file: {orig_posix}
status: active
---

# {title}

> Source: {url}
> Ingested: 2026-08-27 | Original preserved at data-sources/handbooks/

"""


for key, (path, title, url, org) in SRC.items():
    text = path.read_text(encoding="utf-8", errors="ignore")
    out = DST / f"{key}.md"
    existing_id = _existing_mid(out)

    if path.suffix.lower() in (".md", ".markdown"):
        body = text.strip()
        if body.startswith("# "):
            body = body.split("\n", 1)[1].strip()
        md = md_front(title, url, org, str(path), body, existing_id) + body + "\n"
    else:
        md = html_to_md(text, title, url, org, existing_id)

    out.write_text(md, encoding="utf-8")
    print(f"{key} -> {out} len={len(md)} sha={hashlib.sha256(md.encode()).hexdigest()[:8]}")

for p in sorted(DST.rglob("*")):
    if p.is_file():
        print(f"DST {p} {p.stat().st_size}")
