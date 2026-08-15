"""MindGraph 关系抽取自动化（V3 关键闭环）。

把"AI 自动发现候选关系 → 用户确认 → confirmed"这一侧的抽取流水线补齐。

设计原则（Human-in-the-loop）：
- 本服务只产出 **proposed** 候选关系，绝不自动写 confirmed（confirmed 必须用户确认后才进入 Graph RAG 检索路径）；
- 完全离线可用：默认用 BGE 本地嵌入做笔记级语义相似度候选（规则 + 语义），无需任何 LLM；
- 可选 `use_llm=True`：对高相似度候选用已配置的 Chat Provider 精炼关系类型与依据（轻量 LLM），提高候选质量；
- 幂等去重：已存在于 note_relations（任意状态 / 任一方向）的 pair 不再重复写入；可多次运行逐步扩充图谱（候选池耗尽后自然不再新增）；
- dry_run 模式只预测不落库，便于先预览再确认。

候选信号：
1. 语义相似度（主）：每篇笔记取其 chunk 文本拼接后用 BGE 嵌入，求两两余弦相似度，取每篇 top_k 且超过阈值者为候选；
2. 规则（可选 method='all'）：共享 frontmatter 标签 / 标题关键词 —— 与 seed 重叠部分会被去重自然过滤。
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from retrieval.embeddings import BGEEmbeddingProvider

logger = logging.getLogger("expense_rag.relation_extraction")

MODEL_VERSION = "auto-v1"
PROMPT_VERSION = "auto-v1"
EMBED_TRUNC_CHARS = 2000
LLM_EXCERPT_CHARS = 500


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_tags(fm_json: str | None) -> set[str]:
    try:
        fm = json.loads(fm_json) if fm_json else {}
    except Exception:
        return set()
    if not isinstance(fm, dict):
        return set()
    tags = fm.get("tags") or fm.get("tag") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in re.split(r"[,\s]+", tags) if t.strip()]
    out: set[str] = set()
    for t in tags:
        if isinstance(t, str):
            out.add(t.strip().lstrip("#").lower())
    return out


def _title_keywords(title: str) -> set[str]:
    toks = re.findall(r"[a-zA-Z][a-zA-Z0-9]{1,}", title or "")
    digits = re.findall(r"\d{4}", title or "")
    kw = {t.lower() for t in toks if len(t) >= 2}
    kw.update(digits)
    return kw


class RelationExtractionService:
    def __init__(self, db: Any, index_root: Path, provider_registry: Any | None = None) -> None:
        self.db = db
        self.index_root = Path(index_root)
        self.provider_registry = provider_registry

    # ------------------------------------------------------------------
    # 数据装载
    # ------------------------------------------------------------------
    def _active_chunks_path(self) -> Path | None:
        cur = self.index_root / "CURRENT"
        if not cur.exists():
            return None
        version = cur.read_text(encoding="utf-8").strip()
        p = self.index_root / version / "chunks.json"
        return p if p.exists() else None

    def _load_note_texts(self) -> dict[str, str]:
        """从激活索引的 chunks.json 按 mindgraph_id(==note_id) 聚合笔记文本。"""
        path = self._active_chunks_path()
        if not path:
            return {}
        chunks = json.loads(path.read_text(encoding="utf-8"))
        by_note: dict[str, list[str]] = {}
        titles: dict[str, str] = {}
        for ch in chunks:
            m = ch.get("metadata", {})
            nid = m.get("mindgraph_id")
            if not nid:
                continue
            by_note.setdefault(nid, []).append(ch.get("text", ""))
            if nid not in titles and m.get("title"):
                titles[nid] = m["title"]
        # 补全标题（来自 DB）
        try:
            for r in self.db.fetch_all("SELECT note_id, title FROM notes WHERE index_status='ready'"):
                titles.setdefault(r["note_id"], r["title"] or "")
        except Exception as exc:  # pragma: no cover
            logger.warning("note_titles_fetch_failed", extra={"error": str(exc)})
        # 拼接文本（标题重复一次增强权重，截断控制开销）
        out: dict[str, str] = {}
        for nid, texts in by_note.items():
            joined = "\n".join(texts)[:EMBED_TRUNC_CHARS]
            title = titles.get(nid, "")
            out[nid] = f"{title}\n{title}\n{joined}" if title else joined
        self._titles = titles
        return out

    def _build_note_embeddings(self, note_texts: dict[str, str]) -> dict[str, np.ndarray]:
        if not note_texts:
            return {}
        provider = BGEEmbeddingProvider()
        ids = list(note_texts.keys())
        vectors = np.asarray(
            provider.embed_documents([note_texts[i] for i in ids]), dtype="float32"
        )
        # L2 归一化，使点积 = 余弦相似度
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        vectors = vectors / norms
        return {nid: vectors[i] for i, nid in enumerate(ids)}

    @staticmethod
    def _is_noise_pair(titles: dict[str, str], a: str, b: str) -> bool:
        """过滤低价值候选：① 同标题（疑似重复笔记）；② 任一标题为纯日期 / 过短。"""
        ta, tb = (titles.get(a) or "").strip(), (titles.get(b) or "").strip()
        if not ta or not tb:
            return True
        if ta == tb:
            return True
        if re.fullmatch(r"\d{4}[-/]\d{2}[-/]\d{2}", ta) or re.fullmatch(r"\d{4}[-/]\d{2}[-/]\d{2}", tb):
            return True
        if len(ta) < 3 or len(tb) < 3:
            return True
        return False

    # ------------------------------------------------------------------
    # 候选生成
    # ------------------------------------------------------------------
    def _embedding_candidates(
        self, emb: dict[str, np.ndarray], top_k: int, threshold: float
    ) -> list[dict[str, Any]]:
        """每篇笔记取 top_k 最相似且 > threshold 的其他笔记作为候选。"""
        if not emb:
            return []
        ids = list(emb.keys())
        mat = np.stack([emb[i] for i in ids])  # [N, D]
        sim = mat @ mat.T  # 余弦相似度矩阵
        candidates: list[dict[str, Any]] = []
        for i, a in enumerate(ids):
            row = sim[i]
            order = np.argsort(row)[::-1]  # 降序
            taken = 0
            for j in order:
                if taken >= top_k:
                    break
                if j == i:
                    continue
                score = float(row[j])
                if score < threshold:
                    break
                b = ids[j]
                candidates.append(
                    {
                        "source": a,
                        "target": b,
                        "relation_type": "related_to",
                        "confidence": round(min(0.95, max(0.5, score)), 3),
                        "evidence": f"语义相似度 {score:.2f}",
                        "signal": "embedding",
                    }
                )
                taken += 1
        return candidates

    def _rule_candidates(self, limit: int = 60) -> list[dict[str, Any]]:
        """共享标签 / 标题关键词候选（与 seed 重叠部分由去重过滤）。"""
        rows = self.db.fetch_all(
            "SELECT note_id, title, frontmatter_json FROM notes WHERE index_status='ready'"
        )
        notes = [
            {"note_id": r["note_id"], "title": r["title"] or "", "tags": _parse_tags(r["frontmatter_json"])}
            for r in rows
        ]
        by_tag: dict[str, list] = {}
        for n in notes:
            for t in n["tags"]:
                by_tag.setdefault(t, []).append(n)
        out: list[dict[str, Any]] = []
        seen: set[frozenset] = set()
        for tag, ns in by_tag.items():
            if len(ns) < 2:
                continue
            ns = sorted(ns, key=lambda x: x["note_id"])
            a, b = ns[0], ns[1]
            pair = frozenset({a["note_id"], b["note_id"]})
            if pair in seen:
                continue
            seen.add(pair)
            out.append(
                {
                    "source": a["note_id"],
                    "target": b["note_id"],
                    "relation_type": "related_to",
                    "confidence": 0.82,
                    "evidence": f"共享标签 #{tag}",
                    "signal": "tag",
                }
            )
            if len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------------
    # LLM 精炼（可选）
    # ------------------------------------------------------------------
    def _llm_refine(self, candidates: list[dict[str, Any]], note_texts: dict[str, str]) -> list[dict[str, Any]]:
        if not self.provider_registry:
            return candidates
        provider = self.provider_registry.get()
        if provider is None:
            return candidates
        try:
            provider.available
        except Exception:
            return candidates
        titles = getattr(self, "_titles", {})
        kept: list[dict[str, Any]] = []
        for c in candidates:
            a, b = c["source"], c["target"]
            ta, tb = titles.get(a, a), titles.get(b, b)
            ea = note_texts.get(a, "")[:LLM_EXCERPT_CHARS]
            eb = note_texts.get(b, "")[:LLM_EXCERPT_CHARS]
            try:
                decision = self._ask_llm(provider, ta, ea, tb, eb)
            except Exception as exc:
                logger.warning("llm_refine_failed", extra={"pair": f"{a}->{b}", "error": str(exc)})
                kept.append(c)  # 失败则保留规则候选
                continue
            if decision.get("related"):
                c["relation_type"] = decision.get("relation_type", "related_to")
                c["evidence"] = decision.get("reason", c["evidence"])
                c["confidence"] = round(min(0.95, max(c["confidence"], 0.7)), 3)
                kept.append(c)
            # related=False → 丢弃该候选（LLM 判定不相关）
        return kept

    def _ask_llm(self, provider: Any, ta: str, ea: str, tb: str, eb: str) -> dict[str, Any]:
        system = (
            "你是个人知识图谱的关系抽取助手。给定两篇笔记的标题与片段，"
            "判断它们是否应建立知识关联。只输出 JSON："
            '{"related": true/false, "relation_type": "related_to|references|contradicts|elaborates", '
            '"reason": "一句话依据(中文)"}。不要输出其它内容。'
        )
        user = (
            f"笔记A《{ta}》：\n{ea}\n\n"
            f"笔记B《{tb}》：\n{eb}\n\n"
            "这两篇笔记是否应建立关联？"
        )
        text, _ = provider.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": user}]
        )
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return {"related": False}
        try:
            data = json.loads(m.group(0))
        except Exception:
            return {"related": False}
        return {"related": bool(data.get("related")), **data}

    # ------------------------------------------------------------------
    # 去重与落库
    # ------------------------------------------------------------------
    def _existing_pairs(self) -> set[frozenset]:
        rows = self.db.fetch_all("SELECT source_note_id, target_note_id FROM note_relations")
        return {frozenset({r["source_note_id"], r["target_note_id"]}) for r in rows}

    def extract(
        self,
        *,
        method: str = "embedding",
        top_k: int = 5,
        similarity_threshold: float = 0.5,
        max_candidates: int = 300,
        use_llm: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        note_texts = self._load_note_texts()
        if not note_texts:
            return {"ok": False, "reason": "no_index_or_notes", "inserted": 0, "skipped": 0}

        emb = self._build_note_embeddings(note_texts)
        candidates = self._embedding_candidates(emb, top_k, similarity_threshold)
        if method == "all":
            candidates += self._rule_candidates()

        # 质量过滤：去掉同标题（重复笔记）/ 纯日期 / 过短标题的噪音候选
        titles = getattr(self, "_titles", {})
        before = len(candidates)
        candidates = [c for c in candidates if not self._is_noise_pair(titles, c["source"], c["target"])]
        filtered_noise = before - len(candidates)

        # 去重（已存在任意状态 / 任一方向）
        existing = self._existing_pairs()
        deduped: list[dict[str, Any]] = []
        skipped_existing = 0
        for c in candidates:
            pair = frozenset({c["source"], c["target"]})
            if pair in existing:
                skipped_existing += 1
                continue
            deduped.append(c)

        # 限流 + 可选 LLM 精炼
        if use_llm and deduped:
            deduped = self._llm_refine(deduped[:max_candidates], note_texts)
        truncated = max(0, len(deduped) - max_candidates)
        deduped = deduped[:max_candidates]

        inserted = 0
        conflicts = 0
        # 冲突：候选反向已 confirmed
        confirmed_pairs = {
            frozenset({r["source_note_id"], r["target_note_id"]})
            for r in self.db.fetch_all("SELECT source_note_id, target_note_id FROM note_relations WHERE status='confirmed'")
        }
        if not dry_run:
            ts = _now_iso()
            for c in deduped:
                pair = frozenset({c["source"], c["target"]})
                if pair in confirmed_pairs:
                    conflicts += 1
                relation_id = f"auto-{uuid.uuid4().hex[:12]}"
                self.db.execute(
                    """INSERT INTO note_relations
                       (relation_id, source_note_id, target_note_id, relation_type, direction, status,
                        evidence_chunk_id, confidence, model_version, prompt_version, proposed_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        relation_id,
                        c["source"],
                        c["target"],
                        c["relation_type"],
                        "outgoing",
                        "proposed",
                        c["evidence"],
                        c["confidence"],
                        MODEL_VERSION,
                        PROMPT_VERSION,
                        ts,
                    ),
                )
                inserted += 1

        return {
            "ok": True,
            "dry_run": dry_run,
            "method": method,
            "use_llm": use_llm,
            "notes_scanned": len(note_texts),
            "candidates_before_dedup": len(candidates),
            "filtered_noise": filtered_noise,
            "inserted": inserted,
            "skipped_existing": skipped_existing,
            "truncated_by_max": truncated,
            "conflicts_flagged": conflicts,
            "remaining_proposed": self._count_proposed(),
        }

    def _count_proposed(self) -> int:
        row = self.db.fetch_one("SELECT COUNT(*) AS c FROM note_relations WHERE status='proposed'")
        return row["c"] if row else 0
