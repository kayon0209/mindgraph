#!/usr/bin/env python3
"""Seed MindGraph note_relations with realistic demo relationships.

Reads real notes from data/product/product.sqlite3, derives relationships
from shared frontmatter tags and title keywords, and writes a small set of
confirmed + proposed relations (plus one intentional conflict sample) so the
knowledge-graph and link-suggestion pages have content.

Idempotent: clears previous seed-v1 rows before inserting.
"""
import sqlite3
import json
import os
import re
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "product", "product.sqlite3")
MODEL_VERSION = "seed-v1"
PROMPT_VERSION = "seed-v1"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_tags(fm_json: str) -> set:
    try:
        fm = json.loads(fm_json) if fm_json else {}
    except Exception:
        return set()
    tags = fm.get("tags") or fm.get("tag") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in re.split(r"[,\s]+", tags) if t.strip()]
    out = set()
    for t in tags:
        if isinstance(t, str):
            out.add(t.strip().lstrip("#").lower())
    return out


def title_keywords(title: str) -> set:
    # latin/number tokens length >= 2 (English terms, years, etc.)
    toks = re.findall(r"[a-zA-Z][a-zA-Z0-9]{1,}", title or "")
    digits = re.findall(r"\d{4}", title or "")  # years
    kw = {t.lower() for t in toks if len(t) >= 2}
    kw.update(digits)
    return kw


def load_notes(db: str):
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT note_id, vault_path, title, frontmatter_json "
        "FROM notes WHERE index_status='ready'"
    ).fetchall()
    con.close()
    notes = []
    for nid, vpath, title, fm in rows:
        notes.append(
            {
                "note_id": nid,
                "vault_path": vpath,
                "title": title or "",
                "tags": parse_tags(fm),
                "kw": title_keywords(title or ""),
            }
        )
    return notes


def main():
    if not os.path.exists(DB):
        print(f"[abort] DB not found: {DB}")
        sys.exit(1)

    notes = load_notes(DB)
    print(f"[load] {len(notes)} ready notes")

    by_tag: dict = {}
    by_kw: dict = {}
    for n in notes:
        for t in n["tags"]:
            by_tag.setdefault(t, []).append(n)
        for k in n["kw"]:
            by_kw.setdefault(k, []).append(n)

    confirmed = []  # (a, b, relation_type, confidence, evidence)
    proposed = []
    seen_pairs = set()  # unordered pairs already used (avoid duplicate edges)

    # confirmed: shared frontmatter tags
    tag_pairs = 0
    for tag, ns in by_tag.items():
        if len(ns) < 2:
            continue
        ns_sorted = sorted(ns, key=lambda x: x["note_id"])
        a, b = ns_sorted[0], ns_sorted[1]
        if a["note_id"] == b["note_id"]:
            continue
        pair = frozenset({a["note_id"], b["note_id"]})
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        confirmed.append(
            (a["note_id"], b["note_id"], "related_to", 0.85 + (tag_pairs % 5) * 0.02, f"共享标签 #{tag}")
        )
        tag_pairs += 1
        if tag_pairs >= 15:
            break

    # proposed: shared title keywords (exclude already-used pairs, both directions)
    kw_pairs = 0
    for kw, ns in by_kw.items():
        if len(ns) < 2 or kw in ("the", "and", "for", "with"):
            continue
        ns_sorted = sorted(ns, key=lambda x: x["note_id"])
        a, b = ns_sorted[0], ns_sorted[1]
        if a["note_id"] == b["note_id"]:
            continue
        pair = frozenset({a["note_id"], b["note_id"]})
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        proposed.append((a["note_id"], b["note_id"], "related_to", 0.65, f"标题均含关键词 {kw}"))
        kw_pairs += 1
        if kw_pairs >= 10:
            break

    # conflict sample: propose the reverse of an existing confirmed pair
    if confirmed:
        a, b, *_ = confirmed[0]
        proposed.append((b, a, "related_to", 0.6, "反向关系（冲突样例）"))

    # write (idempotent)
    con = sqlite3.connect(DB)
    con.execute("DELETE FROM note_relations WHERE model_version=?", (MODEL_VERSION,))
    ts = now_iso()
    rid = 0

    def insert(source, target, rtype, conf, evidence, status, resolved):
        nonlocal rid
        rid += 1
        relation_id = f"seed-{rid:04d}"
        con.execute(
            """INSERT INTO note_relations
               (relation_id, source_note_id, target_note_id, relation_type, direction, status,
                evidence_chunk_id, confidence, model_version, prompt_version, proposed_at, resolved_at, resolved_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                relation_id,
                source,
                target,
                rtype,
                "outgoing",
                status,
                evidence,
                conf,
                MODEL_VERSION,
                PROMPT_VERSION,
                ts,
                resolved,
                ("seed" if resolved else None),
            ),
        )

    for a, b, rtype, conf, ev in confirmed:
        insert(a, b, rtype, conf, ev, "confirmed", ts)
    for a, b, rtype, conf, ev in proposed:
        insert(a, b, rtype, conf, ev, "proposed", None)

    con.commit()
    n_conf = con.execute("SELECT count(*) FROM note_relations WHERE status='confirmed'").fetchone()[0]
    n_prop = con.execute("SELECT count(*) FROM note_relations WHERE status='proposed'").fetchone()[0]
    n_total = con.execute("SELECT count(*) FROM note_relations").fetchone()[0]
    con.close()
    print(f"[done] note_relations total={n_total} (confirmed={n_conf}, proposed={n_prop}, model_version={MODEL_VERSION})")


if __name__ == "__main__":
    main()
