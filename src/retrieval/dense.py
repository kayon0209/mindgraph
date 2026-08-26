from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .types import Chunk, EmbeddingProvider, RetrievalCandidate


class IncompatibleIndexError(RuntimeError):
    pass


class FAISSDenseRetriever:
    def __init__(self, provider: EmbeddingProvider, index_dir: Path) -> None:
        self.provider = provider
        self.index_dir = Path(index_dir)
        self._index: Any = None
        self._chunks: list[Chunk] = []
        self._metadata: dict[str, Any] = {}

    @staticmethod
    def _faiss():
        try:
            import faiss
        except ImportError as exc:
            raise RuntimeError("faiss-cpu is required for dense retrieval") from exc
        return faiss

    @property
    def metadata(self) -> dict[str, Any]:
        return dict(self._metadata)

    @property
    def chunks(self) -> list[Chunk]:
        return list(self._chunks)

    def build(self, chunks: Sequence[Chunk], metadata: dict[str, Any]) -> None:
        if not chunks:
            raise ValueError("Cannot build a FAISS index from an empty corpus")
        faiss = self._faiss()
        vectors = np.asarray(self.provider.embed_documents([chunk.text for chunk in chunks]), dtype="float32")
        if vectors.ndim != 2 or vectors.shape[0] != len(chunks):
            raise ValueError("Embedding count does not match chunk count")
        faiss.normalize_L2(vectors)
        index = faiss.IndexFlatIP(vectors.shape[1])
        index.add(vectors)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        faiss.write_index(index, str(self.index_dir / "dense.faiss"))
        (self.index_dir / "chunks.json").write_text(
            json.dumps([chunk.__dict__ for chunk in chunks], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        full_metadata = {
            **metadata,
            "embedding_model_name": self.provider.model_name,
            "embedding_model_revision": self.provider.model_revision,
            "vector_dimension": int(vectors.shape[1]),
            "chunk_count": len(chunks),
        }
        (self.index_dir / "metadata.json").write_text(json.dumps(full_metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        self._index, self._chunks, self._metadata = index, list(chunks), full_metadata

    def load(self) -> None:
        faiss = self._faiss()
        metadata = json.loads((self.index_dir / "metadata.json").read_text(encoding="utf-8"))
        expected = (self.provider.model_name, self.provider.dimension)
        actual = (metadata.get("embedding_model_name"), metadata.get("vector_dimension"))
        if expected != actual:
            raise IncompatibleIndexError(f"Index embedding mismatch: expected {expected}, found {actual}; full rebuild required")
        raw_chunks = json.loads((self.index_dir / "chunks.json").read_text(encoding="utf-8"))
        self._chunks = [Chunk(**item) for item in raw_chunks]
        self._index = faiss.read_index(str(self.index_dir / "dense.faiss"))
        if self._index.ntotal != len(self._chunks):
            raise IncompatibleIndexError("FAISS row count does not match chunk metadata")
        self._metadata = metadata

    def search(self, query: str, top_k: int, access_scope: dict[str, Any] | None = None) -> tuple[list[RetrievalCandidate], dict[str, float]]:
        if top_k <= 0 or not query.strip():
            return [], {"query_embedding_ms": 0.0, "dense_retrieval_ms": 0.0}
        if self._index is None:
            self.load()
        start = time.perf_counter()
        vector = np.asarray([self.provider.embed_query(query)], dtype="float32")
        embedding_ms = (time.perf_counter() - start) * 1000
        self._faiss().normalize_L2(vector)
        start = time.perf_counter()
        # FAISS IndexFlatIP has no native metadata predicate. Over-fetch and
        # discard unauthorized rows before constructing RetrievalCandidate so
        # private chunks never enter the observable dense stage.
        search_k = len(self._chunks) if access_scope else min(top_k, len(self._chunks))
        scores, positions = self._index.search(vector, search_k)
        retrieval_ms = (time.perf_counter() - start) * 1000
        from application.access_control import chunk_acl_matches

        results = []
        for score, position in zip(scores[0], positions[0]):
            if position < 0:
                continue
            chunk = self._chunks[int(position)]
            if access_scope is not None and not chunk_acl_matches(chunk.metadata, access_scope):
                continue
            results.append(RetrievalCandidate(chunk=chunk, dense_score=float(score), dense_rank=len(results) + 1))
            if len(results) >= top_k:
                break
        return results, {"query_embedding_ms": round(embedding_ms, 3), "dense_retrieval_ms": round(retrieval_ms, 3)}
