"""最小多页 PDF 生成器（页级摄取 / OCR 测试共用）。

为什么抽出来：``test_page_ingestion.py`` 与 ``test_ocr_enrichment.py`` 各存了一份
字节级相同的实现。"第 2 页空白 → PDFParser 判 ocr_required"是所有页级/OCR 断言的
共同前提，前提有两份副本，就迟早会分叉（改了 A 忘了 B，B 的测试结果就是假的）。
这里只留一份，谁改都得改到一处。

对象号从 3 起：1 = Catalog，2 = Pages，其余按入列顺序编号。
空字符串页写出的内容流不含任何文本 → 被 PDFParser 标为 ``ocr_required``。
"""

from __future__ import annotations


def build_pdf(page_texts: list[str]) -> bytes:
    """生成最小多页 PDF（未压缩内容流）。空白页会被 PDFParser 判为 ocr_required。"""
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
