from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from application.access_control import note_acl_matches
from domain.errors import ConflictError, NotFoundError, ValidationError
from domain.models import AuthorityLevel, DocumentStatus, DocumentVersionModel
from infrastructure.database import ProductDatabase, dumps, loads
from infrastructure.parsers import default_parser_registry
from application.ocr_enrichment import ocr_pages
from application.page_ingestion import PageIngestionService
from application.structured_chunker import StructuredChunker
from infrastructure.ocr.base import OCRProvider
from infrastructure.ocr import get_ocr_provider
from infrastructure.settings import get_settings


TRANSITIONS = {
    "draft": {"pending_index", "parse_failed", "deleted"},
    "pending_index": {"active", "index_failed", "deleted"},
    "active": {"replaced", "expired", "deleted"},
    "index_failed": {"pending_index", "deleted"},
    "parse_failed": {"draft", "deleted"},
    "expired": set(), "replaced": set(), "deleted": set(),
}

# 存储目录段安全校验：logical_document_id / version 直接拼进文件系统路径，
# 必须拒绝路径分隔符与 ".."，否则表单字段可穿越 storage_root 任意写文件。
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def _safe_segment(value: str, name: str) -> str:
    text = (value or "").strip()
    if not text or ".." in text or not _SAFE_SEGMENT.match(text):
        raise ValidationError(f"Invalid {name}: only letters, digits, dot, dash and underscore are allowed")
    return text


class DocumentLifecycleService:
    def __init__(self, database: ProductDatabase, storage_root: Path,
                 ocr_provider: OCRProvider | None = None) -> None:
        self.database, self.storage_root = database, Path(storage_root)
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.chunker = StructuredChunker()
        self.page_ingestion = PageIngestionService(database)
        # None = 由 OCR_ENABLED / OCR_PROVIDER 决定；注入用于测试与自定义引擎
        self.ocr_provider = ocr_provider

    def import_existing_markdown(self, paths: list[Path]) -> None:
        for path in paths:
            logical_id = hashlib.sha256(path.name.encode()).hexdigest()[:16]
            if self.database.fetch_one("SELECT document_id FROM document_versions WHERE logical_document_id=?", (logical_id,)):
                continue
            self.create_version(path.name, path.read_bytes(), logical_id, "v1", "other", "official_policy", status="active")

    def create_version(self, filename: str, data: bytes, logical_document_id: str | None, version: str,
                       category: str, authority: str, effective_date: str | None = None,
                       expiration_date: str | None = None, status: str = "draft",
                       workspace: str | None = None, department: str | None = None,
                       acl_json: str = "{}", acl_public: bool = False) -> DocumentVersionModel:
        logical_id = _safe_segment(logical_document_id or str(uuid.uuid4()), "logical_document_id")
        version = _safe_segment(version, "version")
        parser = default_parser_registry.get(filename)
        checksum = hashlib.sha256(data).hexdigest()
        document_id = hashlib.sha256(f"{logical_id}:{version}:{checksum}".encode()).hexdigest()[:24]
        target_dir = self.storage_root / logical_id / version
        # 纵深防御：即使段校验被绕过，也确保目标路径仍位于存储根之内
        if self.storage_root.resolve() not in target_dir.resolve().parents:
            raise ValidationError("Invalid document storage path")
        if target_dir.exists(): raise ConflictError("Document version already exists")
        target_dir.mkdir(parents=True)
        source = target_dir / ("source." + Path(filename).suffix.lower().lstrip("."))
        source.write_bytes(data)
        chunks: list = []  # 解析失败时为空；页级记录据此跳过 finalize
        ocr_report: dict | None = None
        try:
            parsed = parser.parse(data, filename)
            parsed, ocr_report = self._maybe_ocr(parsed, data)
            chunks = self.chunker.chunk(parsed)
            ocr_required_pages = list(parsed.ocr_required_pages)
            if ocr_required_pages:
                diagnostics = {"parser": parsed.parser_name, "parser_version": parsed.parser_version,
                    "elements": len(parsed.elements), "chunks": len(chunks), "pages": parsed.metadata.get("page_count"),
                    "tables": sum(item.element_type == "table" for item in parsed.elements), "warnings": parsed.warnings,
                    "ocr_required_pages": ocr_required_pages,
                    "status": "ocr_required", "failure_reason": "OCR is required before indexing"}
                status = "parse_failed"
            else:
                diagnostics = {"parser": parsed.parser_name, "parser_version": parsed.parser_version,
                    "elements": len(parsed.elements), "chunks": len(chunks), "pages": parsed.metadata.get("page_count"),
                    "tables": sum(item.element_type == "table" for item in parsed.elements), "warnings": parsed.warnings,
                    "ocr_required_pages": [], "status": "success"}
            (target_dir / "parsed.json").write_text(parsed.model_dump_json(indent=2), encoding="utf-8")
            (target_dir / "chunks.json").write_text(json.dumps([item.model_dump(mode="json") for item in chunks], ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            diagnostics = {"status": "failed", "failure_reason": f"{type(exc).__name__}: {exc}", "warnings": []}
            status = "parse_failed"
        # 页级摄取记录（PR-06）：把已算出的页级产物落库，供"失败在哪一页"与
        # "重试跳过成功页"查询。它是观测/恢复能力，**不是入库前置条件**：
        # 落库失败只进诊断，绝不允许让一次本可成功的上传失败。
        # OCR 报告只含统计信息（页数/置信度/耗时/失败原因），不含识别文本
        if ocr_report is not None:
            diagnostics["ocr"] = ocr_report
        diagnostics["page_ingestion"] = self._record_page_ingestion(
            document_id=document_id, logical_id=logical_id, version=version,
            filename=filename, checksum=checksum, data=data, chunks=chunks,
        )
        now = datetime.now(timezone.utc)
        record = DocumentVersionModel(document_id=document_id, logical_document_id=logical_id, version=version,
            title=Path(filename).stem, file_type=Path(filename).suffix.lower().lstrip("."), knowledge_category=category,
            authority_level=cast(AuthorityLevel, authority), effective_date=effective_date, expiration_date=expiration_date, status=cast(DocumentStatus, status),
            checksum=checksum, parsing_diagnostics=diagnostics, created_at=now, updated_at=now,
            workspace=workspace, department=department, acl_json=acl_json, acl_public=acl_public)
        self.database.execute(
            "INSERT INTO document_versions ("
            "document_id, logical_document_id, version, title, file_type, knowledge_category, "
            "authority_level, effective_date, expiration_date, status, checksum, supersedes_version, "
            "source_path, parsing_diagnostics_json, created_at, updated_at, indexed_at, created_by, "
            "workspace, department, acl_json, acl_public"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                record.document_id, record.logical_document_id, record.version, record.title, record.file_type,
                record.knowledge_category, record.authority_level, record.effective_date, record.expiration_date,
                record.status, record.checksum, record.supersedes_version, str(source), dumps(diagnostics),
                now.isoformat(), now.isoformat(), None, record.created_by, record.workspace, record.department,
                record.acl_json, 1 if record.acl_public else 0,
            ),
        )
        return record

    def _maybe_ocr(self, parsed, data: bytes):
        """OCR 关闭时**原样返回**（默认路径）；开启时对标记页跑识别。

        失败一律降级为"这页没解析出来"，不让 OCR 异常影响整份文档入库。
        """
        settings = get_settings()
        if not settings.OCR_ENABLED or not parsed.ocr_required_pages:
            return parsed, None
        provider = self.ocr_provider or get_ocr_provider(
            settings.OCR_PROVIDER, timeout_seconds=settings.OCR_TIMEOUT_SECONDS
        )
        try:
            report = ocr_pages(parsed, data, provider=provider,
                               min_confidence=settings.OCR_MIN_CONFIDENCE, dpi=settings.OCR_DPI)
        except Exception as exc:  # noqa: BLE001 -- OCR 是增强，失败不该阻断入库
            return parsed, {"enabled": True, "error": f"{type(exc).__name__}: {exc}"}
        return report["document"], report

    def _record_page_ingestion(self, *, document_id: str, logical_id: str, version: str, filename: str,
                               checksum: str, data: bytes, chunks: list) -> dict:
        """落页级摄取记录（PR-06）。**任何异常都吞掉**：它是观测能力，不该阻断入库。"""
        try:
            self.page_ingestion.register(document_id=document_id, logical_document_id=logical_id,
                                         version=version, filename=filename, checksum=checksum)
            report = self.page_ingestion.run(document_id, data, filename)
            if chunks:
                self.page_ingestion.finalize(document_id, len(chunks))
            return {
                "job_id": document_id,
                "status": report["status"],
                "pages_recorded": len(report["processed_pages"]),
                "ocr_required_pages": report.get("ocr_required_pages", []),
                "failed_pages": report.get("failed_pages", []),
            }
        except Exception as exc:  # noqa: BLE001 -- 观测失败不得影响文档入库
            return {"status": "not_recorded", "failure_reason": f"{type(exc).__name__}: {exc}"}

    def transition(self, document_id: str, target: str) -> DocumentVersionModel:
        record = self.get(document_id)
        if target not in TRANSITIONS.get(record.status, set()):
            raise ConflictError(f"Invalid document transition: {record.status} -> {target}")
        now = datetime.now(timezone.utc).isoformat()
        if target == "active":
            # 同化旧版本与新状态必须原子，避免"双 active"中间态
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE document_versions SET status='replaced',updated_at=? WHERE logical_document_id=? AND status='active' AND document_id<>?",
                    (now, record.logical_document_id, document_id),
                )
                connection.execute(
                    "UPDATE document_versions SET status=?,updated_at=? WHERE document_id=?",
                    (target, now, document_id),
                )
        else:
            self.database.execute("UPDATE document_versions SET status=?,updated_at=? WHERE document_id=?", (target, now, document_id))
        return self.get(document_id)

    def list(self, status: str | None = None, category: str | None = None, access_scope: dict | None = None):
        clauses, params = [], []
        if status: clauses.append("status=?"); params.append(status)
        if category: clauses.append("knowledge_category=?"); params.append(category)
        sql = "SELECT * FROM document_versions" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY logical_document_id,created_at DESC"  # nosec B608 -- clauses 仅为常量 'status=?'/'knowledge_category=?'
        rows = [self._row(row) for row in self.database.fetch_all(sql, tuple(params))]
        if access_scope is not None:
            rows = [row for row in rows if note_acl_matches(row.model_dump(mode="python"), access_scope)]
        return rows

    def get(self, document_id: str) -> DocumentVersionModel:
        row = self.database.fetch_one("SELECT * FROM document_versions WHERE document_id=?", (document_id,))
        if not row: raise NotFoundError("Document version not found")
        return self._row(row)

    def active_chunks(self, as_of: str | None = None, include_historical: bool = False):
        statuses = ("active", "replaced", "expired") if include_historical else ("active",)
        placeholders = ",".join("?" for _ in statuses)
        rows = self.database.fetch_all(
            f"SELECT * FROM document_versions WHERE status IN ({placeholders}) AND (? IS NULL OR effective_date IS NULL OR effective_date<=?) AND (? IS NULL OR expiration_date IS NULL OR expiration_date>=?)",  # nosec B608 -- placeholders 由常量元组 statuses 生成，仅含 '?'
            (*statuses, as_of, as_of, as_of, as_of),
        )
        output = []
        for row in rows:
            source = Path(row["source_path"]); chunks_path = source.parent / "chunks.json"
            try:
                chunk_data = json.loads(chunks_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError) as exc:
                import logging
                logging.getLogger("mindgraph.documents").warning(
                    "chunk_load_failed", extra={"document_id": row["document_id"], "error": str(exc)}
                )
                continue
            for chunk in chunk_data:
                chunk["document_id"] = row["document_id"]
                chunk["document_version"] = row["version"]; chunk["logical_document_id"] = row["logical_document_id"]
                chunk["authority_level"] = row["authority_level"]; chunk["knowledge_category"] = row["knowledge_category"]
                chunk["document_status"] = row["status"]; chunk["document_title"] = row["title"]
                chunk["effective_date"] = row["effective_date"]; chunk["expiration_date"] = row["expiration_date"]
                output.append(chunk)
        return output

    @staticmethod
    def _row(row) -> DocumentVersionModel:
        return DocumentVersionModel(document_id=row["document_id"], logical_document_id=row["logical_document_id"], version=row["version"],
            title=row["title"], file_type=row["file_type"], knowledge_category=row["knowledge_category"], authority_level=row["authority_level"],
            effective_date=row["effective_date"], expiration_date=row["expiration_date"], status=row["status"], checksum=row["checksum"],
            supersedes_version=row["supersedes_version"], parsing_diagnostics=loads(row["parsing_diagnostics_json"], {}),
            created_at=row["created_at"], updated_at=row["updated_at"], indexed_at=row["indexed_at"], created_by=row["created_by"],
            workspace=row.get("workspace"), department=row.get("department"),
            acl_json=row.get("acl_json") or "{}", acl_public=bool(row.get("acl_public")))
