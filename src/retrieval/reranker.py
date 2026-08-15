from __future__ import annotations

import os
from copy import deepcopy
from typing import Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder

from .types import RetrievalCandidate


def _lazy_import_ce():
    """Lazy import to avoid blocking at module load time."""
    from sentence_transformers import CrossEncoder as CE
    return CE


DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-base"


class CrossEncoderReranker:
    def __init__(self, model_name: str | None = None, local_files_only: bool | None = None) -> None:
        self._model_name = model_name or os.getenv("RERANKER_MODEL_NAME", DEFAULT_RERANKER_MODEL)
        self._local_files_only = local_files_only if local_files_only is not None else os.getenv("RERANKER_LOCAL_FILES_ONLY", "true").lower() == "true"
        self._model = None

    @property
    def model_name(self) -> str:
        return self._model_name

    def _load(self):
        if self._model is None:
            CE = _lazy_import_ce()
            self._model = CE(self._model_name, local_files_only=self._local_files_only)
        return self._model

    def rerank(self, query: str, candidates: Sequence[RetrievalCandidate], top_k: int) -> list[RetrievalCandidate]:
        if not candidates or top_k <= 0:
            return []
        scores = self._load().predict([(query, candidate.chunk.text) for candidate in candidates])
        ranked = sorted(
            ((float(score), deepcopy(candidate)) for score, candidate in zip(scores, candidates)),
            key=lambda item: (-item[0], item[1].chunk.chunk_id),
        )[:top_k]
        results = []
        for rank, (score, candidate) in enumerate(ranked, 1):
            candidate.reranker_score, candidate.final_rank = score, rank
            results.append(candidate)
        return results
