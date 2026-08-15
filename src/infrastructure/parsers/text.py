from __future__ import annotations

import hashlib
import re

from domain.models import ParsedDocument, ParsedElement


CLAUSE = re.compile(r"^\s*((?:第[一二三四五六七八九十百]+条)|(?:\d+(?:\.\d+)*[、.]))\s*(.*)$")


class TextParser:
    name, version = "text-structured", "1.0.0"

    def __init__(self, markdown: bool) -> None:
        self.markdown = markdown

    def supports(self, file_type: str) -> bool:
        return file_type.lower() in ({"md", "markdown"} if self.markdown else {"txt"})

    def parse(self, data: bytes, document_name: str) -> ParsedDocument:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Text document must use UTF-8 encoding") from exc
        elements, heading_path, order = [], [], 0
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if self.markdown and (match := re.match(r"^(#{1,6})\s+(.+)$", line)):
                level, title = len(match.group(1)), match.group(2).strip()
                heading_path = heading_path[:level - 1] + [title]
                element_type, clause = "heading", None
            elif match := CLAUSE.match(line):
                element_type, clause = "numbered_clause", match.group(1)
            elif re.match(r"^[-*+]\s+", line):
                element_type, clause = "list_item", None
            else:
                element_type, clause = "paragraph", None
            elements.append(ParsedElement(element_type=element_type, text=line, order=order, heading_path=list(heading_path), clause_number=clause))
            order += 1
        return ParsedDocument(document_id=hashlib.sha256(document_name.encode()).hexdigest()[:16], document_name=document_name,
            file_type="md" if self.markdown else "txt", checksum=hashlib.sha256(data).hexdigest(), parser_name=self.name,
            parser_version=self.version, elements=elements)
