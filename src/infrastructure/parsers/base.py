from __future__ import annotations

from typing import Protocol

from domain.models import ParsedDocument


class DocumentParser(Protocol):
    name: str
    version: str

    def supports(self, file_type: str) -> bool: ...
    def parse(self, data: bytes, document_name: str) -> ParsedDocument: ...
