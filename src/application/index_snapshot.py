"""PR-04｜索引快照与切分一致性门禁。

## 为什么需要它

``data/retrieval_indexes/`` 里同时躺着 **69 chunks 与 98 chunks** 两个版本，
``CURRENT`` 在它们之间被切换过而**无人阻止**（2026-09-11 实测）：

===========================  =======  ==========================================
版本                          chunk    构建入口
===========================  =======  ==========================================
``m3-20260910T073532Z-…``        69   ``document_loader`` 扁平切分
``m4-20260909T123040Z-…``        98   ``StructuredChunker`` parent-child
===========================  =======  ==========================================

两者的 ``chunk_size / overlap`` **数值相同**（500/50），所以"参数漂移"这个说法
是错的——真正的差异是**构建入口（schema）不同**，后果是 chunk_id 命名空间整体
换掉（``差旅费报销管理办法.md::6`` ↔ 32 位 hex），已公布的检索指标只对应其中一套。

## 口径指纹为什么带 schema

只用 ``(child_size, overlap)`` 比较，69↔98 这种切换**抓不到**（数值一样）。
带上 schema 后：

- ``("chunk_size_overlap", 500, None, 50)``  ← m3
- ``("chunker", 500, 1200, 50)``             ← m4

两者不同 → 阻断。而**同一入口内的正常重建**（文档换版本、重新嵌入）schema 不变，
指纹相同 → 放行，不会误伤。

## 刻意不做的事

- **不跨根比较**：``mindgraph_indexes`` 与 ``retrieval_indexes`` 是两个命名空间，
  各服务不同评测栈，混比只会产生噪声（见 ``index_metadata.INDEX_ROOT_REGISTRY``）。
- **不因 chunk 集不相交就阻断**：文档换版本会让 chunk_id 全变，那是正常的。
  不相交只作为**诊断信息**报出，是否切换由人判断。
- **缺 manifest 不阻断**：全仓有 4 个版本目录没有 ``metadata.json``，
  这是历史事实；把它当成失败会让索引永远无法激活。降级为"不可比"。
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from application.index_metadata import document_key

logger = logging.getLogger("mindgraph.index_snapshot")

# manifest 里切分口径的四种形态（按优先级从新到旧）
SCHEMA_POLICY = "chunking_policy"          # PR-03 起的新构建
SCHEMA_CHUNKER = "chunker"                 # m4：StructuredChunker
SCHEMA_FLAT = "chunk_size_overlap"         # m2/m3：document_loader
SCHEMA_ABSENT = "absent"                   # 老 mg 索引：无切分字段


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def chunking_fingerprint(manifest: dict[str, Any] | None) -> dict[str, Any]:
    """把任意 schema 的 manifest 归一化成可比的切分口径。

    ``comparable=False`` 表示**这份 manifest 没有声明切分口径**（老 mg 索引），
    调用方必须据此跳过切分比较——绝不能把 "没写" 当成 "500"。
    """
    absent = {
        "schema": SCHEMA_ABSENT, "child_size": None, "parent_size": None,
        "overlap": None, "comparable": False,
    }
    if not isinstance(manifest, dict):
        return absent

    policy = manifest.get("chunking_policy")
    if isinstance(policy, dict) and _as_int(policy.get("child_size")):
        return {
            "schema": SCHEMA_POLICY,
            "child_size": _as_int(policy.get("child_size")),
            "parent_size": _as_int(policy.get("parent_size")),
            "overlap": _as_int(policy.get("overlap")),
            "comparable": True,
        }

    chunker = manifest.get("chunker")
    if isinstance(chunker, dict) and _as_int(chunker.get("child_size")):
        return {
            "schema": SCHEMA_CHUNKER,
            "child_size": _as_int(chunker.get("child_size")),
            "parent_size": _as_int(chunker.get("parent_size")),
            "overlap": _as_int(chunker.get("overlap")),
            "comparable": True,
        }

    if _as_int(manifest.get("chunk_size")):
        return {
            "schema": SCHEMA_FLAT,
            "child_size": _as_int(manifest.get("chunk_size")),
            "parent_size": None,  # 扁平切分没有 parent 概念
            "overlap": _as_int(manifest.get("chunk_overlap")),
            "comparable": True,
        }
    return absent


def _corpus_digest(digests: dict[str, str]) -> str:
    """chunk 集的内容指纹：顺序无关。"""
    payload = "".join(f"{cid}:{dig}" for cid, dig in sorted(digests.items()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_snapshot(root: str | Path, version: str) -> dict[str, Any] | None:
    """读取 ``root/version`` 的快照；``chunks.json`` 不可读时返回 ``None``。

    ``metadata_missing=True`` 表示版本目录存在但没有可读的 manifest——
    这是**需要上报的历史事实**，不是错误。
    """
    directory = Path(root) / str(version)
    try:
        raw = json.loads((directory / "chunks.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("index_snapshot_chunks_unreadable", extra={"version": str(version), "error": str(exc)[:200]})
        return None
    if not isinstance(raw, list):
        return None

    chunk_ids: set[str] = set()
    document_keys: set[str] = set()
    digests: dict[str, str] = {}
    for chunk in raw:
        if not isinstance(chunk, dict):
            continue
        chunk_id = chunk.get("chunk_id")
        text = chunk.get("text") or ""
        if chunk_id:
            chunk_ids.add(str(chunk_id))
            digests[str(chunk_id)] = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        metadata = chunk.get("metadata")
        key = document_key(metadata if isinstance(metadata, dict) else None)
        if key:
            document_keys.add(key)

    manifest: dict[str, Any] | None = None
    metadata_missing = True
    metadata_file = directory / "metadata.json"
    if metadata_file.exists():
        try:
            loaded = json.loads(metadata_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest, metadata_missing = loaded, False
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("index_snapshot_manifest_unreadable", extra={"version": str(version), "error": str(exc)[:200]})

    return {
        "root": str(root),
        "version": str(version),
        "chunk_ids": frozenset(chunk_ids),
        "document_keys": frozenset(document_keys),
        "digests": digests,
        "corpus_digest": _corpus_digest(digests),
        "chunk_count": len(chunk_ids),
        "chunking": chunking_fingerprint(manifest),
        "metadata_missing": metadata_missing,
    }


def _fingerprint_tuple(fp: dict[str, Any]) -> tuple:
    return (fp["schema"], fp["child_size"], fp["parent_size"], fp["overlap"])


def compare_snapshots(
    previous: dict[str, Any] | None,
    candidate: dict[str, Any] | None,
) -> dict[str, Any]:
    """比较两个快照，产出**最小差异报告**。

    只报"变了什么、从几变到几"，不报总数——2026-09-09 事故的教训是：
    只说"25 篇变成 4 篇"看不出丢的是哪些，报出文件名才能立刻定位。
    """
    if previous is None or candidate is None:
        return {
            "comparable": False, "chunking_comparable": False, "chunking_changed": False,
            "chunking": {"previous": None, "candidate": None},
            "documents_removed": [], "documents_added": [],
            "chunk_ids_removed": 0, "chunk_ids_added": 0,
            "chunk_overlap_ratio": None, "chunk_namespace_disjoint": False,
            "corpus_digest_changed": None,
            "previous_metadata_missing": None, "candidate_metadata_missing": None,
        }

    prev_chunking = previous["chunking"]
    cand_chunking = candidate["chunking"]
    both_comparable = prev_chunking["comparable"] and cand_chunking["comparable"]
    chunking_changed = both_comparable and _fingerprint_tuple(prev_chunking) != _fingerprint_tuple(cand_chunking)

    prev_ids: frozenset[str] = previous["chunk_ids"]
    cand_ids: frozenset[str] = candidate["chunk_ids"]
    intersection = len(prev_ids & cand_ids)
    ratio = (intersection / len(prev_ids)) if prev_ids else None

    return {
        "comparable": True,
        "chunking_comparable": both_comparable,
        "chunking_changed": chunking_changed,
        "chunking": {"previous": prev_chunking, "candidate": cand_chunking},
        "documents_removed": sorted(previous["document_keys"] - candidate["document_keys"]),
        "documents_added": sorted(candidate["document_keys"] - previous["document_keys"]),
        "chunk_ids_removed": len(prev_ids - cand_ids),
        "chunk_ids_added": len(cand_ids - prev_ids),
        "chunk_overlap_ratio": ratio,
        # 交比为 0 说明换了一整套 chunk 命名——是诊断信号，本身不构成阻断
        "chunk_namespace_disjoint": bool(prev_ids) and intersection == 0,
        "corpus_digest_changed": previous["corpus_digest"] != candidate["corpus_digest"],
        "previous_metadata_missing": previous["metadata_missing"],
        "candidate_metadata_missing": candidate["metadata_missing"],
    }


def evaluate_activation_gate(
    previous: dict[str, Any] | None,
    candidate: dict[str, Any] | None,
    *,
    allow_chunking_change: bool = False,
    allow_document_removal: bool = False,
    excused_documents: list[str] | None = None,
) -> dict[str, Any]:
    """激活门禁结论：**在 CURRENT 被改写之前**调用。

    阻断性判断只有三条（都要求"有 previous 可比"）：
      1. 候选索引读不出来 → 不可能放行；
      2. 切分口径变了（含 schema 变化）→ 未经认可的口径切换；
      3. 文档丢失且无法解释 → 09-09 那类静默缩水。

    其余（chunk 集不相交、缺 manifest）都是**诊断信息**，进 ``report`` 不进 ``reasons``：
    文档换版本会让 chunk_id 全变，那是正常重建，不能一刀切拦。
    """
    report = compare_snapshots(previous, candidate)
    reasons: list[str] = []
    warnings: list[str] = []

    if candidate is None:
        return {"blocked": True, "reasons": ["candidate_unreadable"], "warnings": [], "report": report}

    if previous is None:
        return {"blocked": False, "reasons": ["no_previous"], "warnings": [], "report": report}

    if report["chunking_changed"] and not allow_chunking_change:
        reasons.append("chunking_changed")

    removed = report["documents_removed"]
    if removed and not allow_document_removal:
        excused = {(str(name).rsplit("/", 1)[-1]).lower() for name in (excused_documents or [])}
        unexplained = [key for key in removed if key.rsplit("/", 1)[-1].lower() not in excused]
        if unexplained:
            reasons.append("documents_removed")

    if report["chunk_namespace_disjoint"]:
        warnings.append("chunk_namespace_disjoint")
    if report["previous_metadata_missing"] or report["candidate_metadata_missing"]:
        warnings.append("metadata_missing_skipped")

    return {"blocked": bool(reasons), "reasons": reasons, "warnings": warnings, "report": report}
