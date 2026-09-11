"""PR-09：检索命中 child 后按预算用 parent_text 补充上下文。

## 背景（任务书修正 2 实测）

``StructuredChunker`` 一直产出 parent lineage（parent_chunk_id / parent_text），
m4 索引路径把它们完整写进 chunk ``metadata``——但检索层**零消费**：
``grep parent_chunk_id src/retrieval/`` 命中数为 0。小块召回定位准，
但进入 LLM 上下文时只有半条条款，条件与结论分离。

## 设计约束

- **预算内替换，预算外不动**：parent 超预算时**不截断**——半条父块比完整
  child 更容易误导生成（宁缺毋滥），记 ``parent_exceeds_budget`` 留痕。
- **同 parent 去重**：同一父块的多个 child 命中时，parent 文本只进入上下文
  一次，防 prompt 重复膨胀。
- **无 lineage 原样通过**：mg-/m3- 路径的扁平块没有 parent，不记扩展
  （不是错误，是两种索引形态的既定差异）。
- **child 可追溯性保留**：扩展后 ``chunk_id`` 与 lineage 字段原样保留，
  citation 与审计仍指向命中子块。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from typing import Any

# reason codes（进 trace，供评测与人工复核统计）
PARENT_WITHIN_BUDGET = "parent_within_budget"
PARENT_EXCEEDS_BUDGET = "parent_exceeds_budget"
NO_PARENT_LINEAGE = "no_parent_lineage"

DEFAULT_MAX_CONTEXT_CHARS = 1200


@dataclass
class ExpansionReport:
    """一次扩展的完整决策账本，写入 RetrievalTrace 供评测分层。

    同时支持属性与 ``report["expanded"]`` 字典式访问——trace 序列化与
    评测侧聚合都以 dict 形态消费它。
    """

    expanded: int = 0
    duplicates_skipped: int = 0
    total_context_chars: int = 0
    reason_codes: list[str] = field(default_factory=list)
    detail: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "expanded": self.expanded,
            "duplicates_skipped": self.duplicates_skipped,
            "total_context_chars": self.total_context_chars,
            "reason_codes": self.reason_codes,
            "detail": self.detail,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


def _parent_text_of(candidate) -> tuple[str, str] | None:
    """取 (parent_chunk_id, parent_text)；缺任一即视为无 lineage。"""
    metadata = getattr(candidate.chunk, "metadata", None) or {}
    parent_id = metadata.get("parent_chunk_id")
    parent_text = metadata.get("parent_text")
    if not isinstance(parent_id, str) or not parent_id.strip():
        return None
    if not isinstance(parent_text, str) or not parent_text.strip():
        return None
    return parent_id, parent_text


def expand_to_parent(candidates: list, *, max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS):
    """把预算内可扩展的 child 候选替换为其完整父块文本。

    返回 ``(candidates, report)``：候选是浅拷贝（原 trace 里的 rerank/fusion
    阶段结果不被改动），替换只发生在进入 LLM 上下文的那一份上。
    """
    report = ExpansionReport()
    if not candidates:
        return candidates, report
    seen_parents: set[str] = set()
    expanded_list = []
    for candidate in candidates:
        lineage = _parent_text_of(candidate)
        if lineage is None:
            report.reason_codes.append(NO_PARENT_LINEAGE)
            expanded_list.append(candidate)
            continue
        parent_id, parent_text = lineage

        if parent_id in seen_parents:
            report.duplicates_skipped += 1
            report.reason_codes.append("duplicate_parent_kept_child")
            expanded_list.append(candidate)
            continue

        if len(parent_text) > max_context_chars:
            report.reason_codes.append(PARENT_EXCEEDS_BUDGET)
            report.detail.append({
                "parent_chunk_id": parent_id, "chars": len(parent_text),
                "budget": max_context_chars,
            })
            expanded_list.append(candidate)
            continue

        seen_parents.add(parent_id)
        report.total_context_chars += len(parent_text)
        old_metadata = dict(candidate.chunk.metadata)
        old_metadata["context_expanded_from_child"] = candidate.chunk.chunk_id
        # Chunk 是 frozen dataclass：用 replace 构造新实例，不改历史候选
        # （trace 里 fusion/rerank 阶段的结果必须保持原样）。
        new_chunk = replace(candidate.chunk, text=parent_text, metadata=old_metadata)
        new_candidate = copy.copy(candidate)
        new_candidate.chunk = new_chunk
        report.expanded += 1
        report.reason_codes.append(PARENT_WITHIN_BUDGET)
        report.detail.append({
            "parent_chunk_id": parent_id, "child_chunk_id": candidate.chunk.chunk_id,
            "chars": len(parent_text),
        })
        expanded_list.append(new_candidate)

    if report.total_context_chars:
        pass  # 统计在 report.total_context_chars；reason_codes 只放可聚合枚举
    report.reason_codes = list(dict.fromkeys(report.reason_codes))
    return expanded_list, report
