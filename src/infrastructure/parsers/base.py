from __future__ import annotations

from typing import Protocol

from domain.models import ParsedDocument


class DocumentParser(Protocol):
    name: str
    version: str

    def supports(self, file_type: str) -> bool: ...
    def parse(self, data: bytes, document_name: str) -> ParsedDocument: ...


class PagedDocumentParser(Protocol):
    """可选能力：只解析指定页（PR-06「重试不重跑成功页」的前提）。

    它是**独立 Protocol 而不是加进 DocumentParser**：分页解析只有部分格式
    支持（PDF 有页，Markdown 没有），强加进主协议会让每个 parser 都必须实现
    一个自己根本不支持的方法。

    运行时用 ``supports_paged()`` 探测，不要求显式继承。
    """

    def parse_pages(self, data: bytes, document_name: str, page_numbers: list[int]) -> ParsedDocument: ...


def supports_paged(parser: object) -> bool:
    return callable(getattr(parser, "parse_pages", None))
