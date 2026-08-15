from __future__ import annotations

import hashlib
from collections import Counter
from io import BytesIO

from pypdf import PdfReader

from domain.models import ParsedDocument, ParsedElement


class PDFParser:
    name, version = "pypdf-text", "1.0.0"

    def supports(self, file_type: str) -> bool: return file_type.lower() == "pdf"

    def parse(self, data: bytes, document_name: str) -> ParsedDocument:
        try:
            reader = PdfReader(BytesIO(data))
        except Exception as exc:
            raise ValueError("Corrupted or unsupported PDF") from exc
        page_lines = []
        for page in reader.pages:
            page_lines.append([line.strip() for line in (page.extract_text() or "").splitlines() if line.strip()])
        boundary = Counter(lines[0] for lines in page_lines if lines) + Counter(lines[-1] for lines in page_lines if lines)
        repeated = {line for line, count in boundary.items() if len(page_lines) > 1 and count >= 2}
        elements, warnings, ocr_pages, order = [], [], [], 0
        for page_number, lines in enumerate(page_lines, 1):
            body = [line for line in lines if line not in repeated]
            if len("".join(body)) < 20:
                ocr_pages.append(page_number); warnings.append(f"page {page_number}: ocr_required")
            for line in body:
                elements.append(ParsedElement(element_type="paragraph", text=line, order=order, page_number=page_number, source_ref=f"page:{page_number}")); order += 1
            if page_number < len(page_lines):
                elements.append(ParsedElement(element_type="page_break", text="", order=order, page_number=page_number)); order += 1
        return ParsedDocument(document_id=hashlib.sha256(document_name.encode()).hexdigest()[:16], document_name=document_name,
            file_type="pdf", checksum=hashlib.sha256(data).hexdigest(), parser_name=self.name, parser_version=self.version,
            elements=elements, warnings=warnings, ocr_required_pages=ocr_pages, metadata={"page_count": len(reader.pages)})
