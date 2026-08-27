"""OCR 验证：rapidocr_onnxruntime（纯 pip，无外部二进制）替代 Tesseract。

对 data-sources/ocr/chinese-gov/rendered/ 全部 PNG 做中文识别。
核心靶标：guowuyuan-gongbao-202524_p2/p3.png（真·纯图像页，源文字层=0）。
运行（managed env）：python data-sources/ocr/chinese-gov/ocr_verify_rapidocr.py
"""
from __future__ import annotations

from pathlib import Path

from rapidocr_onnxruntime import RapidOCR

RENDERED = Path(__file__).resolve().parents[0] / "rendered"


def main() -> int:
    pngs = sorted(RENDERED.glob("*.png"))
    if not pngs:
        print("NO PNG TARGETS")
        return 1
    print(f"targets: {len(pngs)}")
    engine = RapidOCR()
    any_ok = False
    for png in pngs:
        result, _elapse = engine(str(png))
        texts = [line[1] for line in result] if result else []
        joined = "".join(texts)
        ok = len(joined.strip()) > 0
        any_ok |= ok
        print(f"=== {png.name} | chars={len(joined)} | {'OK' if ok else 'EMPTY'} ===")
        print(joined[:300])
        print()
    print("SUMMARY:", "OCR PIPELINE OK (non-empty text on >=1 target)" if any_ok else "NO TEXT RECOGNIZED")
    return 0 if any_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
