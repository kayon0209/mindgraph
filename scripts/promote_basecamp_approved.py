"""Promote 4 Basecamp candidates to approved golden entries.

User reviewed and approved on 2026-08-27 (human-in-the-loop, consistent with
the Mattermost external cases). Backs up both dataset files first.
"""
import json
from pathlib import Path

GOLDEN = Path("evaluation/datasets/mindgraph_golden_v2.jsonl")
CAND = Path("evaluation/datasets/mindgraph_candidates_v2.jsonl")
PREFIX = "ext-basecamp-"


def load(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def dump(p: Path, rows: list[dict]) -> None:
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def main() -> None:
    # 备份（可回滚）
    for p in (GOLDEN, CAND):
        bak = p.with_suffix(p.suffix + ".bak-approve")
        if not bak.exists():
            bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")

    golden = load(GOLDEN)
    cands = load(CAND)

    to_promote = [c for c in cands if c["case_id"].startswith(PREFIX)]
    if not to_promote:
        print("no basecamp candidates found; nothing to do")
        return

    for c in to_promote:
        c["validation_status"] = "approved"
    golden.extend(to_promote)
    cands = [c for c in cands if not c["case_id"].startswith(PREFIX)]

    dump(GOLDEN, golden)
    dump(CAND, cands)

    print(f"promoted={len(to_promote)} golden={len(golden)} candidates={len(cands)}")
    for c in to_promote:
        print(f"  approved: {c['case_id']}")


if __name__ == "__main__":
    main()
