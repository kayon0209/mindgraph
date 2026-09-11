"""PR-06｜页级摄取状态机与检查点。

## 它补的是什么缺口

页级解析**早就存在**（``infrastructure/parsers/`` 四个 parser、``ParsedElement.page_number``、
``ParsedDocument.ocr_required_pages``）。真正缺的是：解析结果**只存在于内存里**，
解析完就随进程丢弃 —— 于是「失败在哪一页」「重试时跳过已成功的页」
在物理上做不到，因为没有一页的成功记录可查。

本模块只做两件事，不重写解析层：

1. 把**已经算出来**的页级产物落库（page_artifacts）；
2. 给摄取过程一个可查询的状态机（ingestion_jobs）。

## 状态机

``registered → extracting → parsed / ocr_required / failed → chunked``

``parsed`` 与 ``ocr_required`` 都可以回到 ``extracting``：前者是重新解析，
后者是等 OCR 完成后补跑（PR-07 的接入点）。

## 设计取舍

- **落库失败不影响上传**：摄取记录是观测与恢复能力，不是文档入库的前置条件。
  ``DocumentLifecycleService`` 以 fail-soft 方式调用，落库异常只进诊断。
- **分页解析是可选能力**：只有声明了 ``parse_pages`` 的 parser 才支持按页重试，
  由 ``supports_paged()`` 探测。不支持时退化为整份重解析，且会在报告里写明
  ``paged_retry=False`` —— 不假装省下了工作。
"""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from typing import Any

from domain.errors import ConflictError, NotFoundError
from infrastructure.database import ProductDatabase, dumps, loads
from infrastructure.parsers import default_parser_registry
from infrastructure.parsers.base import supports_paged

# ── 作业状态 ──────────────────────────────────────────────────────────────
REGISTERED = "registered"
EXTRACTING = "extracting"
PARSED = "parsed"
OCR_REQUIRED = "ocr_required"
FAILED = "failed"
CHUNKED = "chunked"

JOB_TRANSITIONS: dict[str, set[str]] = {
    REGISTERED: {EXTRACTING, FAILED},
    EXTRACTING: {PARSED, OCR_REQUIRED, FAILED},
    PARSED: {EXTRACTING, CHUNKED},
    OCR_REQUIRED: {EXTRACTING, FAILED},
    FAILED: {EXTRACTING},
    CHUNKED: set(),
}

# ── 页状态 ────────────────────────────────────────────────────────────────
PAGE_PARSED = "parsed"
PAGE_OCR_REQUIRED = "ocr_required"
PAGE_FAILED = "failed"
PAGE_STATUSES = (PAGE_PARSED, PAGE_OCR_REQUIRED, PAGE_FAILED)

# 只有成功解析的页才在重试时被跳过
_RETRY_SKIPPED_STATUSES = (PAGE_PARSED,)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _page_checksum(elements: list[Any]) -> str:
    """页文本指纹：同一页重解析后若文本一致，指纹就一致（用于判断是否真的要重跑）。"""
    joined = "\n".join(str(getattr(item, "text", "") or "") for item in elements)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


class PageIngestionService:
    def __init__(self, database: ProductDatabase, registry=None, now=None) -> None:
        self.database = database
        self.registry = registry or default_parser_registry
        self._now = now or _utc_now_iso

    # ── 作业生命周期 ──────────────────────────────────────────────────────

    def register(self, *, document_id: str, logical_document_id: str, version: str,
                 filename: str, checksum: str) -> dict[str, Any]:
        """登记摄取作业。同一 document_id 重复提交是幂等的（返回既有作业）。"""
        existing = self.database.fetch_one("SELECT * FROM ingestion_jobs WHERE job_id=?", (document_id,))
        if existing:
            return self._job_row(existing)
        now = self._now()
        self.database.execute(
            "INSERT INTO ingestion_jobs (job_id, document_id, logical_document_id, version, filename,"
            " source_checksum, status, attempt, created_at, updated_at) VALUES (?,?,?,?,?,?,?,0,?,?)",
            (document_id, document_id, logical_document_id, version, filename, checksum, REGISTERED, now, now),
        )
        return self.get_job(document_id)

    def _transition(self, job_id: str, target: str) -> None:
        job = self.get_job(job_id)
        if target not in JOB_TRANSITIONS.get(job["status"], set()):
            raise ConflictError(f"Invalid ingestion transition: {job['status']} -> {target}")
        self.database.execute(
            "UPDATE ingestion_jobs SET status=?, updated_at=? WHERE job_id=?", (target, self._now(), job_id)
        )

    def run(self, job_id: str, data: bytes, filename: str, *, only_pages: list[int] | None = None) -> dict[str, Any]:
        """执行（或重跑）摄取。``only_pages`` 为 None 时解析整份文档。

        返回报告含 ``requested_pages`` / ``processed_pages`` / ``skipped_pages``，
        让「到底跑了哪些页」可核对 —— 这是「不重跑成功页」唯一的证据。
        """
        job = self.get_job(job_id)
        parser = self.registry.get(filename)
        self._transition(job_id, EXTRACTING)

        attempt = int(job["attempt"]) + 1
        self.database.execute(
            "UPDATE ingestion_jobs SET attempt=?, parser_name=?, parser_version=?, updated_at=? WHERE job_id=?",
            (attempt, parser.name, parser.version, self._now(), job_id),
        )

        paged = only_pages is not None and supports_paged(parser)
        requested: list[int] | None = None  # 解析抛异常时也要能返回报告
        try:
            if paged:
                selected = sorted(set(only_pages or []))
                parsed = parser.parse_pages(data, filename, selected)
                requested = selected
            else:
                parsed = parser.parse(data, filename)
                requested = None
        except Exception as exc:
            self.database.execute(
                "UPDATE ingestion_jobs SET status=?, failure_reason=?, diagnostics_json=?, updated_at=? WHERE job_id=?",
                (FAILED, f"{type(exc).__name__}: {exc}", dumps({"stage": "parse", "attempt": attempt}), self._now(), job_id),
            )
            return {"job_id": job_id, "status": FAILED, "attempt": attempt,
                    "requested_pages": requested, "processed_pages": [], "skipped_pages": [],
                    "paged_retry": paged, "failure_reason": f"{type(exc).__name__}: {exc}"}

        pages = self._group_by_page(parsed)
        total_pages = (parsed.metadata or {}).get("page_count")
        if requested is not None:
            # 只处理被请求的页；其余页保持原状（这就是"不重跑成功页"）
            processed = sorted(set(requested or []))
        elif total_pages:
            # 整份解析时按总页数铺满：解析不出任何元素的页**必须留痕**，
            # 否则"这一页失败了"会表现为"这一页不存在"，失败根本无法定位。
            processed = list(range(1, int(total_pages) + 1))
        else:
            processed = sorted(pages)

        ocr_pages = sorted({number for number in processed if number in set(parsed.ocr_required_pages or [])})
        failed_pages = []
        for page_number in processed:
            status = self._upsert_page(job_id, page_number, pages.get(page_number) or [], attempt, ocr_pages)
            if status == PAGE_FAILED:
                failed_pages.append(page_number)

        if failed_pages:
            status = FAILED
        elif ocr_pages:
            status = OCR_REQUIRED
        else:
            status = PARSED

        known = {row["page_number"] for row in self.pages(job_id)}
        skipped = sorted(known - set(processed))
        diagnostics = {
            "parser": parsed.parser_name, "parser_version": parsed.parser_version,
            "page_count": (parsed.metadata or {}).get("page_count"),
            "ocr_required_pages": ocr_pages, "failed_pages": failed_pages,
            "attempt": attempt, "paged": paged, "warnings": list(parsed.warnings or []),
        }
        self.database.execute(
            "UPDATE ingestion_jobs SET status=?, failure_reason=?, diagnostics_json=?, updated_at=? WHERE job_id=?",
            (status, None if status != FAILED else f"{len(failed_pages)} page(s) produced no extractable text",
             dumps(diagnostics), self._now(), job_id),
        )
        return {
            "job_id": job_id, "status": status, "attempt": attempt,
            "requested_pages": requested, "processed_pages": processed, "skipped_pages": skipped,
            "paged_retry": paged, "ocr_required_pages": ocr_pages, "failed_pages": failed_pages,
        }

    def retry(self, job_id: str, data: bytes, filename: str) -> dict[str, Any]:
        """只重跑**未成功**的页。

        没有任何历史页记录时（首次运行）退化为全量解析。
        """
        existing = self.pages(job_id)
        if not existing:
            return self.run(job_id, data, filename)
        pending = [row["page_number"] for row in existing if row["status"] not in _RETRY_SKIPPED_STATUSES]
        if not pending:
            # 没有待重试的页就不解析任何东西 —— paged_retry 必须如实为 False，
            # 否则报表会声称"按页重试省下了工作"，而实际上什么都没跑。
            return {"job_id": job_id, "status": self.get_job(job_id)["status"],
                    "attempt": int(self.get_job(job_id)["attempt"]),
                    "requested_pages": [], "processed_pages": [],
                    "skipped_pages": [row["page_number"] for row in existing],
                    "paged_retry": False, "nothing_to_retry": True}
        parser = self.registry.get(filename)
        report = self.run(job_id, data, filename, only_pages=pending if supports_paged(parser) else None)
        report["nothing_to_retry"] = False
        return report

    def finalize(self, job_id: str, chunk_count: int) -> dict[str, Any]:
        self._transition(job_id, CHUNKED)
        self.database.execute(
            "UPDATE ingestion_jobs SET chunk_count=?, updated_at=? WHERE job_id=?", (chunk_count, self._now(), job_id)
        )
        return self.get_job(job_id)

    # ── 查询 ──────────────────────────────────────────────────────────────

    def get_job(self, job_id: str) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM ingestion_jobs WHERE job_id=?", (job_id,))
        if not row:
            raise NotFoundError(f"Ingestion job not found: {job_id}")
        return self._job_row(row)

    def pages(self, job_id: str) -> list[dict[str, Any]]:
        rows = self.database.fetch_all("SELECT * FROM page_artifacts WHERE job_id=? ORDER BY page_number", (job_id,))
        return [dict(row) for row in rows]

    def page(self, job_id: str, page_number: int) -> dict[str, Any]:
        row = self.database.fetch_one(
            "SELECT * FROM page_artifacts WHERE job_id=? AND page_number=?", (job_id, page_number)
        )
        if not row:
            raise NotFoundError(f"Page artifact not found: {job_id}#{page_number}")
        return dict(row)

    # ── 内部 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _group_by_page(parsed) -> dict[int, list[Any]]:
        grouped: dict[int, list[Any]] = {}
        for element in parsed.elements:
            number = getattr(element, "page_number", None)
            if number is None:
                continue
            grouped.setdefault(int(number), []).append(element)
        return grouped

    def _upsert_page(self, job_id: str, page_number: int, elements: list[Any], attempt: int,
                     ocr_pages: list[int] | None = None) -> str:
        text = "\n".join(str(getattr(item, "text", "") or "") for item in elements).strip()
        if page_number in set(ocr_pages or ()):
            # parser 明确说"这页需要 OCR"比"这页没文本"更具体 —— 先采信它，
            # 否则空白扫描页会被记成解析失败，而失败重试也救不了一页扫描件。
            status = PAGE_OCR_REQUIRED
        elif not text:
            status = PAGE_FAILED
        else:
            status = PAGE_PARSED
        now = self._now()
        self.database.execute(
            "INSERT INTO page_artifacts (job_id, page_number, status, checksum, char_count, element_count,"
            " attempt, failure_reason, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(job_id, page_number) DO UPDATE SET status=excluded.status, checksum=excluded.checksum,"
            " char_count=excluded.char_count, element_count=excluded.element_count, attempt=excluded.attempt,"
            " failure_reason=excluded.failure_reason, updated_at=excluded.updated_at",
            (job_id, page_number, status, _page_checksum(elements), len(text), len(elements), attempt,
             None if status != PAGE_FAILED else "page produced no extractable text", now, now),
        )
        return status

    @staticmethod
    def _job_row(row) -> dict[str, Any]:
        return {
            "job_id": row["job_id"], "document_id": row["document_id"],
            "logical_document_id": row["logical_document_id"], "version": row["version"],
            "filename": row["filename"], "source_checksum": row["source_checksum"],
            "status": row["status"], "parser_name": row["parser_name"], "parser_version": row["parser_version"],
            "attempt": int(row["attempt"] or 0), "page_count": row["page_count"], "chunk_count": row["chunk_count"],
            "failure_reason": row["failure_reason"], "diagnostics": loads(row["diagnostics_json"], {}),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }


def page_ingestion_report(job: dict[str, Any], pages: list[dict[str, Any]]) -> str:
    """人读的摄取报告：能回答「失败在哪一页、哪一步、用哪个 parser」。"""
    lines = [
        f"job {job['job_id']}  status={job['status']}  attempt={job['attempt']}"
        f"  parser={job['parser_name']}@{job['parser_version']}",
    ]
    if job.get("failure_reason"):
        lines.append(f"  失败原因：{job['failure_reason']}")
    for item in pages:
        lines.append(
            f"  page {item['page_number']:<4} {item['status']:<12} chars={item['char_count']:<6}"
            f" elements={item['element_count']:<4} attempt={item['attempt']}"
            f" checksum={str(item['checksum'])[:12]}"
        )
    return "\n".join(lines)


__all__ = [
    "CHUNKED",
    "EXTRACTING",
    "FAILED",
    "JOB_TRANSITIONS",
    "OCR_REQUIRED",
    "PAGE_FAILED",
    "PAGE_OCR_REQUIRED",
    "PAGE_PARSED",
    "PARSED",
    "REGISTERED",
    "PageIngestionService",
    "page_ingestion_report",
]
