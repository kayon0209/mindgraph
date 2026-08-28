"""问题概念挖掘（阶段B：从真实提问中积累图谱数据）。

设计原则（与治理红线一致）：
- 纯规则、离线、无 LLM：从 query_logs 的真实提问中挖掘概念信号；
- 只产出 **proposed** 候选关系（CO_ASKED 共同提问），必须经 HITL 确认后才进入检索路径；
- 不改变任何查询时行为 —— 本服务只做数据积累，供图谱页/关系审核页展示；
- 增量幂等：每次只扫描上次运行之后新增的提问（concept_mine_runs 记录水位），
  候选对与 note_relations 已有记录（任意状态/任一方向）去重；
- 隐私安全：PRIVACY_LOG_QUESTIONS=false 时 question 为 NULL，直接跳过，绝不猜测。

挖掘信号：
1. 《标题》 引用：提问中 《...》 内的文本与笔记标题/别名精确匹配 → 命中笔记；
   未匹配的 《...》 文本 → concept_signals 覆盖缺口（用户问过但知识库没有的概念）；
2. 标题/别名子串命中：笔记标题或别名（≥2 字符）出现在提问中 → 命中笔记；
3. 共同提问（CO_ASKED）：同一提问命中 ≥2 篇笔记 → 两两产出 proposed 候选，
   confidence 随共同出现次数归一化递增（0.55 起步，每次 +0.05，封顶 0.9）。
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from itertools import combinations
from typing import Any

logger = logging.getLogger("mindgraph.question_concept_miner")

MODEL_VERSION = "mine-v1"
PROMPT_VERSION = "mine-v1"
EXTRACTION_METHOD = "question_co_asked"
BOOK_TITLE_RE = re.compile(r"《([^《》]{2,60})》")
MIN_TERM_LEN = 2
EVIDENCE_SPAN_CHARS = 300
BASE_CONFIDENCE = 0.55
CONFIDENCE_STEP = 0.05
MAX_CONFIDENCE = 0.9


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "").strip().lower()


def _parse_aliases(fm_json: str | None) -> list[str]:
    try:
        fm = json.loads(fm_json) if fm_json else {}
    except Exception:
        return []
    if not isinstance(fm, dict):
        return []
    aliases = fm.get("aliases") or fm.get("alias") or []
    if isinstance(aliases, str):
        aliases = [a.strip() for a in re.split(r"[,，;；\s]+", aliases) if a.strip()]
    return [a.strip() for a in aliases if isinstance(a, str) and a.strip()]


class QuestionConceptMiner:
    def __init__(self, db: Any, *, gap_min_seen: int = 2) -> None:
        self.db = db
        self.gap_min_seen = max(1, int(gap_min_seen))

    # ------------------------------------------------------------------
    # 数据装载
    # ------------------------------------------------------------------
    def _load_note_terms(self) -> tuple[dict[str, str], dict[str, str]]:
        """返回 (term -> note_id, note_id -> title)。同一词命中多篇笔记时保留 note_id 最小者（确定性）。"""
        rows = self.db.fetch_all("SELECT note_id, title, frontmatter_json FROM notes")
        term_to_note: dict[str, str] = {}
        titles: dict[str, str] = {}
        for row in rows:
            note_id = row["note_id"]
            title = (row.get("title") or "").strip()
            titles[note_id] = title
            terms = {title} if len(title) >= MIN_TERM_LEN else set()
            terms.update(a for a in _parse_aliases(row.get("frontmatter_json")) if len(a) >= MIN_TERM_LEN)
            for term in terms:
                key = _norm(term)
                if not key:
                    continue
                if key not in term_to_note or note_id < term_to_note[key]:
                    term_to_note[key] = note_id
        return term_to_note, titles

    def _last_run_created_at(self) -> str | None:
        row = self.db.fetch_one("SELECT created_at FROM concept_mine_runs ORDER BY created_at DESC LIMIT 1")
        return row["created_at"] if row else None

    def _load_new_questions(self, since: str | None) -> list[dict[str, Any]]:
        if since:
            rows = self.db.fetch_all(
                "SELECT question, question_hash, created_at FROM query_logs "
                "WHERE question IS NOT NULL AND created_at > ? ORDER BY created_at ASC",
                (since,),
            )
        else:
            rows = self.db.fetch_all(
                "SELECT question, question_hash, created_at FROM query_logs "
                "WHERE question IS NOT NULL ORDER BY created_at ASC"
            )
        return rows

    # ------------------------------------------------------------------
    # 挖掘主流程
    # ------------------------------------------------------------------
    def mine(self, *, trigger: str = "manual", dry_run: bool = False) -> dict[str, Any]:
        term_to_note, titles = self._load_note_terms()
        since = None if dry_run else self._last_run_created_at()
        questions = self._load_new_questions(since)
        if not questions:
            return {
                "ok": True, "dry_run": dry_run, "trigger": trigger, "reason": "no_new_questions",
                "mined": 0, "proposed_created": 0, "skipped_existing": 0, "gap_terms": 0, "gaps": [],
            }

        # 逐题匹配笔记 + 收集未收录 《》 概念
        co_count: dict[frozenset, int] = {}
        co_evidence: dict[frozenset, str] = {}
        gap_terms: dict[str, dict[str, Any]] = {}
        matched_questions = 0
        for q in questions:
            text = q["question"] or ""
            if not text.strip():
                continue
            matched: set[str] = set()
            # 信号1：《标题》精确匹配
            for m in BOOK_TITLE_RE.finditer(text):
                inner = m.group(1).strip()
                note_id = term_to_note.get(_norm(inner))
                if note_id:
                    matched.add(note_id)
                elif len(inner) >= MIN_TERM_LEN:
                    self._accumulate_gap(gap_terms, inner, q)
            # 信号2：标题/别名字串命中
            for term, note_id in term_to_note.items():
                if term and term in _norm(text):
                    matched.add(note_id)
            if matched:
                matched_questions += 1
            # 信号3：共同提问 → CO_ASKED 候选
            if len(matched) >= 2:
                evidence = text[:EVIDENCE_SPAN_CHARS]
                for a, b in combinations(sorted(matched), 2):
                    pair = frozenset((a, b))
                    co_count[pair] = co_count.get(pair, 0) + 1
                    co_evidence.setdefault(pair, evidence)

        # 去重：note_relations 已存在（任意状态/任一方向）的 pair 不再写入
        existing = {
            frozenset((r["source_note_id"], r["target_note_id"]))
            for r in self.db.fetch_all("SELECT source_note_id, target_note_id FROM note_relations")
        }
        fresh_pairs = [(pair, n) for pair, n in sorted(co_count.items(), key=lambda kv: (-kv[1], sorted(kv[0])))]
        skipped_existing = sum(1 for pair, _ in fresh_pairs if pair in existing)
        to_insert = [(pair, n) for pair, n in fresh_pairs if pair not in existing]

        ts = _now_iso()
        proposed_created = 0
        if to_insert and not dry_run:
            rows_to_insert = []
            for pair, count in to_insert:
                source, target = sorted(pair)
                confidence = round(min(MAX_CONFIDENCE, BASE_CONFIDENCE + CONFIDENCE_STEP * (count - 1)), 3)
                rows_to_insert.append((
                    f"mine-{uuid.uuid4().hex[:12]}",
                    source,
                    target,
                    "CO_ASKED",
                    "outgoing",
                    "proposed",
                    None,
                    confidence,
                    MODEL_VERSION,
                    PROMPT_VERSION,
                    ts,
                    co_evidence[pair],
                    "query_logs",
                    None,
                    None,
                    None,
                    EXTRACTION_METHOD,
                ))
            with self.db.transaction() as connection:
                connection.executemany(
                    """INSERT INTO note_relations
                       (relation_id, source_note_id, target_note_id, relation_type, direction, status,
                        evidence_chunk_id, confidence, model_version, prompt_version, proposed_at,
                        evidence_span, evidence_section, source_document_version, effective_from, effective_to, extraction_method)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    rows_to_insert,
                )
            proposed_created = len(rows_to_insert)

        gap_upserted = self._persist_gaps(gap_terms, ts, dry_run=dry_run)
        if not dry_run:
            # 水位取本批扫描到的最大 created_at（而非墙钟 now）：
            # 保证"上次运行之后新增的提问"按数据时间线增量推进，回填历史提问也能被覆盖。
            watermark = max(q["created_at"] for q in questions)
            self.db.execute(
                "INSERT INTO concept_mine_runs (run_id, trigger, created_at, questions_scanned, proposed_created, gap_terms) "
                "VALUES (?,?,?,?,?,?)",
                (f"mine-run-{uuid.uuid4().hex[:12]}", trigger, watermark, len(questions), proposed_created, gap_upserted),
            )

        return {
            "ok": True,
            "dry_run": dry_run,
            "trigger": trigger,
            "mined": len(questions),
            "matched_questions": matched_questions,
            "proposed_created": proposed_created,
            "skipped_existing": skipped_existing,
            "gap_terms": gap_upserted,
            "gaps": self.top_gaps(limit=10),
        }

    # ------------------------------------------------------------------
    # 覆盖缺口（concept_signals）
    # ------------------------------------------------------------------
    @staticmethod
    def _accumulate_gap(gap_terms: dict[str, dict[str, Any]], term: str, q: dict[str, Any]) -> None:
        key = _norm(term)
        if not key:
            return
        entry = gap_terms.setdefault(key, {"term": term.strip(), "count": 0, "sample_hash": None})
        entry["count"] += 1
        if entry["sample_hash"] is None:
            entry["sample_hash"] = q.get("question_hash")

    def _persist_gaps(self, gap_terms: dict[str, dict[str, Any]], ts: str, *, dry_run: bool) -> int:
        if not gap_terms:
            return 0
        if dry_run:
            return len(gap_terms)
        with self.db.transaction() as connection:
            for entry in gap_terms.values():
                connection.execute(
                    """INSERT INTO concept_signals (term, seen_count, first_seen, last_seen, sample_question_hash)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(term) DO UPDATE SET
                           seen_count = seen_count + excluded.seen_count,
                           last_seen = excluded.last_seen,
                           sample_question_hash = COALESCE(excluded.sample_question_hash, concept_signals.sample_question_hash)""",
                    (entry["term"], entry["count"], ts, ts, entry["sample_hash"]),
                )
        return len(gap_terms)

    def top_gaps(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            "SELECT term, seen_count, first_seen, last_seen, sample_question_hash FROM concept_signals "
            "WHERE seen_count >= ? ORDER BY seen_count DESC, last_seen DESC LIMIT ?",
            (self.gap_min_seen, limit),
        )
        return [
            {
                "term": r["term"],
                "seen_count": r["seen_count"],
                "first_seen": r["first_seen"],
                "last_seen": r["last_seen"],
                "sample_question_hash": r.get("sample_question_hash"),
            }
            for r in rows
        ]

    def gap_total(self) -> int:
        row = self.db.fetch_one("SELECT COUNT(*) AS c FROM concept_signals WHERE seen_count >= ?", (self.gap_min_seen,))
        return row["c"] if row else 0
