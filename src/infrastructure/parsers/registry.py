from __future__ import annotations

from pathlib import Path

from .docx import DOCXParser
from .pdf import PDFParser
from .text import TextParser
from .xlsx import XLSXParser


class ParserRegistry:
    def __init__(self, parsers) -> None: self.parsers = list(parsers)

    def get(self, filename: str):
        file_type = Path(filename).suffix.lower().lstrip(".")
        for parser in self.parsers:
            if parser.supports(file_type): return parser
        raise ValueError(f"Unsupported document type: {file_type or 'none'}")

    def parse(self, data: bytes, filename: str): return self.get(filename).parse(data, filename)
    def supported_extensions(self): return sorted({ext for parser in self.parsers for ext in ("md", "txt", "pdf", "docx", "xlsx") if parser.supports(ext)})


default_parser_registry = ParserRegistry([TextParser(True), TextParser(False), PDFParser(), DOCXParser(), XLSXParser()])
