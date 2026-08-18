from __future__ import annotations

import re
from dataclasses import dataclass


GRAPH_STRATEGIES = {"hybrid", "hybrid_rerank"}
EXCEPTION_OR_CONFLICT_TERMS = (
    "例外",
    "冲突",
    "矛盾",
    "新旧",
    "替代",
    "取代",
    "适用哪个",
    "无发票",
    "无法取得发票",
)
CROSS_POLICY_TERMS = ("同时", "分别", "对比", "一起", "重复报销", "跨制度", "能否同时")
EXPLICIT_TITLE_PATTERN = re.compile(r"《([^》]{2,})》")
STRUCTURED_TERM_PATTERN = re.compile(r"(?:\b[vV]\s*\d+(?:\.\d+)*\b|\d+(?:\.\d+)?\s*(?:元|万元|%|天|小时))")
QUESTION_FILLER_PATTERN = re.compile(r"(?:请问|请|如何|怎么|怎样|是否|能否|可以|可否|请帮我|帮我|麻烦|想问|告诉我|一下|嘛|呢|呀)")
PUNCTUATION_PATTERN = re.compile(r"[，。！？；、,!?;:\t\r\n（）()【】\[\]{}<>《》\-—~～·/\\|]+")
WHITESPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True)
class RetrievalRouteDecision:
    mode: str
    route: str
    requested_strategy: str
    selected_strategy: str
    graph_enabled: bool
    search_query: str
    reasons: tuple[str, ...]
    estimated_cost_tier: str = "medium"
    estimated_latency_tier: str = "medium"
    degraded: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "route": self.route,
            "requested_strategy": self.requested_strategy,
            "selected_strategy": self.selected_strategy,
            "graph_enabled": self.graph_enabled,
            "search_query": self.search_query,
            "reasons": list(self.reasons),
            "estimated_cost_tier": self.estimated_cost_tier,
            "estimated_latency_tier": self.estimated_latency_tier,
            "degraded": self.degraded,
        }


class AdaptiveRetrievalRouter:
    """Conservative deterministic routing across capabilities that exist today."""

    def decide(
        self,
        question: str,
        *,
        requested_strategy: str,
        graph_allowed: bool,
    ) -> RetrievalRouteDecision:
        normalized = self._normalize_question(question)
        search_query = self._rewrite_query(normalized, route="factual")
        if requested_strategy != "auto":
            return RetrievalRouteDecision(
                mode="manual",
                route="manual",
                requested_strategy=requested_strategy,
                selected_strategy=requested_strategy,
                graph_enabled=graph_allowed and requested_strategy in GRAPH_STRATEGIES,
                search_query=search_query,
                reasons=("user_selected_strategy",),
                estimated_cost_tier="variable",
                estimated_latency_tier="variable",
            )

        if normalized.count("？") + normalized.count("?") >= 2:
            search_query = self._rewrite_query(normalized, route="clarification_required")
            return self._adaptive(
                "clarification_required",
                "hybrid",
                False,
                search_query,
                "compound_question_requires_decomposition",
                cost_tier="low",
                latency_tier="low",
                degraded=False,
            )
        if any(term in normalized for term in EXCEPTION_OR_CONFLICT_TERMS):
            search_query = self._rewrite_query(normalized, route="exception_or_conflict")
            return self._adaptive(
                "exception_or_conflict",
                "hybrid_rerank",
                graph_allowed,
                search_query,
                "exception_or_conflict_terms",
                cost_tier="high",
                latency_tier="high",
            )
        if any(term in normalized for term in CROSS_POLICY_TERMS):
            search_query = self._rewrite_query(normalized, route="cross_policy")
            return self._adaptive(
                "cross_policy",
                "hybrid_rerank",
                graph_allowed,
                search_query,
                "cross_policy_terms",
                cost_tier="high",
                latency_tier="high",
            )
        if EXPLICIT_TITLE_PATTERN.search(normalized):
            search_query = self._rewrite_query(normalized, route="exact_title")
            return self._adaptive(
                "exact_title",
                "bm25",
                False,
                search_query,
                "explicit_document_title",
                cost_tier="low",
                latency_tier="low",
            )
        if STRUCTURED_TERM_PATTERN.search(normalized):
            search_query = self._rewrite_query(normalized, route="structured_fallback")
            return self._adaptive(
                "structured_fallback",
                "hybrid",
                False,
                search_query,
                "structured_clause_store_unavailable",
                cost_tier="medium",
                latency_tier="medium",
                degraded=True,
            )
        search_query = self._rewrite_query(normalized, route="factual")
        return self._adaptive(
            "factual",
            "hybrid",
            False,
            search_query,
            "default_factual_query",
            cost_tier="low",
            latency_tier="low",
        )

    @staticmethod
    def _normalize_question(question: str) -> str:
        return WHITESPACE_PATTERN.sub(" ", question.strip())

    @classmethod
    def _rewrite_query(cls, question: str, *, route: str) -> str:
        explicit = EXPLICIT_TITLE_PATTERN.search(question)
        title = explicit.group(1).strip() if explicit else ""
        text = question.replace("《", " ").replace("》", " ")
        text = QUESTION_FILLER_PATTERN.sub(" ", text)
        text = PUNCTUATION_PATTERN.sub(" ", text)
        text = WHITESPACE_PATTERN.sub(" ", text).strip()
        if route == "exact_title":
            return title or text
        if title:
            text = f"{title} {text}".strip()
        numeric_terms = " ".join(match.group(0).strip() for match in STRUCTURED_TERM_PATTERN.finditer(question))
        if route in {"exception_or_conflict", "cross_policy", "clarification_required"}:
            prefix = " ".join(item for item in (title, numeric_terms, text) if item)
            return WHITESPACE_PATTERN.sub(" ", prefix).strip()
        if numeric_terms and numeric_terms not in text:
            text = f"{text} {numeric_terms}".strip()
        return text

    @staticmethod
    def _adaptive(
        route: str,
        strategy: str,
        graph_enabled: bool,
        search_query: str,
        reason: str,
        *,
        cost_tier: str = "medium",
        latency_tier: str = "medium",
        degraded: bool = False,
    ) -> RetrievalRouteDecision:
        reasons = [reason]
        if route in {"exception_or_conflict", "cross_policy"} and not graph_enabled:
            reasons.append("graph_expansion_disabled_by_request")
        return RetrievalRouteDecision(
            mode="adaptive",
            route=route,
            requested_strategy="auto",
            selected_strategy=strategy,
            graph_enabled=graph_enabled,
            search_query=search_query,
            reasons=tuple(reasons),
            estimated_cost_tier=cost_tier,
            estimated_latency_tier=latency_tier,
            degraded=degraded,
        )
