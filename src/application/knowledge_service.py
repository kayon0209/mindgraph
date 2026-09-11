from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from application.index_metadata import (
    document_key,
    enrich_records_with_notes,
    evaluate_index_shrinkage,
    index_document_keys,
    load_note_index,
)
from domain.errors import ConflictError, IndexShrinkageError, NotFoundError
from domain.models import DocumentRecord, IndexStatus
from retrieval.embeddings import BGEEmbeddingProvider
from retrieval.indexing import build_versioned_index, load_corpus


SAFE_NAME = re.compile(r"[^\w\-.\u4e00-\u9fff]+")
logger = logging.getLogger("mindgraph.knowledge")


class KnowledgeService:
    def __init__(
        self,
        docs_dir: Path,
        upload_dir: Path,
        index_root: Path,
        invalidate_pipeline=lambda: None,
        db_path: Path | str | None = None,
        included_subtrees: tuple[str, ...] | None = None,
    ) -> None:
        self.docs_dir, self.upload_dir, self.index_root = docs_dir, upload_dir, index_root
        self.invalidate_pipeline = invalidate_pipeline
        # 索引元数据的单一事实源是 product.sqlite3 的 notes 表（见 application/index_metadata.py）。
        # 这里只存路径、构建时才只读打开，避免 KnowledgeService 持有长连接。
        self.db_path = Path(db_path) if db_path is not None else None
        # 已声明的语料范围（settings.INDEX_INCLUDED_SUBTREES）。只用于把"范围外跳过"
        # 的日志降级为 INFO，不改变实际扫描范围；None = 未声明，保持原样告警。
        self.included_subtrees = None if included_subtrees is None else tuple(included_subtrees)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self._deletions_file = self.upload_dir / ".pending_deletions.json"

    def _pending_deletions(self) -> list[dict]:
        try:
            return cast(list[dict], json.loads(self._deletions_file.read_text(encoding="utf-8")))
        except Exception:
            return []

    def _save_pending_deletions(self, items: list[dict]) -> None:
        self._deletions_file.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def document_id(name: str) -> str:
        return hashlib.sha256(name.encode()).hexdigest()[:16]

    def _metadata(self) -> dict:
        try:
            current = (self.index_root / "CURRENT").read_text(encoding="utf-8").strip()
            return cast(dict, json.loads((self.index_root / current / "metadata.json").read_text(encoding="utf-8")))
        except Exception:
            return {}

    def list_documents(self) -> list[DocumentRecord]:
        chunks = load_corpus(
            [(self.docs_dir, "official"), (self.upload_dir, "upload")],
            included_subtrees=self.included_subtrees,
        )
        counts: dict[str, int] = {}
        for chunk in chunks:
            doc_name = chunk.metadata.get("doc_name", "") if hasattr(chunk, "metadata") else ""
            counts[doc_name] = counts.get(doc_name, 0) + 1
        metadata = self._metadata()
        indexed_at = metadata.get("index_created_at")
        indexed_dt = datetime.fromisoformat(indexed_at) if indexed_at else None
        records = []
        for category, directory in (("official", self.docs_dir), ("upload", self.upload_dir)):
            for path in sorted(directory.glob("*.md")):
                modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                pending = indexed_dt is None or modified > indexed_dt
                records.append(DocumentRecord(
                    document_id=self.document_id(path.name), document_name=path.name, knowledge_category=category,
                    version=hashlib.sha256(path.read_bytes()).hexdigest()[:12], chunk_count=counts.get(path.name, 0),
                    index_version=metadata.get("index_version"), index_status="pending" if pending else "indexed",
                    embedding_model=metadata.get("embedding_model_name"), uploaded_at=modified,
                    last_indexed_at=indexed_dt, pending_reindex=pending,
                ))
        for item in self._pending_deletions():
            records.append(DocumentRecord(**item, index_version=metadata.get("index_version"),
                embedding_model=metadata.get("embedding_model_name"), last_indexed_at=indexed_dt,
                index_status="pending_deletion", pending_reindex=True))
        return records

    def get_document(self, document_id: str) -> DocumentRecord:
        for record in self.list_documents():
            if record.document_id == document_id:
                return record
        raise NotFoundError("Document not found")

    def upload(self, filename: str, content: bytes, category: str = "upload") -> DocumentRecord:
        if len(content) > 2 * 1024 * 1024:
            raise ValueError("File exceeds 2 MB limit")
        safe_name = SAFE_NAME.sub("_", Path(filename).name)
        if not safe_name.lower().endswith(".md"):
            raise ValueError("Only Markdown files are supported")
        if not content.strip():
            raise ValueError("Document is empty")
        target = self.upload_dir / safe_name
        if target.exists():
            raise ConflictError("Document already exists")
        content.decode("utf-8")
        target.write_bytes(content)
        pending = [item for item in self._pending_deletions() if item["document_name"] != safe_name]
        self._save_pending_deletions(pending)
        return self.get_document(self.document_id(safe_name))

    def delete(self, document_id: str) -> DocumentRecord:
        record = self.get_document(document_id)
        if record.knowledge_category != "upload":
            raise ConflictError("Official documents cannot be deleted through the demo API")
        target = self.upload_dir / record.document_name
        target.unlink()
        pending = self._pending_deletions()
        pending.append({
            "document_id": record.document_id, "document_name": record.document_name,
            "knowledge_category": record.knowledge_category, "version": record.version,
            "chunk_count": record.chunk_count, "uploaded_at": record.uploaded_at.isoformat(),
            "error": None,
        })
        self._save_pending_deletions(pending)
        record.index_status = "pending_deletion"
        record.pending_reindex = True
        return record

    def _pending_deletion_names(self) -> list[str]:
        return [str(item.get("document_name") or "") for item in self._pending_deletions()]

    def rebuild(self, *, force: bool = False) -> IndexStatus:
        previous = None
        try:
            previous = (self.index_root / "CURRENT").read_text(encoding="utf-8").strip()
        except OSError:
            pass
        chunks = load_corpus(
            [(self.docs_dir, "official"), (self.upload_dir, "upload")],
            included_subtrees=self.included_subtrees,
        )

        # ── 元数据单一事实源（2026-09-10 P0）─────────────────────────────────
        # 这条路径只扫顶层 markdown，产出的 chunk 原本没有任何 ACL / 命名空间字段。
        # 一旦它的版本成为 CURRENT，按源过滤（source_ids）与按权限过滤（access_scope）
        # 会同时静默失效——2026-09-09 就是这样丢掉 21 篇文档+全部 ACL 元数据的。
        # 这里从 notes 表补齐字段：只加键，不改正文与分块，因此召回指标不受影响。
        enrichment = enrich_records_with_notes(chunks, load_note_index(self.db_path))
        if not enrichment.source_available:
            logger.warning(
                "index_metadata_source_unavailable",
                extra={"reason": enrichment.source_reason, "chunks": enrichment.total},
            )
        elif enrichment.unmatched:
            logger.warning(
                "index_metadata_partial",
                extra={
                    "matched": enrichment.matched,
                    "unmatched": enrichment.unmatched,
                    "coverage": enrichment.coverage,
                    "samples": enrichment.unmatched_samples,
                },
            )

        # ── 准入守卫：拒绝在无人知晓的情况下把索引改小 ──────────────────────
        candidate_keys = {key for key in (document_key(chunk.metadata) for chunk in chunks) if key}
        guard = evaluate_index_shrinkage(
            previous_keys=index_document_keys(self.index_root)[0],
            candidate_keys=candidate_keys,
            excused_names=self._pending_deletion_names(),
        )
        emit = logger.error if (guard["blocked"] and not force) else logger.info
        emit("index_rebuild_shrinkage_guard", extra=guard)
        if guard["blocked"] and not force:
            raise IndexShrinkageError(
                "索引重建会让以下文档从活跃索引中消失，已拒绝激活："
                + ", ".join(guard["unexplained_missing"][:5])
                + ("…" if guard["unexplained_missing_count"] > 5 else "")
                + "；确认无误请显式 force=true 重试。",
                detail={"guard": guard, "previous_index_version": previous},
            )

        version = datetime.now(timezone.utc).strftime("m3-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
        try:
            _, _ = build_versioned_index(BGEEmbeddingProvider(), chunks, self.index_root, version)  # 尊重 BGE_LOCAL_FILES_ONLY（默认 true；设 false 允许首次自动下载）
            self._save_pending_deletions([])
            self.invalidate_pipeline()
            logger.info("index_rebuild_completed", extra={"index_version": version, "chunk_count": len(chunks)})
        except Exception:
            if previous:
                (self.index_root / "CURRENT").write_text(previous, encoding="utf-8")
            logger.exception("index_rebuild_failed", extra={"previous_index_version": previous})
            raise
        return self.index_status()

    def index_status(self) -> IndexStatus:
        metadata = self._metadata()
        return IndexStatus(
            index_version=metadata.get("index_version"), status="ready" if metadata else "missing",
            embedding_model=metadata.get("embedding_model_name"), vector_dimension=metadata.get("vector_dimension"),
            chunk_count=metadata.get("chunk_count", 0), created_at=metadata.get("index_created_at"),
            pending_changes=any(record.pending_reindex for record in self.list_documents()),
        )
