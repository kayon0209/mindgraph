from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from pathlib import Path
from typing import Any

from application.chunking_policy import ChunkingPolicy  # noqa: E402  # PR-03：单一来源
from document_loader import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE, load_all_kb_chunks

from .dense import FAISSDenseRetriever
from .types import Chunk, EmbeddingProvider


def index_metadata(chunks: list[Chunk]) -> dict[str, Any]:
    """m3 索引的 manifest 数据（含切分策略投影），供 build 与测试共用。"""
    return {
        "chunk_size": DEFAULT_CHUNK_SIZE,
        "chunk_overlap": DEFAULT_CHUNK_OVERLAP,
        "chunking_policy": ChunkingPolicy.from_settings().manifest_payload(),
        "corpus_sha256": corpus_hash(chunks),
    }


def load_corpus(doc_dirs, *, included_subtrees=None) -> list[Chunk]:
    """加载语料。``included_subtrees`` 只影响"范围外跳过"的日志等级，不改变扫描范围。"""
    chunks = load_all_kb_chunks(doc_dirs, included_subtrees=included_subtrees)
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
    version = version or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    index_dir = indexes_root / version
    if index_dir.exists():
        raise FileExistsError(f"Index version already exists: {index_dir}")
    retriever = FAISSDenseRetriever(provider, index_dir)
    metadata: dict[str, Any] = {
        "index_version": version,
        "index_created_at": datetime.now(UTC).isoformat(),
        **index_metadata(chunks),
    }
    retriever.build(chunks, metadata)
    (indexes_root / "CURRENT").write_text(version, encoding="utf-8")
    return retriever, index_dir


def load_current_index(provider: EmbeddingProvider, indexes_root: Path) -> FAISSDenseRetriever:
    version = (indexes_root / "CURRENT").read_text(encoding="utf-8").strip()
    retriever = FAISSDenseRetriever(provider, indexes_root / version)
    retriever.load()
    return retriever
