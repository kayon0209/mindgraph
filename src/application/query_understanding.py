from __future__ import annotations

import re
from dataclasses import dataclass, field

from application.adaptive_retrieval_router import RetrievalRouteDecision

# 关键概念词：用于 HyDE 提示与检索增强，避免问句被改写掉实体
CONCEPT_TERMS = (
    "报销",
    "差旅",
    "住宿",
    "餐补",
    "发票",
    "审批",
    "例外",
    "冲突",
    "版本",
    "工资",
    "加班",
    "出差",
    "标准",
    "额度",
    "流程",
    "时间",
    "材料",
    "退款",
    "预支",
)
LONG_QUERY_THRESHOLD = 80
MAX_DECOMPOSE_PARTS = 4
MAX_VARIANTS = 3

_QUESTION_SPLIT_PATTERN = re.compile(r"[？?]+")
_NON_KEYWORD_PATTERN = re.compile(r"[，。！？；、,!?;:：\t\r\n（）()【】\[\]{}<>《》\-—~～·/\\|]+")
_WHITESPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True)
class QueryPlan:
    mode: str
    variants: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "variants": list(self.variants),
            "reasons": list(self.reasons),
        }


class QueryUnderstandingService:
    """在 AdaptiveRetrievalRouter 之后，按路由结果决定是否生成检索用变体。

    - none: 直接使用路由给出的 search_query（保持短问零额外成本）；
    - rewrite: 长问生成关键词化变体，降低长问的检索噪音；
    - decompose: 复合问题拆成子问题，逐个检索后合并。
    所有变体均为确定性规则，不引入额外模型调用。
    """

    def plan(self, question: str, decision: RetrievalRouteDecision) -> QueryPlan:
        route = decision.route
        if route == "clarification_required":
            parts = self._decompose(question)
            variants = tuple(dict.fromkeys(parts[:MAX_DECOMPOSE_PARTS]))
            if variants:
                return QueryPlan(mode="decompose", variants=variants, reasons=("compound_question_decomposed",))
            return QueryPlan(mode="none", reasons=("no_decomposable_subquestions",))
        if route == "factual" and len(question) >= LONG_QUERY_THRESHOLD:
            rewrite = self._rewrite(question)
            variants = tuple(dict.fromkeys([item for item in (rewrite, question) if item and item != decision.search_query]))
            if variants:
                return QueryPlan(mode="rewrite", variants=variants[:MAX_VARIANTS], reasons=("long_query_rewritten",))
            return QueryPlan(mode="none", reasons=("rewrite_no_change",))
        return QueryPlan(mode="none", reasons=("no_query_understanding_required",))

    def _decompose(self, question: str) -> list[str]:
        parts = [part.strip() for part in _QUESTION_SPLIT_PATTERN.split(question) if part.strip()]
        if len(parts) < 2:
            return [question.strip()]
        titles = re.findall(r"《([^》]{2,})》", question)
        merged: list[str] = []
        for part in parts:
            text = _NON_KEYWORD_PATTERN.sub(" ", part)
            text = _WHITESPACE_PATTERN.sub(" ", text).strip()
            if not text:
                continue
            if any(title in part for title in titles):
                text = f"{titles[0]} {text}".strip()
            merged.append(text)
        return merged

    @staticmethod
    def _rewrite(question: str) -> str:
        text = _NON_KEYWORD_PATTERN.sub(" ", question)
        text = _WHITESPACE_PATTERN.sub(" ", text).strip()
        terms = [item for item in text.split(" ") if item in CONCEPT_TERMS or len(item) > 1]
        keywords = [item for item in terms if item in CONCEPT_TERMS]
        if len(keywords) < 2:
            return text
        return " ".join(keywords[:6])
