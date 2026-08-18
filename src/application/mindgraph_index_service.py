"""MindGraph 索引构建服务（M1-D3 增量同步核心）。

策略（对齐《最终方案 v1.0》"嵌入复用 + 增量重建 + 原子切换"）：

- 数据源：``notes`` 表（MindGraph 笔记），不再依赖原 RAG 文档目录；
- 增量优化：复用 ``embedding_cache`` 表（按 chunk 正文 checksum 缓存向量），
  仅变更 chunk 重新计算 embedding，未变更 chunk 直接复用；
- 原子切换：新索引版本构建成功后，才通过 ``CURRENT.tmp`` + ``replace`` 激活，
  失败时回滚笔记状态为 ``pending``，不影响线上可用索引
  （符合索引状态机 ``pending → processing → ready/failed``）。
- 删除的笔记在扫描阶段已物理剪枝，构建时自然排除，无需单独的「索引移除」逻辑。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import faiss
import numpy as np

from document_loader import _chunk_text, _split_by_markdown_headers
from infrastructure.database import ProductDatabase, dumps
from infrastructure.markdown_frontmatter import parse_frontmatter
from retrieval.embeddings import BGEEmbeddingProvider
from retrieval.types import Chunk


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MindGraphIndexService:
    def __init__(
        self,
        db: ProductDatabase,
        vault_root: Path,
        index_root: Path,
        provider: Any | None = None,
    ) -> None:
        self.db = db
        self.vault_root = Path(vault_root)
        self.index_root = Path(index_root)
        self.index_root.mkdir(parents=True, exist_ok=True)
        self.provider = provider or BGEEmbeddingProvider()  # 尊重 BGE_LOCAL_FILES_ONLY 环境变量（默认 true=离线安全；设 false 即首次自动下载）

    # ------------------------------------------------------------------ #
    # 查询待索引笔记
    # ------------------------------------------------------------------ #
    def pending_notes(self) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT * FROM notes "
            "WHERE index_status IN ('pending','failed') AND ai_access_level <> 'excluded'"
        )

    def has_pending(self) -> bool:
        row = self.db.fetch_one(
            "SELECT 1 FROM notes "
            "WHERE index_status IN ('pending','failed') AND ai_access_level <> 'excluded' LIMIT 1"
        )
        return row is not None

    # ------------------------------------------------------------------ #
    # 分块（带 mindgraph_id，正文剥离 Frontmatter）
    # ------------------------------------------------------------------ #
    def _load_note_chunks(self, note: dict[str, Any]) -> list[Chunk]:
        path = self.vault_root / note["vault_path"]
        category = Path(note["vault_path"]).parent.name or "根目录"
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return []
        _, body, _ = parse_frontmatter(raw)
        if not body.strip():
            return []
        chunks: list[Chunk] = []
        idx = 0
        for section_path, sec_body in _split_by_markdown_headers(body):
            for sub in _chunk_text(sec_body, 500, 50):
                chunks.append(Chunk(
                    chunk_id=f"{note['note_id']}::{idx}",
                    text=sub,
                    document_id=note["note_id"],
                    chunk_index=idx,
                    section_path=section_path,
                    metadata={
                        "mindgraph_id": note["note_id"],
                        "vault_path": note["vault_path"],
                        "title": note["title"],
                        "doc_name": Path(note["vault_path"]).name,
                        "section_path": section_path,
                        "chunk_index": idx,
                        "ai_access_level": note.get("ai_access_level", "local_only"),
                        "owner": note.get("owner"),
                        "policy_key": note.get("policy_key"),
                        "document_version": note.get("document_version"),
                        "effective_from": note.get("effective_from"),
                        "effective_to": note.get("effective_to"),
                        "policy_status": note.get("policy_status", "unspecified"),
                        "effective_date": note.get("effective_from"),
                        "expiration_date": note.get("effective_to"),
                        "document_status": note.get("policy_status", "unspecified"),
                        "knowledge_category": category,
                        "origin": "mindgraph",
                    },
                ))
                idx += 1
        return chunks

    def _all_chunks(self) -> list[Chunk]:
        notes = self.db.fetch_all("SELECT * FROM notes WHERE ai_access_level <> 'excluded'")
        chunks: list[Chunk] = []
        for note in notes:
            chunks.extend(self._load_note_chunks(note))
        return chunks

    # ------------------------------------------------------------------ #
    # embedding 缓存（按 chunk 正文 checksum）
    # ------------------------------------------------------------------ #
    def _cached_embedding(self, checksum: str) -> list[float] | None:
        row = self.db.fetch_one(
            "SELECT embedding_json,dimension FROM embedding_cache "
            "WHERE model_name=? AND model_revision=? AND chunk_checksum=?",
            (self.provider.model_name, self.provider.model_revision, checksum),
        )
        if row and row["dimension"] == self.provider.dimension:
            return json.loads(row["embedding_json"])
        return None

    def _cache_embedding(self, checksum: str, vector: list[float]) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO embedding_cache VALUES (?,?,?,?,?,?)",
            (self.provider.model_name, self.provider.model_revision, checksum,
             self.provider.dimension, dumps(vector), _utc_iso()),
        )

    # ------------------------------------------------------------------ #
    # 构建（增量 + 原子切换）
    # ------------------------------------------------------------------ #
    def build(self, operator: str = "local", force: bool = False) -> dict[str, Any]:
        """构建索引。

        - 默认仅在有待索引笔记（pending/failed）时构建；
        - ``force=True`` 用于删除场景：笔记已从 ``notes`` 表剪枝，无 pending
          可触发，但旧 FAISS 索引仍含其 chunk，需强制全量重建以排除。
          embedding 仍按 checksum 命中缓存，重建成本仅为 FAISS.add（毫秒级）。
        """
        pending = self.pending_notes()
        if not pending and not force:
            return {"status": "noop", "reason": "no pending notes"}
        pending_ids = [n["note_id"] for n in pending]

        # 1. 标记 processing（状态机可见）
        self.db.execute_many(
            "UPDATE notes SET index_status='processing' WHERE note_id=?",
            [(i,) for i in pending_ids],
        )

        version = datetime.now(timezone.utc).strftime("mg-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
        directory = self.index_root / version
        directory.mkdir()
        previous = self._current()
        reused = 0
        new_count = 0
        try:
            chunks = self._all_chunks()
            if not chunks:
                raise ValueError("No active chunks to index")

            # 2. 向量：命中缓存复用，未命中收集批量 embed（保持 chunk 顺序）
            vectors: list[list[float] | None] = [None] * len(chunks)
            to_embed: list[tuple[int, str]] = []
            for i, chunk in enumerate(chunks):
                checksum = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
                cached = self._cached_embedding(checksum)
                if cached is not None:
                    vectors[i] = cached
                    reused += 1
                else:
                    to_embed.append((i, checksum))

            if to_embed:
                texts = [chunks[i].text for i, _ in to_embed]
                computed = self.provider.embed_documents(texts)
                for (i, checksum), vec in zip(to_embed, computed):
                    vectors[i] = vec
                    self._cache_embedding(checksum, vec)
                    new_count += 1

            matrix = np.asarray([v for v in vectors], dtype="float32")
            faiss.normalize_L2(matrix)
            index = faiss.IndexFlatIP(matrix.shape[1])
            index.add(matrix)
            faiss.write_index(index, str(directory / "dense.faiss"))
            (directory / "chunks.json").write_text(
                json.dumps([c.__dict__ for c in chunks], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            all_ids = [row["note_id"] for row in self.db.fetch_all("SELECT note_id FROM notes")]
            manifest = {
                "index_version": version,
                "embedding_model_name": self.provider.model_name,
                "embedding_model_revision": self.provider.model_revision,
                "vector_dimension": int(matrix.shape[1]),
                "chunk_count": len(chunks),
                "note_count": len(all_ids),
                "reused_embeddings": reused,
                "new_embeddings": new_count,
                "created_at": _utc_iso(),
                "build_status": "validated",
                "previous_index_version": previous,
                "strategy": "mindgraph_incremental",
            }
            (directory / "metadata.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (directory / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            # 3. 原子切换（先写临时指针再 replace，避免半写）
            self._activate(version)

            # 4. 所有参与构建的笔记置 ready（含上次 failed 自愈）
            now = _utc_iso()
            self.db.execute_many(
                "UPDATE notes SET index_status='ready', index_version=?, last_indexed_at=? WHERE note_id=?",
                [(version, now, nid) for nid in all_ids],
            )
            self.db.execute(
                "INSERT INTO index_builds VALUES (?,?,?,?,?,?,?)",
                (version, "validated", dumps(manifest), previous, manifest["created_at"], now, None),
            )
            return manifest
        except Exception as exc:
            failure = {
                "index_version": version,
                "build_status": "failed",
                "failure_reason": f"{type(exc).__name__}: {exc}",
            }
            (directory / "manifest.json").write_text(
                json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self.db.execute(
                "INSERT OR REPLACE INTO index_builds VALUES (?,?,?,?,?,?,?)",
                (version, "failed", dumps(failure), previous, _utc_iso(), None, failure["failure_reason"]),
            )
            # 回滚：pending 笔记保持 pending，等待下次重试；不破坏当前可用索引
            self.db.execute_many(
                "UPDATE notes SET index_status='pending' WHERE note_id=?",
                [(i,) for i in pending_ids],
            )
            raise

    # ------------------------------------------------------------------ #
    # 当前索引版本 + 原子激活
    # ------------------------------------------------------------------ #
    def _current(self) -> str | None:
        try:
            return (self.index_root / "CURRENT").read_text(encoding="utf-8").strip()
        except OSError:
            return None

    def _activate(self, version: str) -> None:
        temp = self.index_root / "CURRENT.tmp"
        temp.write_text(version, encoding="utf-8")
        temp.replace(self.index_root / "CURRENT")

    def current_version(self) -> str | None:
        return self._current()
