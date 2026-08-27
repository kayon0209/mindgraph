"""OCR 靶标验证脚本（供 Cursor 直接运行）。

对 rendered/ 下全部 PNG 跑 Tesseract 中文识别，输出每张图的识别字符数 + 前 500 字。
纯图像 PNG 必须靠图像识别（无文字层），用以验证 OCR 管线。
"""
import os
import sys

from PIL import Image
import pytesseract

# 若 tesseract 不在 PATH，取消下一行注释并指向实际可执行文件：
# pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

BASE = os.path.dirname(os.path.abspath(__file__))
RENDERED = os.path.join(BASE, "rendered")


def main():
    if not os.path.isdir(RENDERED):
        print(f"RENDERED DIR NOT FOUND: {RENDERED}")
        sys.exit(1)

    pngs = sorted(f for f in os.listdir(RENDERED) if f.lower().endswith(".png"))
    if not pngs:
        print("NO PNG TARGETS FOUND")
        sys.exit(1)

    print(f"FOUND {len(pngs)} PNG TARGETS\n")
    any_ok = False
    for f in pngs:
        path = os.path.join(RENDERED, f)
        img = Image.open(path)
        try:
            txt = pytesseract.image_to_string(img, lang="chi_sim")
        except Exception as e:  # tesseract 未装 / 中文包缺失
            print(f"=== {f} ({img.width}x{img.height}) | ERROR: {e} ===\n")
            continue
        stripped = txt.strip()
        mark = "OK" if stripped else "EMPTY"
        if stripped:
            any_ok = True
        print(f"=== {f} ({img.width}x{img.height}) | chars={len(stripped)} | {mark} ===")
        print(stripped[:500])
        print()

    print("SUMMARY:", "OCR PIPELINE OK (non-empty output on >=1 target)" if any_ok
          else "NO TEXT RECOGNIZED — check tesseract install / chi_sim language data")


if __name__ == "__main__":
    main()
