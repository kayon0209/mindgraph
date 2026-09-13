"""PR-04 共享 fixture：造可跨 builder 比较的索引版本目录。

为什么需要它
------------
仓库里的索引 manifest 有**三种互不相识的 schema**（2026-09-11 实测）：

============  ========================================  ==============================
前缀          切分字段                                    产出
============  ========================================  ==============================
``m2-``/``m3-``  ``chunk_size`` + ``chunk_overlap``       扁平切分（``document_loader``）
``m4-``         ``chunker{child_size,parent_size,overlap}``  ``StructuredChunker``
``mg-``         **无**（PR-03 起新构建才写 ``chunking_policy``）  线上索引
============  ========================================  ==============================

加上「4 个版本目录根本没有 metadata.json」这个事实，任何比较器都必须接受：
**字段缺失是常态，不是异常**。本 fixture 让每种形态都能被一行造出来，
PR-04（一致性门禁）、PR-06（检查点）、PR-09（双跑）共用同一套造法，
避免每个 PR 各写一套、各自假设一种 schema。

用法见 ``build_snapshot_dir``。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

# 切分口径的四种 manifest 形态（与 src/application/index_snapshot.py 的 schema 名一致）
STYLE_FLAT = "flat"        # m2/m3：chunk_size + chunk_overlap
STYLE_CHUNKER = "chunker"  # m4：chunker{child_size,parent_size,overlap}
STYLE_POLICY = "policy"    # PR-03 起：chunking_policy{name,version,child_size,...}
STYLE_ABSENT = "absent"    # 老 mg：完全没有切分字段


def _chunk_id(doc_key: str, index: int, id_style: str) -> str:
    """两种真实并存的 chunk 命名：m3 的「文档::序号」与 m4/mg 的 32 位 hex。"""
    if id_style == "document":  # 历史 m3：差旅费报销管理办法.md::6
        return f"{doc_key}::{index}"
    raw = f"{doc_key}:{index}"  # m4/mg：与内容绑定的 hex
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def build_snapshot_dir(
    root: Path,
    version: str,
    *,
    documents: dict[str, int],
    style: str = STYLE_FLAT,
    child_size: int = 500,
    overlap: int = 50,
    parent_size: int | None = None,
    id_style: str = "document",
    write_metadata: bool = True,
    extra_manifest: dict | None = None,
) -> Path:
    """在 ``root/version`` 造一个索引版本目录。

    ``documents`` 是 ``{文档键: 该文档切出的 chunk 数}``——**同一批文档切出不同
    chunk 数**正是 69 vs 98 的形态，用它能直接复现"未经认可的口径切换"。

    ``style`` 决定 manifest 里写哪种切分字段；``write_metadata=False`` 复现
    「版本目录存在但没有 metadata.json」的真实情况（全仓实测 4 个）。
    """
    directory = Path(root) / version
    directory.mkdir(parents=True, exist_ok=True)

    chunks = []
    for doc_key, count in documents.items():
        for index in range(count):
            chunk_id = _chunk_id(doc_key, index, id_style)
            chunks.append({
                "chunk_id": chunk_id,
                "text": f"{doc_key} 的第 {index} 段正文内容。",
                "document_id": doc_key,
                "chunk_index": index,
                "section_path": f"第{index}节",
                "metadata": {"doc_name": doc_key, "vault_path": doc_key},
            })
    (directory / "chunks.json").write_text(
        json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if write_metadata:
        manifest: dict = {
            "index_version": version,
            "chunk_count": len(chunks),
            "corpus_sha256": hashlib.sha256(
                "".join(c["chunk_id"] for c in chunks).encode()
            ).hexdigest(),
        }
        if style == STYLE_FLAT:
            manifest["chunk_size"] = child_size
            manifest["chunk_overlap"] = overlap
        elif style == STYLE_CHUNKER:
            manifest["chunker"] = {
                "child_size": child_size,
                "parent_size": parent_size if parent_size is not None else 1200,
                "overlap": overlap,
            }
        elif style == STYLE_POLICY:
            manifest["chunk_size"] = child_size
            manifest["chunk_overlap"] = overlap
            manifest["chunking_policy"] = {
                "name": "legacy_v1", "version": "1",
                "child_size": child_size,
                "parent_size": parent_size if parent_size is not None else 1200,
                "overlap": overlap,
            }
        elif style == STYLE_ABSENT:
            pass  # 老 mg 索引：没有任何切分字段
        else:
            raise ValueError(f"unknown manifest style {style!r}")
        manifest.update(extra_manifest or {})
        (directory / "metadata.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return directory


def activate(root: Path, version: str) -> None:
    """把 ``version`` 设为 ``CURRENT``（模拟索引激活）。"""
    (Path(root) / "CURRENT").write_text(version, encoding="utf-8")
