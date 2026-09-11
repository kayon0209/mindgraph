"""
PRD v1 §9：优先按 Markdown 标题（## / ###）分块，块内按约 500 字、overlap 50 二次切分；
metadata：doc_name、section_path、chunk_index。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from config import ROOT

logger = logging.getLogger("mindgraph.document_loader")

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


def _warn_about_unscanned_subtrees(
    docs_dir: Path,
    glob_pattern: str,
    scanned: int,
    included_subtrees: Optional[Sequence[str]] = None,
) -> None:
    """glob 是**非递归**的：子目录里的 Markdown 会被静默跳过。

    本机真实后果——`knowledge/` 下只有 4 个顶层 .md 进了检索索引（69 chunks），
    而 `policies/`、`workflows/`、`cases/`、`external/public/`（11 份公开手册）
    全部没进，且此前没有任何提示：看知识库规模时很容易误以为"就这些"，
    评测/问答的召回也会被这一层缺口悄悄影响。

    这里只补**可见性**——不扩大摄取范围（扩不扩是产品决策，会直接改变
    索引内容与召回数字），只把"有内容但没被扫到"的子树报出来。

    ``included_subtrees`` 是**已声明的语料范围**（``settings.INDEX_INCLUDED_SUBTREES``）：

    - 传 ``None``（默认）= 调用方没声明范围 → 保持原行为，一律 WARNING；
    - 传序列（``()`` 表示"仅根目录"）= 只有落在声明范围内的子树才 WARNING，
      范围外的一律 INFO ``markdown_subtrees_out_of_declared_scope``。
      否则一个已拍板的缺口会在每次重建时刷 WARNING，变成没人看的噪音。
    """
    suffix = glob_pattern.lstrip("*")
    if not suffix.startswith("."):
        return  # 形如 **/*.md 的自定义模式语义不定，不猜
    unscanned: Dict[str, int] = {}
    try:
        candidates = sorted(docs_dir.glob(f"**/*{suffix}"))
    except OSError:
        return
    for path in candidates:
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(docs_dir)
        except ValueError:
            continue
        if len(rel.parts) < 2:
            continue  # 顶层文件已被非递归 glob 覆盖
        key = str(rel.parent)
        unscanned[key] = unscanned.get(key, 0) + 1
    if not unscanned:
        return

    if included_subtrees is None:
        logger.warning(
            "markdown_subtrees_not_scanned",
            extra={
                "docs_dir": str(docs_dir),
                "pattern": glob_pattern,
                "scanned_top_level_files": scanned,
                "unscanned_subtrees": unscanned,
                "note": "non-recursive glob: subdirectory markdown is excluded from the index",
            },
        )
        return

    declared = {str(item) for item in included_subtrees}
    in_scope = {k: v for k, v in unscanned.items() if k.split("/", 1)[0] in declared}
    out_of_scope = {k: v for k, v in unscanned.items() if k.split("/", 1)[0] not in declared}
    if in_scope:
        logger.warning(
            "markdown_subtrees_not_scanned",
            extra={
                "docs_dir": str(docs_dir),
                "pattern": glob_pattern,
                "scanned_top_level_files": scanned,
                "unscanned_subtrees": in_scope,
                "declared_included_subtrees": sorted(declared),
                "note": "declared in scope but excluded by the non-recursive glob",
            },
        )
    if out_of_scope:
        logger.info(
            "markdown_subtrees_out_of_declared_scope",
            extra={
                "docs_dir": str(docs_dir),
                "pattern": glob_pattern,
                "scanned_top_level_files": scanned,
                "unscanned_subtrees": out_of_scope,
                "declared_included_subtrees": sorted(declared),
                "note": "subdirectory markdown outside the declared corpus scope; excluded by design",
            },
        )


def load_markdown_chunks(
    docs_dir: Path,
    *,
    origin: str = "official",
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    glob_pattern: str = "*.md",
    included_subtrees: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """
    扫描目录下 Markdown，按 PRD 规则切分。

    每条记录：
    - ``text``: chunk 正文
    - ``metadata``: doc_name, section_path, chunk_index, source（同 doc_name）, origin, relative_path

    ``included_subtrees`` 用于把"已声明范围外"的跳过降级为 INFO（见
    :func:`_warn_about_unscanned_subtrees`），不改变实际扫描范围。
    """
    if not docs_dir.is_dir():
        return []

    records: List[Dict[str, Any]] = []
    paths = sorted(docs_dir.glob(glob_pattern))
    _warn_about_unscanned_subtrees(docs_dir, glob_pattern, len(paths), included_subtrees)
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
    included_subtrees: Optional[Sequence[str]] = None,
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
                included_subtrees=included_subtrees,
            )
        )
    return merged
