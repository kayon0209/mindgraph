from __future__ import annotations

import hashlib

from application.chunking_policy import ChunkingPolicy
from application.cross_page_join import decide_join
from domain.models import ParsedDocument, ParsedElement, StructuredChunk


class StructuredChunker:
    """上传文档的结构化 parent-child 切分。

    切分参数自 PR-03 起经 ``ChunkingPolicy`` 单一来源提供：默认 legacy_v1
    （500/1200/50，历史行为的精确快照）；显式传参仍受同样校验约束。

    PR-08：``cross_page_join`` 打开时，无标题内容在换页处不再必然断组——
    由 :mod:`application.cross_page_join` 的确定性信号（同条款号/续表表头/
    页尾未终结句/续接词）决定是否并入上一父块。默认关闭：旧行为逐字节不变。
    """

    def __init__(
        self,
        child_size: int | None = None,
        parent_size: int | None = None,
        overlap: int | None = None,
        policy: ChunkingPolicy | None = None,
        *,
        cross_page_join: bool = False,
        cross_page_join_min_confidence: float = 0.5,
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
        self.cross_page_join_enabled = cross_page_join
        self.cross_page_join_min_confidence = cross_page_join_min_confidence

    def chunk(self, document: ParsedDocument) -> list[StructuredChunk]:
        groups: list[list[ParsedElement]] = []
        current: list[ParsedElement] = []
        current_key = None
        for element, previous in zip(document.elements, [None, *document.elements[:-1]]):
            key = tuple(element.heading_path) or (f"page:{element.page_number}" if element.page_number else "root",)
            # PR-08：无标题内容在换页处必然断组（key 变成 page:<n+1>）。
            # 跨页续接时沿用上一个键，让同一条款留在同一个父块里。
            # 只在开关打开时生效；同页分组逻辑完全不变。
            if (
                self.cross_page_join_enabled
                and previous is not None
                and current
                and decide_join(previous, element, min_confidence=self.cross_page_join_min_confidence).should_join
            ):
                key = current_key
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
                    # 去重但**保序**：条款号是 "第十二条" / "1.10." 这类字符串，
                    # 字典序不等于文档序（"第十一条" 会排到 "第二条" 前、"1.10." 会排到
                    # "1.2." 前）——下游按这个列表读"这段落覆盖了哪几条"就会读错。
                    clause_numbers=list(dict.fromkeys(item.clause_number for item in elements if item.clause_number)),
                    table_ids=sorted({item.table_id for item in elements if item.table_id}),
                    checksum=hashlib.sha256(text.encode()).hexdigest()))
                if end == len(parent_text): break
                start, child_index = end - self.overlap, child_index + 1
        return chunks
