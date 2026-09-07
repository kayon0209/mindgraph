"""确定性引用保真检查（M0，ARCH-REVIEW 2026-08-28 推荐的唯一增强）。

不变量：答案正文中出现的 ``[citation-N]`` 标注必须全部命中本次回答实际
返回的引用集合（按 ``final_rank`` 匹配）。这是纯规则检查，零 LLM 成本、
零网络依赖，可在生成后立即执行。

策略（ADR-003）：M0 只“提示”（warning + 结果字段），不阻断生成；
若后续观察到系统性失真，再在 M2 升级为 fail-closed 硬拒答。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

CITATION_MARK_PATTERN = re.compile(r"\[citation-(\d+)\]")


@dataclass(frozen=True)
class FidelityReport:
    """一次引用保真检查的结果。

    - ``referenced``: 答案中出现的 [citation-N] 序号（按出现顺序去重）；
    - ``missing``: 引用了但不在实际引用集里的序号；
    - ``applicable``: 是否有可判定对象（有引用或至少有一个标注）；
    - ``ok``: missing 为空（含不可判定时视为 True）。
    """

    referenced: list[int] = field(default_factory=list)
    missing: list[int] = field(default_factory=list)
    applicable: bool = False
    ok: bool = True


def extract_citation_marks(answer: str) -> list[int]:
    """提取答案中的 [citation-N] 标注序号（保持出现顺序，含重复）。"""
    return [int(match.group(1)) for match in CITATION_MARK_PATTERN.finditer(answer or "")]


def check_citation_fidelity(answer: str, citation_ranks: Iterable[int]) -> FidelityReport:
    """检查答案标注是否全部命中实际引用序号集合。

    ``citation_ranks`` 为本次回答实际返回的引用 final_rank 列表（可为空）。
    判定语义：
    - 无引用且无标注 → 不可判定（applicable=False, ok=True）；
    - 有标注但引用了不存在的序号 → ok=False，missing 列出缺失序号。
    """
    referenced = extract_citation_marks(answer or "")
    available = set(int(rank) for rank in citation_ranks)
    missing = sorted({rank for rank in referenced if rank not in available})
    applicable = bool(available) or bool(referenced)
    return FidelityReport(
        referenced=sorted(set(referenced)),
        missing=missing,
        applicable=applicable,
        ok=not missing,
    )


def fidelity_warning(report: FidelityReport) -> str | None:
    """把检查结果格式化为 trace.warnings 条目；通过时返回 None。"""
    if report.ok:
        return None
    return f"citation_fidelity:missing_marks={','.join(str(item) for item in report.missing)}"
