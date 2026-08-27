"""Fetch Basecamp handbook raw markdown.

原脚本仅试 raw.githubusercontent.com（本环境 DNS/连接不可达，必然失败）。
修复：raw 优先（并校验非 HTML 包装），失败后走 api.github.com contents API（base64）兜底。
"""
import base64
import json
import urllib.request
import pathlib

dst = pathlib.Path("data-sources/handbooks/basecamp/benefits-and-perks.md")
dst.parent.mkdir(parents=True, exist_ok=True)

RAW_URLS = [
    "https://raw.githubusercontent.com/basecamp/handbook/master/benefits-and-perks.md",
    "https://raw.githubusercontent.com/basecamp/handbook/main/benefits-and-perks.md",
]
API_URL = "https://api.github.com/repos/basecamp/handbook/contents/benefits-and-perks.md?ref=master"


def save(data: bytes) -> None:
    dst.write_bytes(data)
    print(f"saved {len(data)} bytes -> {dst}")


for url in RAW_URLS:
    try:
        print(f"try {url}")
        data = urllib.request.urlopen(url, timeout=20).read()
        if b"<!DOCTYPE" not in data[:200].lower() and len(data) > 100:
            save(data)
            print(data[:2000].decode(errors="ignore"))
            break
        print("suspect non-raw payload (HTML wrapper), skip")
    except Exception as e:
        print(f"failed {url}: {e}")
else:
    print("raw.githubusercontent.com unreachable -> fallback api.github.com")
    try:
        req = urllib.request.Request(
            API_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "mindgraph-fetch"},
        )
        meta = json.loads(urllib.request.urlopen(req, timeout=20).read())
        if meta.get("encoding") == "base64" and meta.get("content"):
            save(base64.b64decode(meta["content"]))
            print(meta["content"][:400])
        else:
            print("api unexpected payload:", str(meta)[:300])
    except Exception as e:
        print(f"api fallback failed: {e}")
