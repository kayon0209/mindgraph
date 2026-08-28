from __future__ import annotations

import hashlib
import os
from typing import Sequence, cast


def _lazy_import_st() -> type:
    """Lazy import to avoid blocking at module load time."""
    from sentence_transformers import SentenceTransformer as ST
    return cast(type, ST)


DEFAULT_BGE_MODEL = "BAAI/bge-small-zh-v1.5"


class BGEEmbeddingProvider:
    """Local BGE provider; formal evaluation never falls back to hash vectors."""

    def __init__(self, model_name: str | None = None, revision: str | None = None, local_files_only: bool | None = None, local_path: str | None = None) -> None:
        self._model_name = model_name or os.getenv("BGE_MODEL_NAME", DEFAULT_BGE_MODEL)
        self._requested_revision = revision or os.getenv("BGE_MODEL_REVISION") or None
        self._local_files_only = local_files_only if local_files_only is not None else os.getenv("BGE_LOCAL_FILES_ONLY", "true").lower() == "true"
        # Local model folder wins if present (bypasses HF hub cache corruption on Windows).
        # Resolution: explicit arg -> BGE_LOCAL_PATH env -> <project>/data/bge-small-zh-v1.5
        _default_local = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "data",
            "bge-small-zh-v1.5",
        )
        self._local_path = local_path or os.getenv("BGE_LOCAL_PATH") or _default_local
        self._model = None
        self._loaded_from_local = False

    def _load(self):
        if self._model is None:
            ST = _lazy_import_st()
            if self._local_path and os.path.isdir(self._local_path):
                self._model = ST(self._local_path)
                self._loaded_from_local = True
            else:
                self._model = ST(
                    self._model_name,
                    revision=self._requested_revision,
                    local_files_only=self._local_files_only,
                )
                self._loaded_from_local = False
        return self._model

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def model_revision(self) -> str | None:
        model = self._load()
        if self._loaded_from_local:
            return "local:" + hashlib.md5(self._local_path.encode("utf-8")).hexdigest()[:12]
        transformer = model[0]
        commit_hash = getattr(getattr(transformer, "auto_model", None).config, "_commit_hash", None)
        return self._requested_revision or commit_hash

    @property
    def dimension(self) -> int:
        model = self._load()
        get_dimension = getattr(model, "get_embedding_dimension", model.get_sentence_embedding_dimension)
        dimension = get_dimension()
        if dimension is None:
            raise RuntimeError(f"Embedding dimension unavailable for {self.model_name}")
        return int(dimension)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._load().encode(
            list(texts),
            batch_size=int(os.getenv("BGE_BATCH_SIZE", "32")),
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return cast(list[list[float]], vectors.tolist())

    def embed_query(self, text: str) -> list[float]:
        if not text.strip():
            return [0.0] * self.dimension
        instruction = "为这个句子生成表示以用于检索相关文章："
        vector = self._load().encode(
            [instruction + text],
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )[0]
        return cast(list[float], vector.tolist())
