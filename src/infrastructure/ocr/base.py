"""PR-07｜可替换 OCR Provider 的边界。

## 为什么要有这个抽象

``PDFParser`` 早已能**标记**哪些页需要 OCR（``ocr_required_pages``），但没有任何东西
去处理这些页：扫描件进不来，而且一旦接了某个引擎，就会被硬编码在解析路径里。
本模块只定义边界与结果形状，不绑定任何引擎。

## 结果必须自带"可信度"，不能只给文本

OCR 文本是**推测**出来的，不是文档里的既定事实。所以 ``OcrPageResult`` 强制携带
``confidence`` 与 ``provider/model/version``：下游据此决定要不要采纳，
而不是无条件把识别结果写进索引。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class OcrLine:
    """一行识别结果。``bbox`` 保留用于跨页续表恢复（PR-08）。"""

    text: str
    confidence: float
    bbox: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class OcrPageResult:
    page_number: int
    lines: tuple[OcrLine, ...] = ()
    confidence: float = 0.0      # 页级置信度：当前实现取行置信度均值
    provider: str = ""
    model: str = ""
    version: str = ""
    latency_ms: float = 0.0
    error: str | None = None     # 非空即失败；此时 lines 为空且不得被采纳

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines if line.text.strip())

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.text.strip())

    def to_dict(self) -> dict[str, Any]:
        """落诊断用。**刻意不含文本**：OCR 全文可能有敏感内容，日志与诊断只留可统计信息。"""
        return {
            "page_number": self.page_number,
            "line_count": len(self.lines),
            "char_count": len(self.text),
            "confidence": round(self.confidence, 4),
            "provider": self.provider,
            "model": self.model,
            "version": self.version,
            "latency_ms": round(self.latency_ms, 1),
            "error": self.error,
            "ok": self.ok,
        }


class OCRProvider(Protocol):
    """一页图像进、一页文本出。实现必须自带超时与失败降级。"""

    name: str
    model: str
    version: str

    def ocr_page(self, image_bytes: bytes, page_number: int) -> OcrPageResult: ...


class NullOcrProvider:
    """关闭态实现：不做任何事，返回明确失败。

    存在的意义是让"OCR 关闭"也是一个可替换的实现，而不是在调用处写一堆 ``if enabled``
    —— 后者会让每个调用点各自决定关闭时该返回什么。
    """

    name = "none"
    model = "none"
    version = "0"

    def ocr_page(self, image_bytes: bytes, page_number: int) -> OcrPageResult:
        return OcrPageResult(page_number=page_number, provider=self.name,
                             model=self.model, version=self.version, error="ocr_disabled")


__all__ = ["NullOcrProvider", "OCRProvider", "OcrLine", "OcrPageResult", "field"]
