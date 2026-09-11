"""PR-06｜页级摄取状态机与检查点。

覆盖「验收标准」三条 + 「测试矩阵」四条：

- 可回答失败在哪一页、哪一步、用哪个 parser → ``test_failure_is_located_to_a_page``
- 重试不重跑成功页                          → ``test_retry_only_reruns_failed_pages``
- 旧上传 API 行为兼容                       → ``test_create_version_still_works_and_records_pages``
- 中途失败可恢复 / 幂等 / 坏 PDF / 路径隔离  → 见下

**为什么用 fake parser 而不是真 PDF 做主力测试**：本 PR 的职责是状态机与持久化，
不是解析算法。用可注入的 fake 才能断言「重试**只请求了**失败的页」——
这是"不重跑成功页"唯一的直接证据，真 PDF 上无法观测。
真实 PDF 的端到端由文件末尾的 ``test_real_pdf_end_to_end`` 兜底。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from application.document_lifecycle_service import DocumentLifecycleService
from application.page_ingestion import (
    CHUNKED,
    FAILED,
    OCR_REQUIRED,
    PAGE_FAILED,
    PAGE_PARSED,
    PARSED,
    REGISTERED,
    PageIngestionService,
    page_ingestion_report,
)
from domain.errors import ConflictError, NotFoundError
from domain.models import ParsedDocument, ParsedElement
from infrastructure.database import ProductDatabase


class _FakeParser:
    """可观测的 parser：记录每次被请求解析哪些页。"""

    name, version = "fake-parser", "1.0.0"

    def __init__(self, pages: dict[int, str], *, ocr_pages: set[int] | None = None,
                 fail_on_pages: set[int] | None = None, raise_on_parse: Exception | None = None) -> None:
        self.pages = pages
        self.ocr_pages = ocr_pages or set()
        self.fail_on_pages = fail_on_pages or set()
        self.raise_on_parse = raise_on_parse
        self.calls: list[list[int] | None] = []  # None = 整份解析

    def supports(self, file_type: str) -> bool:
        return True

    def _document(self, name: str, numbers: list[int]) -> ParsedDocument:
        elements = []
        for number in sorted(numbers):
            text = self.pages.get(number, "")
            if number in self.fail_on_pages:
                continue
            for index, line in enumerate(text.splitlines() or [""]):
                if line.strip():
                    elements.append(ParsedElement(element_type="paragraph", text=line, order=index,
                                                  page_number=number))
        return ParsedDocument(
            document_id="doc", document_name=name, file_type="pdf", checksum="sum",
            parser_name=self.name, parser_version=self.version, elements=elements,
            warnings=[], ocr_required_pages=sorted(self.ocr_pages & set(numbers)),
            metadata={"page_count": len(self.pages)},
        )

    def parse(self, data: bytes, document_name: str) -> ParsedDocument:
        self.calls.append(None)
        if self.raise_on_parse:
            raise self.raise_on_parse
        return self._document(document_name, sorted(self.pages))

    def parse_pages(self, data: bytes, document_name: str, page_numbers: list[int]) -> ParsedDocument:
        self.calls.append(sorted(page_numbers))
        return self._document(document_name, page_numbers)


class _FakeRegistry:
    def __init__(self, parser: _FakeParser) -> None:
        self.parser = parser

    def get(self, filename: str) -> _FakeParser:
        return self.parser


@pytest.fixture()
def database(tmp_path: Path) -> ProductDatabase:
    db = ProductDatabase(tmp_path / "test.sqlite3")
    db.initialize()
    return db


def _service(database: ProductDatabase, parser: _FakeParser) -> PageIngestionService:
    return PageIngestionService(database, registry=_FakeRegistry(parser))


_PAGES = {1: "第一页内容", 2: "第二页内容", 3: "第三页内容"}
_MD_BYTES = "# 报销\n\n时限 30 天。".encode()


# ── 状态机 ────────────────────────────────────────────────────────────────

def test_job_starts_registered(database: ProductDatabase) -> None:
    service = PageIngestionService(database)
    job = service.register(document_id="d1", logical_document_id="doc", version="v1",
                           filename="a.pdf", checksum="c")
    assert job["status"] == REGISTERED
    assert job["attempt"] == 0


def test_register_is_idempotent(database: ProductDatabase) -> None:
    """重复提交同一 document_id 必须复用同一作业，而不是建第二个。"""
    service = PageIngestionService(database)
    first = service.register(document_id="d1", logical_document_id="doc", version="v1",
                             filename="a.pdf", checksum="c")
    second = service.register(document_id="d1", logical_document_id="doc", version="v1",
                              filename="a.pdf", checksum="c")
    assert first["job_id"] == second["job_id"]
    assert database.fetch_one("SELECT COUNT(*) AS n FROM ingestion_jobs")["n"] == 1


def test_illegal_transition_is_rejected(database: ProductDatabase) -> None:
    service = _service(database, _FakeParser(_PAGES))
    service.register(document_id="d1", logical_document_id="doc", version="v1",
                     filename="a.pdf", checksum="c")
    with pytest.raises(ConflictError):
        service.finalize("d1", 0)  # registered 不能直接到 chunked


def test_unknown_job_raises_not_found(database: ProductDatabase) -> None:
    with pytest.raises(NotFoundError):
        PageIngestionService(database).get_job("nope")


# ── 落库：失败定位 ────────────────────────────────────────────────────────

def test_pages_are_persisted_with_checksum_and_parser_version(database: ProductDatabase) -> None:
    parser = _FakeParser(_PAGES)
    service = _service(database, parser)
    service.register(document_id="d1", logical_document_id="doc", version="v1", filename="a.pdf", checksum="c")
    service.run("d1", b"data", "a.pdf")

    pages = service.pages("d1")
    assert [item["page_number"] for item in pages] == [1, 2, 3]
    assert all(item["status"] == PAGE_PARSED for item in pages)
    assert all(item["checksum"] for item in pages)
    assert service.get_job("d1")["parser_version"] == "1.0.0"


def test_failure_is_located_to_a_page(database: ProductDatabase) -> None:
    """验收标准：能回答失败在哪一页、用哪个 parser。"""
    parser = _FakeParser(_PAGES, fail_on_pages={2})
    service = _service(database, parser)
    service.register(document_id="d1", logical_document_id="doc", version="v1", filename="a.pdf", checksum="c")
    report = service.run("d1", b"data", "a.pdf")

    assert report["status"] == FAILED
    assert report["failed_pages"] == [2]
    page_two = service.page("d1", 2)
    assert page_two["status"] == PAGE_FAILED
    assert "no extractable text" in (page_two["failure_reason"] or "")
    # 报告可读：含失败页、parser、状态
    text = page_ingestion_report(service.get_job("d1"), service.pages("d1"))
    assert "page 2" in text and "failed" in text and "1.0.0" in text


def test_empty_page_is_marked_ocr_required(database: ProductDatabase) -> None:
    parser = _FakeParser(_PAGES, ocr_pages={3})
    service = _service(database, parser)
    service.register(document_id="d1", logical_document_id="doc", version="v1", filename="a.pdf", checksum="c")
    report = service.run("d1", b"data", "a.pdf")
    assert report["status"] == OCR_REQUIRED
    assert report["ocr_required_pages"] == [3]
    # 有文本的页照常成功，不因单页需 OCR 而整份失败
    assert service.page("d1", 1)["status"] == PAGE_PARSED


def test_parse_exception_marks_job_failed_with_reason(database: ProductDatabase) -> None:
    parser = _FakeParser(_PAGES, raise_on_parse=ValueError("Corrupted or unsupported PDF"))
    service = _service(database, parser)
    service.register(document_id="d1", logical_document_id="doc", version="v1", filename="a.pdf", checksum="c")
    report = service.run("d1", b"data", "a.pdf")
    assert report["status"] == FAILED
    assert "Corrupted" in report["failure_reason"]
    assert service.get_job("d1")["failure_reason"]


# ── 核心验收：重试不重跑成功页 ────────────────────────────────────────────

def test_retry_only_reruns_failed_pages(database: ProductDatabase) -> None:
    """失败页重跑，成功页**不重跑** —— 由 parser 收到的请求直接证明。"""
    parser = _FakeParser(_PAGES, fail_on_pages={2})
    service = _service(database, parser)
    service.register(document_id="d1", logical_document_id="doc", version="v1", filename="a.pdf", checksum="c")
    service.run("d1", b"data", "a.pdf")
    assert parser.calls == [None]  # 首次整份解析

    parser.fail_on_pages = set()  # 修好第 2 页
    report = service.retry("d1", b"data", "a.pdf")

    assert parser.calls == [None, [2]], "重试必须只请求失败页"
    assert report["processed_pages"] == [2]
    assert report["skipped_pages"] == [1, 3]
    assert report["status"] == PARSED
    assert service.page("d1", 2)["status"] == PAGE_PARSED
    # 成功页的 attempt 不变 —— 它们没有被重跑
    assert service.page("d1", 1)["attempt"] == 1
    assert service.page("d1", 2)["attempt"] == 2


def test_retry_without_any_failed_page_reruns_nothing(database: ProductDatabase) -> None:
    parser = _FakeParser(_PAGES)
    service = _service(database, parser)
    service.register(document_id="d1", logical_document_id="doc", version="v1", filename="a.pdf", checksum="c")
    service.run("d1", b"data", "a.pdf")
    report = service.retry("d1", b"data", "a.pdf")
    assert report["nothing_to_retry"] is True
    assert report["processed_pages"] == []
    assert parser.calls == [None], "没有失败页时不得再解析一次"


def test_retry_on_first_run_falls_back_to_full_parse(database: ProductDatabase) -> None:
    parser = _FakeParser(_PAGES)
    service = _service(database, parser)
    service.register(document_id="d1", logical_document_id="doc", version="v1", filename="a.pdf", checksum="c")
    report = service.retry("d1", b"data", "a.pdf")  # 还没跑过
    assert parser.calls == [None]
    assert report["status"] == PARSED


def test_reports_paged_retry_false_when_parser_cannot_do_pages(database: ProductDatabase) -> None:
    """parser 不支持分页时，必须**如实**说明是整份重解析，不能假装省了工作。"""

    class _NoPagedParser(_FakeParser):
        parse_pages = None  # 显式不支持（getattr 返回 None → supports_paged False）

    parser = _NoPagedParser(_PAGES, fail_on_pages={2})
    service = _service(database, parser)
    service.register(document_id="d1", logical_document_id="doc", version="v1", filename="a.pdf", checksum="c")
    service.run("d1", b"data", "a.pdf")
    report = service.retry("d1", b"data", "a.pdf")
    assert report["paged_retry"] is False


# ── 与既有上传路径的兼容 ──────────────────────────────────────────────────

def test_create_version_still_works_and_records_pages(database: ProductDatabase, tmp_path: Path) -> None:
    """旧同步入口行为不变，只是额外落了页级产物。"""
    lifecycle = DocumentLifecycleService(database, tmp_path / "storage")
    record = lifecycle.create_version("policy.md", _MD_BYTES, "doc-1", "v1",
                                      "policy", "official_policy", status="active")
    assert record.status == "active"
    assert record.document_id
    # 页级作业已登记并完成；Markdown 无分页能力 → 整份解析后落单页记录
    service = PageIngestionService(database)
    assert service.get_job(record.document_id)["status"] in (PARSED, CHUNKED)


def test_create_version_survives_ingestion_failure(database: ProductDatabase, tmp_path: Path) -> None:
    """摄取记录是观测能力，不是入库前置条件 —— 落库失败不得让上传失败。"""
    lifecycle = DocumentLifecycleService(database, tmp_path / "storage")
    original = database.execute

    def broken_execute(*args, **kwargs):
        if "ingestion_jobs" in str(args[0] if args else ""):
            raise RuntimeError("boom")
        return original(*args, **kwargs)

    database.execute = broken_execute  # type: ignore[method-assign]
    try:
        record = lifecycle.create_version("policy2.md", _MD_BYTES, "doc-2", "v1",
                                          "policy", "official_policy", status="active")
    finally:
        database.execute = original  # type: ignore[method-assign]
    assert record.status == "active", "摄取记录失败不得影响文档入库"


def test_page_artifacts_are_scoped_to_their_job(database: ProductDatabase) -> None:
    """路径与主体隔离：一个作业的页级记录不得泄漏到另一个作业。"""
    service = _service(database, _FakeParser(_PAGES))
    service.register(document_id="d1", logical_document_id="doc", version="v1", filename="a.pdf", checksum="c")
    service.register(document_id="d2", logical_document_id="doc", version="v2", filename="a.pdf", checksum="c")
    service.run("d1", b"data", "a.pdf")
    assert service.pages("d2") == []
    assert len(service.pages("d1")) == 3


def test_finalize_records_chunk_count(database: ProductDatabase) -> None:
    parser = _FakeParser(_PAGES)
    service = _service(database, parser)
    service.register(document_id="d1", logical_document_id="doc", version="v1", filename="a.pdf", checksum="c")
    service.run("d1", b"data", "a.pdf")
    job = service.finalize("d1", 12)
    assert job["status"] == CHUNKED
    assert job["chunk_count"] == 12


# ── 真实 PDF 端到端 ───────────────────────────────────────────────────────

def _build_pdf(page_texts: list[str]) -> bytes:
    """生成最小多页 PDF（未压缩内容流）。空白页会被 PDFParser 判为 ocr_required。

    对象号从 3 起：1 = Catalog，2 = Pages，其余按入列顺序编号。
    """
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects) + 2  # 1 与 2 预留给 Catalog / Pages

    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids: list[int] = []
    for text in page_texts:
        content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
        content_id = add(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
                         + content + b"\nendstream")
        page_ids.append(add(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 " + str(font_id).encode() + b" 0 R >> >> "
            b"/Contents " + str(content_id).encode() + b" 0 R >>"
        ))

    kids = b" ".join(str(pid).encode() + b" 0 R" for pid in page_ids)
    bodies = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(page_ids)).encode() + b" >>",
        *objects,
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += str(index).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 " + str(len(bodies) + 1).encode() + b"\n0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (b"trailer\n<< /Size " + str(len(bodies) + 1).encode()
            + b" /Root 1 0 R >>\nstartxref\n" + str(xref_at).encode() + b"\n%%EOF\n")
    return bytes(out)



def test_real_pdf_end_to_end(database: ProductDatabase) -> None:
    """真 PDF 兜底：验证 PDFParser.parse_pages 与状态机真的能配合工作。"""
    pytest.importorskip("pypdf")
    pdf = _build_pdf(["page one travel reimbursement deadline policy text", "",
                     "page three client entertainment standard policy text"])
    from infrastructure.parsers import default_parser_registry

    real = default_parser_registry.get("x.pdf")
    service = PageIngestionService(database, registry=default_parser_registry)
    service.register(document_id="d-real", logical_document_id="doc", version="v1",
                     filename="x.pdf", checksum="c")
    report = service.run("d-real", pdf, "x.pdf")

    assert real.name == "layout-aware-pypdf"
    assert report["status"] == OCR_REQUIRED, "第 2 页空白 → 应判需 OCR"
    assert report["ocr_required_pages"] == [2]
    assert service.page("d-real", 1)["status"] == PAGE_PARSED

    # 真正按页重跑：只请求第 2 页
    from unittest import mock

    with mock.patch.object(type(real), "parse_pages", wraps=real.parse_pages) as spy:
        retry_report = service.retry("d-real", pdf, "x.pdf")
    assert retry_report["paged_retry"] is True
    assert spy.call_count == 1
    assert spy.call_args.args[2] == [2], "重试只应请求失败页"
