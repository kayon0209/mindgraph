"""PR-08｜跨页条款与续表恢复。

## 问题（源码已定位）

``structured_chunker.py:18`` 的父块分组键是
``tuple(heading_path) or (f"page:{page_number}", ...) or ("root",)``。
没有标题的连续条款，分组键在换页处**必然变化** → 一句话被切成两段父块，
后半段脱离上下文，检索到它时看不到它属于哪一条。

## 修复方向

任务书说得对：修在**分组键**上，不是切完再用正则拼字符串。
本模块只回答一个问题：**这两个相邻元素该不该算同一个父块**。

## 只在跨页处判断

同页相邻元素的分组行为**完全不变**。跨页断裂才是缺陷，同页切分是既有设计；
把两者一起"优化"会让改动无法与历史指标对照。

## 不确定就不合并

每个判断都必须给出 reason code 与置信度，低于阈值一律不合并并留下 warning。
宁可漏合，不可错把两个条款粘成一条 —— 后者会让检索结果张冠李戴。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── 合并理由码 ────────────────────────────────────────────────────────────
SAME_CLAUSE = "same_clause"                  # 同一条款号续页
TABLE_HEADER_MATCH = "table_header_match"    # 续表：表头一致
UNTERMINATED_SENTENCE = "unterminated_sentence"  # 页尾没有终止标点
CONNECTIVE_START = "connective_start"        # 下页以续接词开头
NO_SIGNAL = "no_signal"                      # 无信号 → 不合并
NOT_CROSS_PAGE = "not_cross_page"            # 非跨页 → 不参与判断

REASON_LABELS = {
    SAME_CLAUSE: "前后属于同一条款号",
    TABLE_HEADER_MATCH: "续表：两页表头一致",
    UNTERMINATED_SENTENCE: "上一页句末没有终止标点，句子未完",
    CONNECTIVE_START: "下一页以续接词开头",
    NO_SIGNAL: "没有任何续接信号",
    NOT_CROSS_PAGE: "不是跨页相邻，不参与判断",
}

# 各信号的置信度。取所有命中信号里的**最高值**作为最终置信度，
# 并把全部命中信号记进 reasons，便于人工复核"为什么合并"。
_SIGNAL_CONFIDENCE = {
    SAME_CLAUSE: 0.95,
    TABLE_HEADER_MATCH: 0.9,
    UNTERMINATED_SENTENCE: 0.6,
    CONNECTIVE_START: 0.5,
}

_TERMINAL_PUNCTUATION = ("。", "；", "！", "？", "：", "”", "）", ")", ".", ";", "!", "?")
# 续接词：下页以这些开头，说明上一页的话没说完
_CONNECTIVES = ("其中", "但是", "并且", "以及", "其", "如", "若", "另", "同时", "此外",
                "上述", "本条", "前款", "该", "否则", "但", "并", "且", "以及其")


@dataclass(frozen=True)
class JoinDecision:
    should_join: bool
    reason: str
    reason_label: str
    confidence: float
    signals: tuple[str, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_join": self.should_join, "reason": self.reason,
            "reason_label": self.reason_label, "confidence": round(self.confidence, 4),
            "signals": list(self.signals), "detail": self.detail,
        }


def _terminal(text: str) -> bool:
    stripped = (text or "").rstrip()
    return bool(stripped) and stripped.endswith(_TERMINAL_PUNCTUATION)


def _starts_with_connective(text: str) -> bool:
    stripped = (text or "").lstrip()
    return any(stripped.startswith(word) for word in _CONNECTIVES)


def _table_header(element) -> tuple[str, ...] | None:
    rows = getattr(element, "table_rows", None)
    if not rows:
        return None
    first = rows[0] if isinstance(rows[0], list) else [rows[0]]
    return tuple(str(cell).strip() for cell in first if str(cell).strip())


def decide_join(previous, following, *, min_confidence: float = 0.5) -> JoinDecision:
    """判断相邻两元素是否应属同一父块。只在**跨页**时返回 should_join=True。"""
    prev_page = getattr(previous, "page_number", None)
    next_page = getattr(following, "page_number", None)
    if prev_page is None or next_page is None or next_page <= prev_page:
        return JoinDecision(False, NOT_CROSS_PAGE, REASON_LABELS[NOT_CROSS_PAGE], 0.0)

    signals: list[str] = []
    detail: dict[str, Any] = {"from_page": prev_page, "to_page": next_page}

    prev_clause = getattr(previous, "clause_number", None)
    next_clause = getattr(following, "clause_number", None)
    if prev_clause and next_clause and prev_clause == next_clause:
        signals.append(SAME_CLAUSE)
        detail["clause"] = prev_clause

    prev_header = _table_header(previous)
    next_header = _table_header(following)
    if prev_header and next_header and prev_header == next_header:
        signals.append(TABLE_HEADER_MATCH)
        detail["header"] = list(prev_header)

    # 表格边界规则：两侧都是表格但表头不一致 → 明确不合并。两个不同的表
    # 不会被「页尾没句号」这类弱信号粘住——错合的代价是检索张冠李戴。
    if prev_header and next_header and prev_header != next_header:
        return JoinDecision(False, NO_SIGNAL, REASON_LABELS[NO_SIGNAL], 0.0,
                            signals=(NO_SIGNAL,), detail={**detail, "header_mismatch": True})

    if not _terminal(getattr(previous, "text", "") or ""):
        signals.append(UNTERMINATED_SENTENCE)
    if _starts_with_connective(getattr(following, "text", "") or ""):
        signals.append(CONNECTIVE_START)

    if not signals:
        return JoinDecision(False, NO_SIGNAL, REASON_LABELS[NO_SIGNAL], 0.0,
                            signals=(NO_SIGNAL,), detail=detail)

    primary = max(signals, key=lambda name: _SIGNAL_CONFIDENCE[name])
    confidence = _SIGNAL_CONFIDENCE[primary]
    return JoinDecision(confidence >= min_confidence, primary, REASON_LABELS[primary],
                        confidence, signals=tuple(signals), detail=detail)


def joinable_pairs(elements: list, *, min_confidence: float = 0.5) -> list[JoinDecision]:
    """逐对给出判断，供人工抽样与"错误合并率"报告使用。"""
    return [
        decide_join(elements[index], elements[index + 1], min_confidence=min_confidence)
        for index in range(len(elements) - 1)
    ]


__all__ = [
    "JoinDecision", "decide_join", "joinable_pairs", "REASON_LABELS",
    "SAME_CLAUSE", "TABLE_HEADER_MATCH", "UNTERMINATED_SENTENCE", "CONNECTIVE_START",
    "NO_SIGNAL", "NOT_CROSS_PAGE",
]
