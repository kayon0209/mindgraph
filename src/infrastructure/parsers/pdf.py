from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from pypdf import PdfReader

from domain.models import ParsedDocument, ParsedElement


@dataclass(frozen=True)
class _Span:
    text: str
    x: float
    y: float


class PDFParser:
    name, version = "layout-aware-pypdf", "1.1.0"

    def supports(self, file_type: str) -> bool:
        return file_type.lower() == "pdf"

    def parse(self, data: bytes, document_name: str) -> ParsedDocument:
        try:
            reader = PdfReader(BytesIO(data))
        except Exception as exc:
            raise ValueError("Corrupted or unsupported PDF") from exc

        page_items: list[list[_Span]] = []
        warnings: list[str] = []
        ocr_pages: list[int] = []
        layout_pages = 0

        for page_number, page in enumerate(reader.pages, 1):
            spans = self._extract_spans(page)
            if not spans:
                text = (page.extract_text() or "").splitlines()
                spans = [_Span(text=line.strip(), x=0.0, y=float(index)) for index, line in enumerate(text) if line.strip()]
            page_items.append(spans)
            if not spans or len("".join(span.text for span in spans).strip()) < 20:
                ocr_pages.append(page_number)
                warnings.append(f"page {page_number}: ocr_required")
            if any(len(row) >= 2 for row in self._group_rows(spans)):
                layout_pages += 1

        repeated = self._repeated_headers(page_items)
        elements: list[ParsedElement] = []
        order = 0
        for page_number, spans in enumerate(page_items, 1):
            rows = self._group_rows(spans)
            current_table: list[list[str]] = []
            for row in rows:
                texts = [cell.text.strip() for cell in row if cell.text.strip()]
                if not texts:
                    continue
                row_text = " ".join(texts)
                if row_text in repeated:
                    continue
                if len(texts) >= 2:
                    current_table.append(texts)
                    continue
                if current_table:
                    order = self._emit_table(elements, current_table, order, page_number)
                    current_table = []
                elements.append(self._paragraph_element(row_text, order, page_number))
                order += 1
            if current_table:
                order = self._emit_table(elements, current_table, order, page_number)
            if page_number < len(page_items):
                elements.append(ParsedElement(element_type="page_break", text="", order=order, page_number=page_number))
                order += 1

        metadata: dict[str, Any] = {
            "page_count": len(reader.pages),
            "layout_mode": "visitor_text" if layout_pages else "fallback_text",
            "layout_pages": layout_pages,
        }
        if layout_pages:
            warnings.append("layout_aware_extraction_enabled")
        return ParsedDocument(
            document_id=hashlib.sha256(document_name.encode()).hexdigest()[:16],
            document_name=document_name,
            file_type="pdf",
            checksum=hashlib.sha256(data).hexdigest(),
            parser_name=self.name,
            parser_version=self.version,
            elements=elements,
            warnings=list(dict.fromkeys(warnings)),
            ocr_required_pages=ocr_pages,
            metadata=metadata,
        )

    def _extract_spans(self, page) -> list[_Span]:
        spans: list[_Span] = []

        def visitor(text: str, _cm, tm, *_args):
            cleaned = text.replace("\xa0", " ").strip()
            if not cleaned:
                return
            x = float(tm[4])
            y = float(tm[5])
            spans.append(_Span(cleaned, x, y))

        try:
            page.extract_text(visitor_text=visitor)
        except TypeError:
            # Older / alternate pypdf builds may not support visitor_text.
            pass
        except Exception:
            return []
        spans.sort(key=lambda item: (-round(item.y, 1), item.x, item.text))
        return spans

    @staticmethod
    def _group_rows(spans: list[_Span]) -> list[list[_Span]]:
        if not spans:
            return []
        grouped: dict[int, list[_Span]] = defaultdict(list)
        for span in spans:
            grouped[round(span.y / 4)] .append(span)
        rows = []
        for _, items in sorted(grouped.items(), key=lambda pair: (-max(span.y for span in pair[1]), min(span.x for span in pair[1]))):
            rows.append(sorted(items, key=lambda item: (item.x, item.text)))
        return rows

    @staticmethod
    def _repeated_headers(page_items: list[list[_Span]]) -> set[str]:
        boundary: Counter[str] = Counter()
        for spans in page_items:
            rows = PDFParser._group_rows(spans)
            texts = [" ".join(cell.text.strip() for cell in row if cell.text.strip()) for row in rows if any(cell.text.strip() for cell in row)]
            if not texts:
                continue
            boundary[texts[0]] += 1
            if len(texts) > 1:
                boundary[texts[-1]] += 1
        return {text for text, count in boundary.items() if len(page_items) > 1 and count >= 2}

    @staticmethod
    def _paragraph_element(text: str, order: int, page_number: int) -> ParsedElement:
        element_type = "paragraph"
        clause = None
        if text.startswith(("-", "*", "+")):
            element_type = "list_item"
        elif text[:1].isdigit() and any(sep in text for sep in (".", "、")):
            element_type = "numbered_clause"
            clause = text.split(maxsplit=1)[0]
        elif text.startswith("第") and "条" in text:
            element_type = "numbered_clause"
            clause = text.split(" ", 1)[0]
        return ParsedElement(
            element_type=element_type,
            text=text,
            order=order,
            page_number=page_number,
            clause_number=clause,
            source_ref=f"page:{page_number}",
        )

    @staticmethod
    def _emit_table(elements: list[ParsedElement], rows: list[list[str]], order: int, page_number: int) -> int:
        text = "\n".join(" | ".join(row) for row in rows)
        elements.append(
            ParsedElement(
                element_type="table",
                text=text,
                order=order,
                page_number=page_number,
                table_id=f"page:{page_number}:table:{order}",
                table_rows=rows,
                source_ref=f"page:{page_number}",
            )
        )
        return order + 1
