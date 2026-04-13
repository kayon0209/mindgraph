"""
PRD v1 §9：优先按 Markdown 标题（## / ###）分块，块内按约 500 字、overlap 50 二次切分；
metadata：doc_name、section_path、chunk_index。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from config import ROOT

# PRD：chunk_size ≈ 500 中文字符，overlap = 50
DEFAULT_CHUNK_SIZE = 500
DEFAULT_CHUNK_OVERLAP = 50


def _chunk_text(text: str, chunk_size: int, overlap: int) -> List[str]:
    text = text.strip()
    if not text:
        return []
    chunks: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        chunks.append(text[start:end])
        if end >= n:
            break
        start = max(0, end - overlap)
    return chunks


def _split_by_markdown_headers(content: str) -> List[Tuple[str, str]]:
    """
    按行首 ## / ### 切分为多个区块，返回 (section_path, 区块正文)。
    文首无标题内容归入「(文首)」。
    """
    lines = content.splitlines(keepends=True)
    sections: List[Tuple[str, str]] = []
    current_title = "(文首)"
    current_buf: List[str] = []

    for line in lines:
        stripped = line.rstrip("\n\r")
        m = re.match(r"^(#{2,3})\s+(.+)$", stripped)
        if m:
            if current_buf:
                body = "".join(current_buf).strip()
                if body:
                    sections.append((current_title, body))
            current_title = m.group(2).strip()
            current_buf = []
        else:
            current_buf.append(line)

    tail = "".join(current_buf).strip()
    if tail or not sections:
        sections.append((current_title, tail))

    return [(t, b) for t, b in sections if b.strip()]


def load_markdown_chunks(
    docs_dir: Path,
    *,
    origin: str = "official",
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    glob_pattern: str = "*.md",
) -> List[Dict[str, Any]]:
    """
    扫描目录下 Markdown，按 PRD 规则切分。

    每条记录：
    - ``text``: chunk 正文
    - ``metadata``: doc_name, section_path, chunk_index, source（同 doc_name）, origin, relative_path
    """
    if not docs_dir.is_dir():
        return []

    records: List[Dict[str, Any]] = []
    paths = sorted(docs_dir.glob(glob_pattern))
    for path in paths:
        if not path.is_file():
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue

        doc_name = path.name
        try:
            rel = str(path.relative_to(ROOT))
        except ValueError:
            rel = doc_name

        section_blocks = _split_by_markdown_headers(raw)
        if not section_blocks:
            continue

        global_idx = 0
        for section_path, body in section_blocks:
            sub_chunks = _chunk_text(body, chunk_size, chunk_overlap)
            for _si, chunk in enumerate(sub_chunks):
                records.append(
                    {
                        "text": chunk,
                        "metadata": {
                            "doc_name": doc_name,
                            "section_path": section_path,
                            "chunk_index": global_idx,
                            "source": doc_name,
                            "origin": origin,
                            "relative_path": rel,
                        },
                    }
                )
                global_idx += 1

    return records


def load_all_kb_chunks(
    doc_dirs: List[Tuple[Path, str]],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> List[Dict[str, Any]]:
    """合并多个根目录（如内置 docs + 上传 uploads），各自带 origin。"""
    merged: List[Dict[str, Any]] = []
    for base, origin in doc_dirs:
        merged.extend(
            load_markdown_chunks(
                base,
                origin=origin,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
        )
    return merged
