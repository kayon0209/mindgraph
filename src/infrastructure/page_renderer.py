"""把 PDF 页渲染成位图，供 OCR 消费。

渲染是可选依赖（PyMuPDF）。缺依赖时不抛 ImportError 而是返回带 error 的结果，
让调用方按"该页 OCR 失败"处理 —— 一页渲染不出来不该让整份文档失败。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("mindgraph.render")


def render_pdf_page(data: bytes, page_number: int, *, dpi: int = 150) -> bytes | None:
    """返回 PNG 字节；失败返回 None。``page_number`` 从 1 开始。"""
    try:
        import pymupdf  # PyMuPDF 1.28+；旧名 fitz 已弃用
    except Exception as exc:
        try:
            import fitz as pymupdf  # type: ignore[no-redef]
        except Exception:
            logger.warning("page_render_unavailable", extra={"page": page_number, "error": str(exc)})
            return None
    try:
        with pymupdf.open(stream=data, filetype="pdf") as document:
            if page_number < 1 or page_number > len(document):
                logger.warning("page_render_out_of_range",
                               extra={"page": page_number, "pages": len(document)})
                return None
            pixmap = document[page_number - 1].get_pixmap(dpi=dpi)
            return bytes(pixmap.tobytes("png"))
    except Exception as exc:  # 单页渲染失败按页级失败处理
        logger.warning("page_render_failed", extra={"page": page_number, "error": str(exc)})
        return None


__all__ = ["render_pdf_page"]
