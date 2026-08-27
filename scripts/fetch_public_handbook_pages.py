"""Fetch additional public handbook pages with source validation."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import re
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [
    {
        "key": "mattermost-spend-company-money",
        "org": "Mattermost",
        "title": "Mattermost Handbook - How to spend company money",
        "url": "https://handbook.mattermost.com/operations/finance/staff-member-expenses/how-to-spend-company-money.md",
        "raw_url": "https://handbook.mattermost.com/operations/finance/staff-member-expenses/how-to-spend-company-money.md",
        "format": "markdown",
        "path": ROOT / "data-sources/handbooks/mattermost/how-to-spend-company-money.md",
        "license": "CC-BY-SA-4.0",
    },
    {
        "key": "mattermost-corporate-card",
        "org": "Mattermost",
        "title": "Mattermost Handbook - Corporate credit card policy",
        "url": "https://handbook.mattermost.com/operations/finance/staff-member-expenses/corporate-credit-card-policy.md",
        "raw_url": "https://handbook.mattermost.com/operations/finance/staff-member-expenses/corporate-credit-card-policy.md",
        "format": "markdown",
        "path": ROOT / "data-sources/handbooks/mattermost/corporate-credit-card-policy.md",
        "license": "CC-BY-SA-4.0",
    },
    {
        "key": "gitlab-travel-expense",
        "org": "GitLab",
        "title": "GitLab Handbook - Global Travel and Expense Policy",
        "url": "https://handbook.gitlab.com/handbook/finance/expenses/",
        "raw_url": "https://handbook.gitlab.com/handbook/finance/expenses/",
        "format": "html",
        "path": ROOT / "data-sources/handbooks/gitlab/expenses-additional.html",
        "license": "CC-BY-SA-4.0",
    },
    {
        "key": "gitlab-remote-work",
        "org": "GitLab",
        "title": "GitLab Handbook - All-Remote Work",
        "url": "https://handbook.gitlab.com/handbook/company/culture/all-remote/",
        "raw_url": "https://handbook.gitlab.com/handbook/company/culture/all-remote/",
        "format": "html",
        "path": ROOT / "data-sources/handbooks/gitlab/all-remote.html",
        "license": "CC-BY-SA-4.0",
    },
    {
        "key": "basecamp-holidays",
        "org": "Basecamp",
        "title": "Basecamp Handbook - Holidays",
        "url": "https://github.com/basecamp/handbook/blob/master/getting-started.md",
        "repo": "basecamp/handbook",
        "git_path": "getting-started.md",
        "ref": "master",
        "format": "markdown",
        "path": ROOT / "data-sources/handbooks/basecamp/holidays.md",
        "license": "MIT",
    },
    {
        "key": "basecamp-remote-work",
        "org": "Basecamp",
        "title": "Basecamp Handbook - Remote Work",
        "url": "https://github.com/basecamp/handbook/blob/master/how-we-work.md",
        "repo": "basecamp/handbook",
        "git_path": "how-we-work.md",
        "ref": "master",
        "format": "markdown",
        "path": ROOT / "data-sources/handbooks/basecamp/remote-work.md",
        "license": "MIT",
    },
]


def fetch(url: str, headers: dict[str, str] | None = None) -> bytes:
    request = urllib.request.Request(url, headers=headers or {"User-Agent": "mindgraph-public-data-fetch"})
    return urllib.request.urlopen(request, timeout=30).read()


def get_source(item: dict) -> bytes:
    if item.get("repo"):
        api = f"https://api.github.com/repos/{item['repo']}/contents/{item['git_path']}?ref={item['ref']}"
        payload = json.loads(fetch(api, {"Accept": "application/vnd.github+json", "User-Agent": "mindgraph-public-data-fetch"}))
        if payload.get("encoding") != "base64" or not payload.get("content"):
            raise RuntimeError(f"GitHub API returned no base64 content for {item['git_path']}")
        return base64.b64decode(payload["content"])
    data = fetch(item["raw_url"])
    if item.get("format") == "html":
        return data
    if data.lstrip().lower().startswith((b"<!doctype html", b"<html")):
        raise RuntimeError(f"HTML wrapper returned for {item['raw_url']}")
    return data


def main() -> None:
    manifest = []
    for item in SOURCES:
        data = get_source(item)
        if len(data) < 100:
            raise RuntimeError(f"source unexpectedly short: {item['key']} ({len(data)} bytes)")
        item["path"].parent.mkdir(parents=True, exist_ok=True)
        item["path"].write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        manifest.append({
            "key": item["key"],
            "title": item["title"],
            "org": item["org"],
            "url": item["url"],
            "path": str(item["path"].relative_to(ROOT)).replace("\\", "/"),
            "license": item["license"],
            "bytes": len(data),
            "sha256": digest,
        })
        print(f"{item['key']}: {len(data)} bytes sha256={digest[:8]}")
    (ROOT / "data-sources/handbooks/public-pages-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
