"""Convert public handbook HTML to Markdown and place under knowledge/external/public/."""
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

def html_to_md(html: str, title: str, url: str, org: str) -> str:
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
    front = f"""---
title: "{title}"
source_url: {url}
source_org: {org}
license: "CC-BY-SA-4.0 (GitLab/Mattermost) / MIT (Basecamp) - see data-sources/LICENSES.md"
ingest_date: 2026-08-27
ingest_tool: scripts/ingest_public_handbooks.py
original_file: data-sources/handbooks/{org.lower()}/...
---

# {title}

> Source: {url}
> Ingested: 2026-08-27 | Original HTML preserved at data-sources/handbooks/

"""
    return front + body + "\n"

def md_front(title: str, url: str, org: str, orig: str, body: str) -> str:
    mid = hashlib.sha256(body.encode()).hexdigest()[:32]
    return f"""---
mindgraph_id: {mid}
title: "{title}"
source_url: {url}
source_org: {org}
license: "CC-BY-SA-4.0 (GitLab/Mattermost) / MIT (Basecamp) - see data-sources/LICENSES.md"
ingest_date: 2026-08-27
ingest_tool: scripts/ingest_public_handbooks.py
original_file: {orig}
---

# {title}

> Source: {url}
> Ingested: 2026-08-27 | Original preserved at data-sources/handbooks/

"""


for key, (path, title, url, org) in SRC.items():
    text = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix.lower() in (".md", ".markdown"):
        # clean raw markdown source (e.g. Basecamp benefits-and-perks.md fetched via api.github.com)
        body = text.strip()
        if body.startswith("# "):
            body = body.split("\n", 1)[1].strip()  # raw 自带 H1 由 front 标题承接
        md = md_front(title, url, org, str(path), body) + body + "\n"
    else:
        md = html_to_md(text, title, url, org)
    out = DST / f"{key}.md"
    out.write_text(md, encoding="utf-8")
    print(f"{key} -> {out} len={len(md)} sha={hashlib.sha256(md.encode()).hexdigest()[:8]}")

for p in sorted(DST.rglob("*")):
    if p.is_file():
        print(f"DST {p} {p.stat().st_size}")
