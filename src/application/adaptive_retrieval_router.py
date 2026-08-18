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
EXPLICIT_TITLE_PATTERN = re.compile(r"《[^》]{2,}》")
STRUCTURED_TERM_PATTERN = re.compile(
    r"(?:\b[vV]\s*\d+(?:\.\d+)*\b|\d+(?:\.\d+)?\s*(?:元|万元|%|天|小时))"
)


@dataclass(frozen=True)
class RetrievalRouteDecision:
    mode: str
    route: str
    requested_strategy: str
    selected_strategy: str
    graph_enabled: bool
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
        if requested_strategy != "auto":
            return RetrievalRouteDecision(
                mode="manual",
                route="manual",
                requested_strategy=requested_strategy,
                selected_strategy=requested_strategy,
                graph_enabled=graph_allowed and requested_strategy in GRAPH_STRATEGIES,
                reasons=("user_selected_strategy",),
                estimated_cost_tier="variable",
                estimated_latency_tier="variable",
            )

        normalized = question.strip()
        if normalized.count("？") + normalized.count("?") >= 2:
            return self._adaptive(
                "clarification_required",
                "hybrid",
                False,
                "compound_question_requires_decomposition",
                cost_tier="low",
                latency_tier="low",
                degraded=False,
            )
        if any(term in normalized for term in EXCEPTION_OR_CONFLICT_TERMS):
            return self._adaptive(
                "exception_or_conflict",
                "hybrid_rerank",
                graph_allowed,
                "exception_or_conflict_terms",
                cost_tier="high",
                latency_tier="high",
            )
        if any(term in normalized for term in CROSS_POLICY_TERMS):
            return self._adaptive(
                "cross_policy",
                "hybrid_rerank",
                graph_allowed,
                "cross_policy_terms",
                cost_tier="high",
                latency_tier="high",
            )
        if EXPLICIT_TITLE_PATTERN.search(normalized):
            return self._adaptive(
                "exact_title",
                "bm25",
                False,
                "explicit_document_title",
                cost_tier="low",
                latency_tier="low",
            )
        if STRUCTURED_TERM_PATTERN.search(normalized):
            return self._adaptive(
                "structured_fallback",
                "hybrid",
                False,
                "structured_clause_store_unavailable",
                cost_tier="medium",
                latency_tier="medium",
                degraded=True,
            )
        return self._adaptive(
            "factual",
            "hybrid",
            False,
            "default_factual_query",
            cost_tier="low",
            latency_tier="low",
        )

    @staticmethod
    def _adaptive(
        route: str,
        strategy: str,
        graph_enabled: bool,
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
            reasons=tuple(reasons),
            estimated_cost_tier=cost_tier,
            estimated_latency_tier=latency_tier,
            degraded=degraded,
        )
