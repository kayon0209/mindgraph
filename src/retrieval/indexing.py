from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from document_loader import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE, load_all_kb_chunks

from .dense import FAISSDenseRetriever
from .types import Chunk, EmbeddingProvider


def load_corpus(doc_dirs) -> list[Chunk]:
    chunks = load_all_kb_chunks(doc_dirs)
    return [
        Chunk(
            chunk_id=f"{item['metadata']['doc_name']}::{item['metadata']['chunk_index']}",
            text=item["text"],
            document_id=item["metadata"]["doc_name"],
            chunk_index=int(item["metadata"]["chunk_index"]),
            section_path=item["metadata"].get("section_path"),
            metadata=item["metadata"],
        )
        for item in chunks
    ]


def corpus_hash(chunks: list[Chunk]) -> str:
    payload = "\n".join(f"{chunk.chunk_id}\0{chunk.text}" for chunk in chunks)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_versioned_index(
    provider: EmbeddingProvider,
    chunks: list[Chunk],
    indexes_root: Path,
    version: str | None = None,
) -> tuple[FAISSDenseRetriever, Path]:
    version = version or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    index_dir = indexes_root / version
    if index_dir.exists():
        raise FileExistsError(f"Index version already exists: {index_dir}")
    retriever = FAISSDenseRetriever(provider, index_dir)
    metadata: dict[str, Any] = {
        "index_version": version,
        "index_created_at": datetime.now(timezone.utc).isoformat(),
        "chunk_size": DEFAULT_CHUNK_SIZE,
        "chunk_overlap": DEFAULT_CHUNK_OVERLAP,
        "corpus_sha256": corpus_hash(chunks),
    }
    retriever.build(chunks, metadata)
    (indexes_root / "CURRENT").write_text(version, encoding="utf-8")
    return retriever, index_dir


def load_current_index(provider: EmbeddingProvider, indexes_root: Path) -> FAISSDenseRetriever:
    version = (indexes_root / "CURRENT").read_text(encoding="utf-8").strip()
    retriever = FAISSDenseRetriever(provider, indexes_root / version)
    retriever.load()
    return retriever
