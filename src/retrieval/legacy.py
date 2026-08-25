from __future__ import annotations

import time

from config import ZHIPU_API_KEY
from rag_engine import retrieve

from .types import Chunk, RetrievalCandidate


class LegacySQLiteRetriever:
    """Adapter for the frozen SQLite + NumPy baseline during comparisons."""

    def __init__(self) -> None:
        self._chunks: list[Chunk] = []

    @property
    def chunks(self) -> list[Chunk]:
        return list(self._chunks)

    def search(
        self,
        query: str,
        top_k: int,
        allowed_chunk_ids: set[str] | None = None,
    ) -> tuple[list[RetrievalCandidate], dict[str, float]]:
        start = time.perf_counter()
        sources = retrieve(ZHIPU_API_KEY, query, k=top_k)
        elapsed = (time.perf_counter() - start) * 1000
        candidates = []
        chunks = []
        for source in sources:
            chunk = Chunk(
                chunk_id=f"{source.source}::{source.chunk_index}",
                text=source.text,
                document_id=source.source,
                chunk_index=source.chunk_index,
                section_path=source.section_path,
            )
            chunks.append(chunk)
            if allowed_chunk_ids is not None and chunk.chunk_id not in allowed_chunk_ids:
                continue
            score = 1.0 - source.distance if source.distance is not None else None
            candidates.append(
                RetrievalCandidate(
                    chunk=chunk,
                    dense_score=score,
                    dense_rank=len(candidates) + 1,
                )
            )
        self._chunks = chunks
        return candidates, {"legacy_retrieval_ms": round(elapsed, 3)}
