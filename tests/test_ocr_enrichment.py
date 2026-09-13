"""PR-07｜可替换 OCR Provider。

测试矩阵覆盖：扫描页成功、空结果/失败/部分页失败、低置信不采纳、
OCR 关闭时旧行为不变、诊断不含 OCR 全文。

**为什么主力用 fake provider**：本 PR 的职责是"边界 + 采纳策略"，不是 OCR 精度。
精度由文件末尾的 ``test_real_rapidocr_on_target_page`` 用真引擎 + 仓库内已提交的
纯图像页兜底（``data-sources/ocr/chinese-gov/rendered/``，已进 git）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from pdf_fixture import build_pdf
import pytest

from application.ocr_enrichment import DEFAULT_MIN_CONFIDENCE, ocr_pages
from domain.models import ParsedDocument, ParsedElement
from infrastructure.database import ProductDatabase
from infrastructure.ocr import get_ocr_provider
from infrastructure.ocr.base import NullOcrProvider, OcrLine, OcrPageResult


@pytest.fixture()
def database(tmp_path: Path) -> ProductDatabase:
    db = ProductDatabase(tmp_path / "ocr.sqlite3")
    db.initialize()
    return db


class _FakeProvider:
    """按页返回预设结果的 provider，并记录被请求的页。"""

    def __init__(self, results: dict[int, OcrPageResult], *, name="fake") -> None:
        self.results = results
        self.name = name
        self.model = "fake-model"
        self.version = "1.0"
        self.requested: list[int] = []

    def ocr_page(self, image_bytes: bytes, page_number: int) -> OcrPageResult:
        self.requested.append(page_number)
        return self.results.get(page_number, OcrPageResult(page_number=page_number, error="no_result"))


def _line(text: str, confidence: float = 0.95) -> OcrLine:
    return OcrLine(text=text, confidence=confidence)


def _document(ocr_pages_list=(2,)) -> ParsedDocument:
    return ParsedDocument(
        document_id="doc", document_name="scan.pdf", file_type="pdf", checksum="sum",
        parser_name="layout-aware-pypdf", parser_version="1.1.0",
        elements=[ParsedElement(element_type="paragraph", text="第一页有文字层", order=0, page_number=1)],
        warnings=[], ocr_required_pages=list(ocr_pages_list), metadata={"page_count": 3},
    )


@pytest.fixture()
def renderer(monkeypatch: pytest.MonkeyPatch):
    """替换渲染器：测试关心 OCR 逻辑，不关心位图。"""
    import application.ocr_enrichment as module

    monkeypatch.setattr(module, "render_pdf_page", lambda data, page, dpi=150: b"PNGDATA")
    return module


# ── 成功路径 ──────────────────────────────────────────────────────────────

def test_scan_page_becomes_searchable_with_page_number(renderer) -> None:
    provider = _FakeProvider({2: OcrPageResult(page_number=2, lines=(_line("第二条 费用标准"),),
                                               confidence=0.93, provider="fake")})
    report = ocr_pages(_document(), b"pdf", provider=provider)
    document = report["document"]

    assert report["adopted_pages"] == [2]
    ocr_elements = [item for item in document.elements if item.ocr_derived]
    assert [item.text for item in ocr_elements] == ["第二条 费用标准"]
    # 页码可追溯 —— 验收标准要求
    assert all(item.page_number == 2 for item in ocr_elements)
    # 采纳后不再是"需要 OCR"
    assert 2 not in document.ocr_required_pages
    # 原有元素不受影响
    assert document.elements[0].text == "第一页有文字层"


def test_original_document_is_not_mutated(renderer) -> None:
    provider = _FakeProvider({2: OcrPageResult(page_number=2, lines=(_line("x"),), confidence=0.9)})
    original = _document()
    before = len(original.elements)
    ocr_pages(original, b"pdf", provider=provider)
    assert len(original.elements) == before, "必须返回新文档，不得原地改"


# ── 失败与低置信：不污染索引 ──────────────────────────────────────────────

def test_low_confidence_page_is_not_adopted(renderer) -> None:
    provider = _FakeProvider({2: OcrPageResult(page_number=2, lines=(_line("疑似乱码"),),
                                               confidence=0.31, provider="fake")})
    report = ocr_pages(_document(), b"pdf", provider=provider, min_confidence=DEFAULT_MIN_CONFIDENCE)
    assert report["adopted_pages"] == []
    assert report["rejected_pages"] == [2]
    assert not [item for item in report["document"].elements if item.ocr_derived]
    assert 2 in report["document"].ocr_required_pages, "未采纳的页保持待 OCR，便于重试"


def test_empty_result_is_rejected(renderer) -> None:
    provider = _FakeProvider({2: OcrPageResult(page_number=2, lines=(), confidence=0.0)})
    report = ocr_pages(_document(), b"pdf", provider=provider)
    assert report["adopted_pages"] == []
    assert report["results"][0]["ok"] is False


def test_error_page_is_rejected(renderer) -> None:
    provider = _FakeProvider({2: OcrPageResult(page_number=2, error="TimeoutError: too slow")})
    report = ocr_pages(_document(), b"pdf", provider=provider)
    assert report["rejected_pages"] == [2]
    assert "TimeoutError" in report["results"][0]["error"]


def test_partial_failure_keeps_good_pages(renderer) -> None:
    """验收标准：失败页不污染索引 —— 且不能连带把好页一起丢掉。"""
    provider = _FakeProvider({
        2: OcrPageResult(page_number=2, lines=(_line("第二页识别成功"),), confidence=0.95),
        3: OcrPageResult(page_number=3, error="engine crashed"),
    })
    report = ocr_pages(_document(ocr_pages_list=(2, 3)), b"pdf", provider=provider)
    assert report["adopted_pages"] == [2]
    assert report["rejected_pages"] == [3]
    assert [item.text for item in report["document"].elements if item.ocr_derived] == ["第二页识别成功"]


def test_render_failure_is_a_page_failure(renderer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(renderer, "render_pdf_page", lambda data, page, dpi=150: None)
    provider = _FakeProvider({})
    report = ocr_pages(_document(), b"pdf", provider=provider)
    assert report["rejected_pages"] == [2]
    assert report["results"][0]["error"] == "render_failed"


# ── 关闭态与 provider 选择 ────────────────────────────────────────────────

def test_disabled_provider_leaves_document_untouched(renderer) -> None:
    """OCR 关闭时旧行为必须保持 —— 这是回滚路径的正确性保证。"""
    report = ocr_pages(_document(), b"pdf", provider=NullOcrProvider())
    assert report["enabled"] is False
    assert report["adopted_pages"] == []
    # 必须是**明确**的关闭原因，而不是"恰好没识别出文本" —— 后者在换了
    # provider 实现后会静默变成"OCR 跑过了但没结果"，语义完全不同。
    assert report["results"][0]["error"] == "ocr_disabled"
    assert report["document"].elements == _document().elements
    assert report["document"].ocr_required_pages == [2]


def test_no_ocr_pages_means_no_work(renderer) -> None:
    provider = _FakeProvider({})
    ocr_pages(_document(ocr_pages_list=()), b"pdf", provider=provider)
    assert provider.requested == []


def test_unknown_provider_name_falls_back_to_disabled() -> None:
    assert isinstance(get_ocr_provider("nope"), NullOcrProvider)
    assert isinstance(get_ocr_provider(""), NullOcrProvider)
    assert isinstance(get_ocr_provider("none"), NullOcrProvider)


# ── 敏感信息 ──────────────────────────────────────────────────────────────

def test_diagnostics_never_contain_ocr_text(renderer, caplog: pytest.LogCaptureFixture) -> None:
    """测试矩阵：敏感日志不含全文。"""
    secret = "机密条款 编号 ABC-123"
    provider = _FakeProvider({2: OcrPageResult(page_number=2, lines=(_line(secret),), confidence=0.99)})
    with caplog.at_level(logging.DEBUG):
        report = ocr_pages(_document(), b"pdf", provider=provider)
    for item in report["results"]:
        assert secret not in str(item)
    assert secret not in caplog.text


def test_result_dict_shape_is_statistical_only(renderer) -> None:
    provider = _FakeProvider({2: OcrPageResult(page_number=2, lines=(_line("甲乙丙"),), confidence=0.9)})
    item = ocr_pages(_document(), b"pdf", provider=provider)["results"][0]
    assert set(item) == {"page_number", "line_count", "char_count", "confidence", "provider",
                         "model", "version", "latency_ms", "error", "ok"}
    assert item["line_count"] == 1 and item["char_count"] == 3


# ── 接入上传链路（证明不是死代码）────────────────────────────────────────

@pytest.fixture()
def scanned_pdf() -> bytes:
    """3 页真 PDF，第 2 页空白 → 被 PDFParser 标为 ocr_required。"""
    return build_pdf(["page one reimbursement deadline policy text", "",
                      "page three client entertainment standard text"])


def test_upload_uses_ocr_when_enabled(database: ProductDatabase, tmp_path: Path,
                                      monkeypatch: pytest.MonkeyPatch, scanned_pdf: bytes) -> None:
    """开启 OCR 后，扫描页经上传链路进入可检索状态。"""
    from application.document_lifecycle_service import DocumentLifecycleService
    from infrastructure.settings import get_settings

    monkeypatch.setattr("application.ocr_enrichment.render_pdf_page",
                        lambda data, page, dpi=150: b"PNGDATA")
    provider = _FakeProvider({2: OcrPageResult(page_number=2, lines=(_line("第二条 OCR 文本"),),
                                               confidence=0.97)})
    monkeypatch.setenv("OCR_ENABLED", "true")
    get_settings.cache_clear()
    try:
        lifecycle = DocumentLifecycleService(database, tmp_path / "storage", ocr_provider=provider)
        record = lifecycle.create_version("scan.pdf", scanned_pdf, "doc-scan", "v1",
                                          "policy", "official_policy", status="active")
    finally:
        get_settings.cache_clear()

    assert record.status == "active", "OCR 成功后不应再是 parse_failed"
    assert record.parsing_diagnostics["ocr"]["adopted_pages"] == [2]
    chunks = lifecycle.active_chunks()
    texts = " ".join(str(chunk.get("text") or "") for chunk in chunks)
    assert "第二条 OCR 文本" in texts, "OCR 文本必须进入可检索 chunk"


def test_upload_without_ocr_still_fails_scanned_pages(database: ProductDatabase, tmp_path: Path,
                                                      scanned_pdf: bytes) -> None:
    """OCR 关闭（默认）时扫描页仍判 parse_failed —— 旧行为不变，即回滚路径。"""
    from application.document_lifecycle_service import DocumentLifecycleService

    lifecycle = DocumentLifecycleService(database, tmp_path / "storage2")
    record = lifecycle.create_version("scan2.pdf", scanned_pdf, "doc-scan2", "v1",
                                      "policy", "official_policy", status="active")
    assert record.status == "parse_failed"
    assert "ocr" not in record.parsing_diagnostics


# ── 真实引擎兜底 ──────────────────────────────────────────────────────────

def test_real_rapidocr_on_target_page() -> None:
    """真引擎 + 仓库内已提交的**纯图像**页（无文字层，必须靠 OCR）。"""
    pytest.importorskip("rapidocr_onnxruntime")
    from infrastructure.ocr.rapidocr_provider import RapidOcrProvider

    image_path = ("data-sources/ocr/chinese-gov/rendered/"
                  "guowuyuan-gongbao-202524_p2.png")
    try:
        image = open(image_path, "rb").read()  # noqa: SIM115 -- 一次性读取即关，无需 with
    except FileNotFoundError:
        pytest.skip("纯图像靶标不在工作区（换环境会缺），跳过真实引擎验证")

    provider = RapidOcrProvider(timeout_seconds=120)
    result = provider.ocr_page(image, page_number=2)

    assert result.ok, f"真实 OCR 失败：{result.error}"
    assert result.confidence >= 0.6, f"置信度过低：{result.confidence}"
    assert "国务院" in result.text, f"未识别出预期内容：{result.text[:120]}"
    # 结果自带来源信息，便于事后追责/复现
    assert result.provider == "rapidocr" and result.model and result.version != "unknown"
