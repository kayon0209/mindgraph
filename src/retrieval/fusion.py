from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Sequence

from .types import RetrievalCandidate


class ReciprocalRankFusion:
    def __init__(self, constant: int = 60) -> None:
        if constant < 1:
            raise ValueError("RRF constant must be positive")
        self.constant = constant

    def fuse(self, rankings: Sequence[Sequence[RetrievalCandidate]], top_k: int) -> list[RetrievalCandidate]:
        scores: dict[str, float] = defaultdict(float)
        candidates: dict[str, RetrievalCandidate] = {}
        for ranking in rankings:
            for fallback_rank, item in enumerate(ranking, 1):
                rank = item.dense_rank or item.sparse_rank or fallback_rank
                scores[item.chunk.chunk_id] += 1.0 / (self.constant + rank)
                current = candidates.setdefault(item.chunk.chunk_id, deepcopy(item))
                if item.dense_rank is not None:
                    current.dense_rank, current.dense_score = item.dense_rank, item.dense_score
                if item.sparse_rank is not None:
                    current.sparse_rank, current.sparse_score = item.sparse_rank, item.sparse_score
        ordered = sorted(candidates.values(), key=lambda item: (-scores[item.chunk.chunk_id], item.chunk.chunk_id))[:top_k]
        for rank, item in enumerate(ordered, 1):
            item.rrf_score = scores[item.chunk.chunk_id]
            item.fused_rank = rank
        return ordered
