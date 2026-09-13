"""OCR Provider 工厂。

``none`` 是**默认**：OCR 属于可选能力，装了引擎也不自动启用，
必须显式配置 ``OCR_ENABLED=true``。这样"没装引擎"和"装了但没开"是两件
看得出来的不同的事，而不是靠 ImportError 隐式表达。
"""
from __future__ import annotations

from infrastructure.ocr.base import NullOcrProvider, OCRProvider


def get_ocr_provider(name: str, *, timeout_seconds: float = 30.0) -> OCRProvider:
    """按名字取 provider；未知名字**返回关闭态**而不是抛错。

    理由：OCR 是可选增强，配置写错不该让整个上传链路起不来；
    但会打 WARNING，不至于静默降级。
    """
    import logging

    if not name or name == "none":
        return NullOcrProvider()
    if name == "rapidocr":
        from infrastructure.ocr.rapidocr_provider import RapidOcrProvider

        return RapidOcrProvider(timeout_seconds=timeout_seconds)
    # extra 的键不能是 "name"：它会撞 LogRecord 内置字段并抛 KeyError，
    # 于是"配置写错"会变成 500 而不是优雅降级 —— 正是这里要避免的。
    logging.getLogger("mindgraph.ocr").warning("unknown_ocr_provider", extra={"provider_name": name})
    return NullOcrProvider()


__all__ = ["NullOcrProvider", "OCRProvider", "get_ocr_provider"]
