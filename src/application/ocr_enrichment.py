"""PR-07｜把 OCR 结果并回页级解析产物。

## 它做什么

``PDFParser`` 标记了哪些页需要 OCR，但没人处理它们 —— 扫描件到此为止。
本模块对标记页调 OCR Provider，把识别结果变成 ``ParsedElement``（``ocr_derived=True``）
补进 ``ParsedDocument``，让后续切分与索引照常工作。

## 三条硬规则（都是"宁可少收，不可错收"）

1. **低置信不采纳**：低于阈值的页不产出任何 element。
   OCR 文本是推测，不是事实；把低置信文本写进索引等于让模型把猜测当依据引用。
2. **失败页不污染索引**：OCR 出错/超时/空结果的页保持"未解析"状态，不产出 element。
3. **OCR 关闭时行为不变**：provider 为 none 时直接返回空报告，不改动原文档。

## 缓存复用

不做额外缓存表：OCR 成功的页在 ``page_artifacts`` 里会变成 ``parsed``，
PR-06 的重试逻辑因此**天然跳过**它们。再建一张缓存表就是重复且会漂移的第二真源。
"""
from __future__ import annotations

import logging
from typing import Any

from domain.models import ParsedDocument, ParsedElement
from infrastructure.ocr.base import NullOcrProvider, OcrPageResult, OCRProvider
from infrastructure.page_renderer import render_pdf_page

logger = logging.getLogger("mindgraph.ocr")

# 低于该置信度的识别结果不进索引
DEFAULT_MIN_CONFIDENCE = 0.6


def ocr_pages(
    parsed: ParsedDocument,
    data: bytes,
    *,
    provider: OCRProvider | None = None,
    page_numbers: list[int] | None = None,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    dpi: int = 150,
) -> dict[str, Any]:
    """对需要 OCR 的页跑识别，返回报告与**新文档**（不原地修改入参）。

    报告含每页的置信度、耗时、是否采纳 —— 不落 OCR 全文（可能有敏感内容）。
    """
    if provider is None:
        provider = NullOcrProvider()
    targets = sorted(set(page_numbers if page_numbers is not None else parsed.ocr_required_pages))
    if not targets:
        return {"enabled": not isinstance(provider, NullOcrProvider), "provider": provider.name,
                "requested_pages": [], "adopted_pages": [], "rejected_pages": [],
                "results": [], "document": parsed}

    results: list[OcrPageResult] = []
    adopted_elements: list[ParsedElement] = []
    adopted_pages: list[int] = []
    rejected_pages: list[int] = []

    for page_number in targets:
        image = render_pdf_page(data, page_number, dpi=dpi) if parsed.file_type == "pdf" else None
        if image is None:
            results.append(OcrPageResult(page_number=page_number, provider=provider.name,
                                         model=provider.model, version=provider.version,
                                         error="render_failed"))
            rejected_pages.append(page_number)
            continue
        result = provider.ocr_page(image, page_number)
        results.append(result)
        if not result.ok or result.confidence < min_confidence:
            rejected_pages.append(page_number)
            logger.info("ocr_page_rejected", extra={
                "page": page_number, "confidence": round(result.confidence, 3),
                "error": result.error, "min_confidence": min_confidence})
            continue
        adopted_pages.append(page_number)
        for index, line in enumerate(result.lines):
            adopted_elements.append(ParsedElement(
                element_type="paragraph", text=line.text, order=index,
                page_number=page_number, source_ref=f"page:{page_number}", ocr_derived=True,
            ))

    merged = ParsedDocument(
        document_id=parsed.document_id, document_name=parsed.document_name,
        file_type=parsed.file_type, checksum=parsed.checksum,
        parser_name=parsed.parser_name, parser_version=parsed.parser_version,
        elements=[*parsed.elements, *adopted_elements],
        warnings=[*parsed.warnings, f"ocr:{provider.name}:{adopted_pages}" if adopted_pages
                  else f"ocr:{provider.name}:none_adopted"],
        # 采纳过的页不再是"需要 OCR"，未采纳的保持原标记，便于下次重试
        ocr_required_pages=[p for p in parsed.ocr_required_pages if p not in adopted_pages],
        metadata={**parsed.metadata, "ocr": {
            "provider": provider.name, "model": provider.model, "version": provider.version,
            "min_confidence": min_confidence, "adopted_pages": adopted_pages,
            "rejected_pages": rejected_pages,
        }},
    )
    return {
        "enabled": not isinstance(provider, NullOcrProvider),
        "provider": provider.name,
        "requested_pages": targets,
        "adopted_pages": adopted_pages,
        "rejected_pages": rejected_pages,
        "min_confidence": min_confidence,
        # to_dict() 刻意不含文本：诊断里留统计信息，不留 OCR 全文
        "results": [item.to_dict() for item in results],
        "document": merged,
    }


__all__ = ["DEFAULT_MIN_CONFIDENCE", "ocr_pages"]
