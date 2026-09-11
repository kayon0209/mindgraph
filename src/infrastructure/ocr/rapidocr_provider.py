"""PR-07｜rapidocr-onnxruntime 实现。

选它的理由（写进 ADR/pyproject 的取舍说明）：纯 pip 安装、无系统二进制、
模型随 wheel 分发，与本项目「Local Profile 零外部依赖」一致。
已声明的 ``paddleocr`` extra 体积大且依赖 paddlepaddle，作为次选保留。

## 关于 timeout 的诚实说明

onnxruntime 的推理是同步阻塞调用，Python 线程无法真正中断它。这里的超时是
**"不再等待"**：超时后结果被丢弃并记为失败，但底层线程可能仍在跑完。
对 OCR 这种单次几秒的推理足够了；若要硬中断得上子进程。这一点写在这里，
免得有人把 ``timeout`` 当成强隔离来用。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import logging
import time

import numpy as np
from PIL import Image

from infrastructure.ocr.base import OcrLine, OcrPageResult

logger = logging.getLogger("mindgraph.ocr")


def _module_version() -> str:
    """引擎版本取不到时记 unknown，不影响 OCR 本身。"""
    try:
        from importlib import metadata

        return metadata.version("rapidocr-onnxruntime")
    except Exception:
        return "unknown"


def _as_bbox(raw) -> tuple[float, float, float, float] | None:
    """rapidocr 的框是 4 个角点；取外接矩形即可，够跨页续表恢复用（PR-08）。"""
    if not raw:
        return None
    points = [(float(p[0]), float(p[1])) for p in raw]
    if len(points) < 4:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


class RapidOcrProvider:
    name = "rapidocr"
    model = "onnxruntime-default"

    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._engine = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ocr")
        self.version = _module_version()

    def _get_engine(self):
        """懒加载：构造 provider 不该触发几十 MB 的模型加载。"""
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR  # 延迟导入：未装也不影响导入本模块

            self._engine = RapidOCR()
        return self._engine

    def ocr_page(self, image_bytes: bytes, page_number: int) -> OcrPageResult:
        started = time.perf_counter()
        try:
            future = self._executor.submit(self._recognize, image_bytes)
            raw, _elapse = future.result(timeout=self.timeout_seconds)
        except Exception as exc:
            logger.warning("ocr_page_failed", extra={"page": page_number, "error": type(exc).__name__})
            return OcrPageResult(page_number=page_number, provider=self.name, model=self.model,
                                 version=self.version, latency_ms=(time.perf_counter() - started) * 1000,
                                 error=f"{type(exc).__name__}: {exc}")

        lines = tuple(
            OcrLine(text=str(item[1]), confidence=float(item[2]),
                    bbox=_as_bbox(item[0]))
            for item in (raw or [])
            if str(item[1]).strip()
        )
        confidence = (sum(line.confidence for line in lines) / len(lines)) if lines else 0.0
        # 只记可统计信息：OCR 全文可能含敏感内容，不进日志
        logger.info("ocr_page_done", extra={"page": page_number, "lines": len(lines),
                                            "confidence": round(confidence, 3)})
        return OcrPageResult(page_number=page_number, lines=lines, confidence=confidence,
                             provider=self.name, model=self.model, version=self.version,
                             latency_ms=(time.perf_counter() - started) * 1000)

    def _recognize(self, image_bytes: bytes):
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
        return self._get_engine()(np.asarray(image))


__all__ = ["RapidOcrProvider"]
