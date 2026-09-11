from __future__ import annotations

import hashlib

from application.chunking_policy import ChunkingPolicy
from domain.models import ParsedDocument, ParsedElement, StructuredChunk


class StructuredChunker:
    """上传文档的结构化 parent-child 切分。

    切分参数自 PR-03 起经 ``ChunkingPolicy`` 单一来源提供：默认 legacy_v1
    （500/1200/50，历史行为的精确快照）；显式传参仍受同样校验约束。
    """

    def __init__(
        self,
        child_size: int | None = None,
        parent_size: int | None = None,
        overlap: int | None = None,
        policy: ChunkingPolicy | None = None,
    ) -> None:
        base = policy or ChunkingPolicy.from_settings()
        self.child_size = base.child_size if child_size is None else child_size
        self.parent_size = base.parent_size if parent_size is None else parent_size
        self.overlap = base.overlap if overlap is None else overlap
        self.policy = base
        ChunkingPolicy(
            name=base.name, version=base.version,
            child_size=self.child_size, parent_size=self.parent_size, overlap=self.overlap,
        )  # 复用同一套校验：显式覆盖值也必须合法（overlap < child_size 等）

    def chunk(self, document: ParsedDocument) -> list[StructuredChunk]:
        groups: list[list[ParsedElement]] = []
        current: list[ParsedElement] = []
        current_key = None
        for element in document.elements:
            key = tuple(element.heading_path) or (f"page:{element.page_number}" if element.page_number else "root",)
            if current and (key != current_key or sum(len(item.text) for item in current) + len(element.text) > self.parent_size):
                groups.append(current); current = []
            current_key = key; current.append(element)
        if current: groups.append(current)
        chunks = []
        for parent_index, elements in enumerate(groups):
            parent_text = "\n".join(element.text for element in elements if element.text)
            parent_id = hashlib.sha256(f"{document.checksum}:parent:{parent_index}:{parent_text}".encode()).hexdigest()[:24]
            start, child_index = 0, 0
            while start < len(parent_text):
                end = min(start + self.child_size, len(parent_text)); text = parent_text[start:end]
                child_id = hashlib.sha256(f"{parent_id}:child:{child_index}:{text}".encode()).hexdigest()[:24]
                pages = [item.page_number for item in elements if item.page_number]
                chunks.append(StructuredChunk(child_chunk_id=child_id, parent_chunk_id=parent_id, document_id=document.document_id,
                    text=text, parent_text=parent_text, heading_path=elements[0].heading_path if elements else [],
                    page_start=min(pages) if pages else None, page_end=max(pages) if pages else None,
                    clause_numbers=[item.clause_number for item in elements if item.clause_number], table_ids=[item.table_id for item in elements if item.table_id],
                    checksum=hashlib.sha256(text.encode()).hexdigest()))
                if end == len(parent_text): break
                start, child_index = end - self.overlap, child_index + 1
        return chunks
