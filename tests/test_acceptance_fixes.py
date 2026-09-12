"""验收审查修复：workbuddy PR-04/06/07 的跨 PR 集成缺陷。

P0：m4 索引 manifest 的切分口径必须与 ChunkingPolicy 同源（PR-03 单一
来源承诺）；历史硬编码 500/1200/50 在选了非 legacy 预设时谎报指纹，
让 PR-04 门禁基于假数据做拦截决策。

P1：上传路径双重解析——create_version 解析一次（入索引），页级记账
_record_page_ingestion 又对同一 data 完整重解析一次；且记账的解析不含
OCR 增补结果（_maybe_ocr 先跑、记账后跑），OCR 已采纳的页在页级账本上
仍是 ocr_required。修法：记账复用已解析的 ParsedDocument（run_from_parsed），
不再自己重新解析。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from infrastructure.database import ProductDatabase


@pytest.fixture
def db(tmp_path: Path) -> ProductDatabase:
    database = ProductDatabase(tmp_path / "fix.sqlite3")
    database.initialize()
    return database


# ── P0：m4 manifest 切分口径同源 ─────────────────────────────────────


def test_m4_manifest_records_active_policy_not_hardcoded(
    db: ProductDatabase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_embedding
):
    """选了非 legacy 预设后，m4 manifest 必须记录真实 policy——否则
    PR-04 指纹读谎报值，门禁决策失真。

    真调 build()（替身只换 embedding provider 与文档来源）：manifest 是门禁的
    输入，谎报口径会让门禁基于假数据拦截。这里断言"写进去的就是选中的那套口径"。
    """
    from index_build_fixture import make_index_service

    from application import chunking_policy as cp
    from infrastructure.settings import get_settings

    probe = cp.ChunkingPolicy(name="probe_v2", version="1", child_size=800, parent_size=3000, overlap=100)
    monkeypatch.setitem(cp._PRESETS, "probe_v2", probe)
    monkeypatch.setenv("CHUNKING_POLICY", "probe_v2")
    get_settings.cache_clear()
    try:
        manifest = make_index_service(db, tmp_path / "indexes").build()
    finally:
        get_settings.cache_clear()

    assert manifest["chunking_policy"] == probe.manifest_payload()
    # 硬编码 500 会在这里现形（不是靠 grep 源码里有没有 "child_size": 500）
    assert manifest["chunking_policy"]["child_size"] == 800
    assert manifest["chunking_policy"]["name"] == "probe_v2"


def test_m4_manifest_payload_matches_policy_shape(monkeypatch: pytest.MonkeyPatch):
    """manifest 的 chunking_policy 字段形状与 m3 路径一致（schema 同名），
    使 PR-04 指纹在两路径间可比。"""
    from application.chunking_policy import ChunkingPolicy

    policy = ChunkingPolicy.from_settings()
    payload = policy.manifest_payload()
    assert set(payload) == {"name", "version", "child_size", "parent_size", "overlap"}


# ── P1：页级记账复用已解析文档（消除双重解析 + OCR 状态分叉）──


def test_upload_parses_each_document_once(db: ProductDatabase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """「每份文档只解析一次」用行为证明：数解析器实际被调用的次数。

    原来这条断言的是源码里出现过 ``run_from_parsed`` 字样——重构就假红、留着
    字符串就假绿，而"有没有重复解析"这个真性质根本没被验证过。双重解析是性能与
    状态分叉问题（第二次解析不含 OCR 增补），只有计数能守住。
    """
    from application.document_lifecycle_service import DocumentLifecycleService
    from infrastructure.parsers import default_parser_registry

    parser = default_parser_registry.get("policy.md")
    calls: list[str] = []
    original_parse = parser.parse

    def counting_parse(data, filename, *args, **kwargs):
        calls.append(filename)
        return original_parse(data, filename, *args, **kwargs)

    monkeypatch.setattr(parser, "parse", counting_parse)

    lifecycle = DocumentLifecycleService(db, tmp_path / "storage")
    record = lifecycle.create_version(
        "policy.md", "# 差旅费\n十个工作日内报销。".encode(), "doc-once", "v1", "upload", "user_uploaded_reference"
    )

    assert record.status == "draft"
    assert calls == ["policy.md"], f"同一份文档被解析了 {len(calls)} 次（期望 1 次）"


def test_run_from_parsed_adopts_ocr_pages(db: ProductDatabase):
    """OCR 已采纳的页：页级账本记 parsed（不是 ocr_required）——
    消除「文档可检索、页级账本欠账」的状态分叉。"""
    from application.page_ingestion import PARSED, PageIngestionService
    from domain.models import ParsedDocument, ParsedElement
    from infrastructure.parsers import default_parser_registry

    parsed = ParsedDocument(
        document_id="doc-1", document_name="scan.pdf", file_type="pdf",
        checksum="chk", parser_name="pdf", parser_version="1",
        elements=[ParsedElement(element_type="paragraph", text="识别文本", order=0, page_number=1)],
        ocr_required_pages=[],  # OCR 增补后：该页已从待 OCR 列表移除
        metadata={"page_count": 1},
    )
    service = PageIngestionService(db, registry=default_parser_registry)
    service.register(document_id="doc-1", logical_document_id="l1", version="v1",
                     filename="scan.pdf", checksum="chk")
    report = service.run_from_parsed("doc-1", parsed, attempt=1)
    assert report["status"] == PARSED
    page = db.fetch_one("SELECT status FROM page_artifacts WHERE job_id=? AND page_number=1", ("doc-1",))
    assert page["status"] == PARSED


def test_run_from_parsed_marks_ocr_required_page(db: ProductDatabase):
    """未 OCR 的标记页：页级账本仍如实记 ocr_required（重试入口照常工作）。"""
    from application.page_ingestion import OCR_REQUIRED, PageIngestionService
    from domain.models import ParsedDocument
    from infrastructure.parsers import default_parser_registry

    parsed = ParsedDocument(
        document_id="doc-2", document_name="scan.pdf", file_type="pdf",
        checksum="chk", parser_name="pdf", parser_version="1",
        elements=[],
        ocr_required_pages=[1],  # 待 OCR
        metadata={"page_count": 1},
    )
    service = PageIngestionService(db, registry=default_parser_registry)
    service.register(document_id="doc-2", logical_document_id="l2", version="v1",
                     filename="scan.pdf", checksum="chk")
    report = service.run_from_parsed("doc-2", parsed, attempt=1)
    assert report["status"] == OCR_REQUIRED
    page = db.fetch_one("SELECT status FROM page_artifacts WHERE job_id=? AND page_number=1", ("doc-2",))
    assert page["status"] == OCR_REQUIRED


def test_run_from_parsed_state_machine_enforced(db: ProductDatabase):
    """run_from_parsed 同样走状态机（extracting → 终态），不绕过迁移校验。"""
    from application.page_ingestion import PageIngestionService
    from domain.errors import ConflictError
    from domain.models import ParsedDocument
    from infrastructure.parsers import default_parser_registry

    parsed = ParsedDocument(
        document_id="doc-3", document_name="a.md", file_type="md",
        checksum="chk", parser_name="md", parser_version="1",
        elements=[], metadata={},
    )
    service = PageIngestionService(db, registry=default_parser_registry)
    service.register(document_id="doc-3", logical_document_id="l3", version="v1",
                     filename="a.md", checksum="chk")
    service.run_from_parsed("doc-3", parsed, attempt=1)
    service.finalize("doc-3", chunk_count=2)  # parsed -> chunked
    with pytest.raises(ConflictError):
        service.run_from_parsed("doc-3", parsed, attempt=2)  # chunked 是终态


class _AdoptingOcr:
    """把指定页识别成固定文本的假 provider（记录被请求的页）。"""

    name, model, version = "fake-ocr", "fake-model", "1.0"

    def __init__(self, pages: dict[int, str]) -> None:
        self._pages = pages
        self.requested: list[int] = []

    def ocr_page(self, image_bytes: bytes, page_number: int):
        from infrastructure.ocr.base import OcrLine, OcrPageResult

        self.requested.append(page_number)
        text = self._pages.get(page_number)
        if text is None:
            return OcrPageResult(page_number=page_number, error="no_result")
        return OcrPageResult(page_number=page_number, lines=(OcrLine(text=text, confidence=0.97),),
                             confidence=0.97, provider=self.name)


def test_upload_records_pages_from_ocr_enriched_parse(db: ProductDatabase, tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch):
    """端到端：上传「第 1/3 页有文字层 + 第 2 页靠 OCR 采纳」的真 PDF 后，
    页级账本三页**全部**是 parsed、文档 active、且每份文档只解析一次。

    此前这条断言的是 ``inspect.getsource(create_version)`` 里有没有
    ``parsed=parsed`` 字样：改个形参名就假红，留着这串字符就假绿 —— 而
    「记账用的是 OCR 增补后那份 parsed」「没有二次解析」这两个真性质从未被验证。
    行为断言才能同时抓住两头：页级账本里第 2 页是 parsed（不是 ocr_required），
    且解析器只被调用一次（双重解析会变成两次）。
    """
    from pdf_fixture import build_pdf

    from application.document_lifecycle_service import DocumentLifecycleService
    from application.page_ingestion import PAGE_PARSED, PARSED
    from infrastructure.parsers import default_parser_registry
    from infrastructure.settings import get_settings

    monkeypatch.setattr("application.ocr_enrichment.render_pdf_page", lambda data, page, dpi=150: b"PNG")
    parser = default_parser_registry.get("scan.pdf")
    calls: list[str] = []
    original_parse = parser.parse

    def counting_parse(data, filename, *args, **kwargs):
        calls.append(filename)
        return original_parse(data, filename, *args, **kwargs)

    monkeypatch.setattr(parser, "parse", counting_parse)
    provider = _AdoptingOcr({2: "第二条 报销标准 OCR 文本"})
    monkeypatch.setenv("OCR_ENABLED", "true")
    get_settings.cache_clear()
    try:
        lifecycle = DocumentLifecycleService(db, tmp_path / "storage", ocr_provider=provider)
        record = lifecycle.create_version(
            # 文本长度取 test_ocr_enrichment.scanned_pdf 的同一组（PDFParser 对过短
            # 的页也会判 ocr_required，短文本会把"第 3 页有文字层"变成假前提）
            "scan.pdf", build_pdf(["page one reimbursement deadline policy text", "",
                                   "page three client entertainment standard text"]),
            "doc-ocr", "v1", "policy", "official_policy", status="active",
        )
    finally:
        get_settings.cache_clear()

    assert record.status == "active", "OCR 采纳后不该再是 parse_failed"
    assert provider.requested == [2], "只应请求被标记的那一页"
    pages = {
        row["page_number"]: row["status"]
        for row in db.fetch_all(
            "SELECT page_number, status FROM page_artifacts WHERE job_id=? ORDER BY page_number",
            (record.document_id,),
        )
    }
    # 三页都在账本里留痕，且 OCR 已采纳的页记 parsed（旧实现会记 ocr_required → 状态分叉）
    assert pages == {1: PAGE_PARSED, 2: PAGE_PARSED, 3: PAGE_PARSED}
    assert record.parsing_diagnostics["page_ingestion"]["status"] == PARSED
    # 双重解析在这里现形：旧实现 create_version 解析一次、记账再解析一次 → 2 次
    assert calls == ["scan.pdf"], f"同一份文档被解析了 {len(calls)} 次（期望 1 次）"
