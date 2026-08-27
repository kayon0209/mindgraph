from __future__ import annotations

import hashlib
from html.parser import HTMLParser
from pathlib import Path
import re

from domain.models import ParsedDocument, ParsedElement


CLAUSE = re.compile(r"^\s*((?:第[一二三四五六七八九十百]+条)|(?:\d+(?:\.\d+)*[、.]))\s*(.*)$")


class _HTMLTextExtractor(HTMLParser):
    _IGNORED_TAGS = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self._ignored = 0
        self._heading_level: int | None = None
        self._buffer: list[str] = []
        self.lines: list[tuple[str, int | None]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in self._IGNORED_TAGS:
            self._ignored += 1
        elif self._ignored == 0 and len(tag) == 2 and tag[0] == "h" and tag[1].isdigit():
            self._heading_level = int(tag[1])

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._IGNORED_TAGS:
            self._ignored = max(0, self._ignored - 1)
        elif self._ignored == 0 and (tag in {"p", "li", "div", "br", "tr"} or tag.startswith("h")):
            text = " ".join("".join(self._buffer).split())
            if text:
                self.lines.append((text, self._heading_level))
            self._buffer.clear()
            if tag.startswith("h"):
                self._heading_level = None

    def handle_data(self, data: str) -> None:
        if self._ignored == 0:
            self._buffer.append(data)


class TextParser:
    name, version = "text-structured", "1.0.0"

    def __init__(self, markdown: bool) -> None:
        self.markdown = markdown

    def supports(self, file_type: str) -> bool:
        return file_type.lower() in ({"md", "markdown", "html", "htm"} if self.markdown else {"txt"})

    def parse(self, data: bytes, document_name: str) -> ParsedDocument:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Text document must use UTF-8 encoding") from exc
        if Path(document_name).suffix.lower() in {".html", ".htm"}:
            extractor = _HTMLTextExtractor()
            extractor.feed(text)
            lines = extractor.lines
        else:
            lines = [(line, None) for line in text.splitlines()]
        elements, heading_path, order = [], [], 0
        for raw, html_heading_level in lines:
            line = raw.strip()
            if not line:
                continue
            if html_heading_level or (self.markdown and (match := re.match(r"^(#{1,6})\s+(.+)$", line))):
                if html_heading_level:
                    level, title = html_heading_level, line
                else:
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
