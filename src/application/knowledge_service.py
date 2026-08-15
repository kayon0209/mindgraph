from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from domain.errors import ConflictError, NotFoundError
from domain.models import DocumentRecord, IndexStatus
from retrieval.embeddings import BGEEmbeddingProvider
from retrieval.indexing import build_versioned_index, load_corpus


SAFE_NAME = re.compile(r"[^\w\-.\u4e00-\u9fff]+")
logger = logging.getLogger("expense_rag.knowledge")


class KnowledgeService:
    def __init__(self, docs_dir: Path, upload_dir: Path, index_root: Path, invalidate_pipeline=lambda: None) -> None:
        self.docs_dir, self.upload_dir, self.index_root = docs_dir, upload_dir, index_root
        self.invalidate_pipeline = invalidate_pipeline
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self._deletions_file = self.upload_dir / ".pending_deletions.json"

    def _pending_deletions(self) -> list[dict]:
        try:
            return json.loads(self._deletions_file.read_text(encoding="utf-8"))
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
            return json.loads((self.index_root / current / "metadata.json").read_text(encoding="utf-8"))
        except Exception:
            return {}

    def list_documents(self) -> list[DocumentRecord]:
        chunks = load_corpus([(self.docs_dir, "official"), (self.upload_dir, "upload")])
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

    def rebuild(self) -> IndexStatus:
        previous = None
        try:
            previous = (self.index_root / "CURRENT").read_text(encoding="utf-8").strip()
        except OSError:
            pass
        chunks = load_corpus([(self.docs_dir, "official"), (self.upload_dir, "upload")])
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
