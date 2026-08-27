from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class RouteReasonCode(StrEnum):
    USER_SELECTED_STRATEGY = "user_selected_strategy"
    COMPOUND_QUESTION_REQUIRES_DECOMPOSITION = "compound_question_requires_decomposition"
    EXCEPTION_OR_CONFLICT_TERMS = "exception_or_conflict_terms"
    CROSS_POLICY_TERMS = "cross_policy_terms"
    EXPLICIT_DOCUMENT_TITLE = "explicit_document_title"
    STRUCTURED_CLAUSE_QUERY_SELECTED = "structured_clause_query_selected"
    DEFAULT_FACTUAL_QUERY = "default_factual_query"
    GRAPH_EXPANSION_DISABLED_BY_REQUEST = "graph_expansion_disabled_by_request"
    ROUTER_FALLBACK = "router_fallback"
    VERSION_CONSTRAINT = "version_constraint"


RouteName = Literal[
    "manual", "factual", "exact_title", "exception_or_conflict", "cross_policy",
    "structured_fallback", "clarification_required",
]


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
VERSION_DATE_PATTERN = re.compile(
    r"(?P<year>20\d{2})\s*(?:年|[-/])\s*(?P<month>\d{1,2})\s*(?:月|[-/])\s*(?P<day>\d{1,2})\s*日?"
)
VERSION_TERM_PATTERN = re.compile(r"(?:版本|生效|失效|截至|之后|以前|当前|现行|最新版|\b[vV]\s*\d+(?:\.\d+)*)")


@dataclass(frozen=True)
class RetrievalRouteDecision:
    mode: str
    route: RouteName
    requested_strategy: str
    selected_strategy: str
    graph_enabled: bool
    search_query: str
    reasons: tuple[RouteReasonCode, ...]
    confidence: float = 1.0
    top_k: int = 5
    filters: dict[str, object] | None = None
    fallback: str | None = None
    estimated_cost_tier: str = "medium"
    estimated_latency_tier: str = "medium"
    degraded: bool = False

    @property
    def reason_codes(self) -> tuple[RouteReasonCode, ...]:
        return self.reasons

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "route": self.route,
            "requested_strategy": self.requested_strategy,
            "selected_strategy": self.selected_strategy,
            "graph_enabled": self.graph_enabled,
            "search_query": self.search_query,
            "reasons": [code.value for code in self.reasons],
            "reason_codes": [code.value for code in self.reason_codes],
            "confidence": self.confidence,
            "top_k": self.top_k,
            "filters": self.filters or {},
            "fallback": self.fallback,
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
        top_k: int = 5,
        filters: dict[str, object] | None = None,
        query_type: str | None = None,
    ) -> RetrievalRouteDecision:
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 50:
            raise ValueError("top_k must be between 1 and 50")
        normalized = self._normalize_question(question)
        route_filters = self._effective_filters(normalized, filters, query_type)
        has_version_constraint = query_type == "versioned_policy" or bool(
            VERSION_DATE_PATTERN.search(normalized) or VERSION_TERM_PATTERN.search(normalized)
        )
        search_query = self._rewrite_query(normalized, route="factual")
        if requested_strategy != "auto":
            return RetrievalRouteDecision(
                mode="manual",
                route="manual",
                requested_strategy=requested_strategy,
                selected_strategy=requested_strategy,
                graph_enabled=graph_allowed and requested_strategy in GRAPH_STRATEGIES,
                search_query=search_query,
                reasons=(RouteReasonCode.USER_SELECTED_STRATEGY,),
                confidence=1.0,
                filters=route_filters,
                top_k=top_k,
                fallback=None,
                estimated_cost_tier="variable",
                estimated_latency_tier="variable",
            )

        if query_type in {"clarification", "clarification_required", "compound_question"}:
            search_query = self._rewrite_query(normalized, route="clarification_required")
            return self._adaptive(
                "clarification_required",
                "hybrid",
                False,
                search_query,
                RouteReasonCode.COMPOUND_QUESTION_REQUIRES_DECOMPOSITION,
                cost_tier="low",
                top_k=top_k,
                filters=route_filters,
                fallback="hybrid",
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
                RouteReasonCode.EXCEPTION_OR_CONFLICT_TERMS,
                cost_tier="high",
                top_k=top_k,
                filters=route_filters,
                fallback="hybrid",
                latency_tier="high",
                extra_reasons=(RouteReasonCode.VERSION_CONSTRAINT,) if has_version_constraint else (),
            )
        if any(term in normalized for term in CROSS_POLICY_TERMS):
            search_query = self._rewrite_query(normalized, route="cross_policy")
            return self._adaptive(
                "cross_policy",
                "hybrid_rerank",
                graph_allowed,
                search_query,
                RouteReasonCode.CROSS_POLICY_TERMS,
                cost_tier="high",
                top_k=top_k,
                filters=route_filters,
                fallback="hybrid",
                latency_tier="high",
                extra_reasons=(RouteReasonCode.VERSION_CONSTRAINT,) if has_version_constraint else (),
            )
        if EXPLICIT_TITLE_PATTERN.search(normalized):
            search_query = self._rewrite_query(normalized, route="exact_title")
            return self._adaptive(
                "exact_title",
                "bm25",
                False,
                search_query,
                RouteReasonCode.EXPLICIT_DOCUMENT_TITLE,
                cost_tier="low",
                top_k=top_k,
                filters=route_filters,
                fallback="hybrid",
                latency_tier="low",
                extra_reasons=(RouteReasonCode.VERSION_CONSTRAINT,) if has_version_constraint else (),
            )
        if has_version_constraint or STRUCTURED_TERM_PATTERN.search(normalized):
            search_query = self._rewrite_query(normalized, route="structured_fallback")
            return self._adaptive(
                "structured_fallback",
                "hybrid",
                False,
                search_query,
                RouteReasonCode.STRUCTURED_CLAUSE_QUERY_SELECTED,
                cost_tier="medium",
                top_k=top_k,
                filters=route_filters,
                fallback="hybrid",
                latency_tier="medium",
                degraded=False,
                extra_reasons=(RouteReasonCode.VERSION_CONSTRAINT,) if has_version_constraint else (),
            )
        search_query = self._rewrite_query(normalized, route="factual")
        return self._adaptive(
            "factual",
            "hybrid",
            False,
            search_query,
            RouteReasonCode.DEFAULT_FACTUAL_QUERY,
            cost_tier="low",
            top_k=top_k,
            filters=route_filters,
            fallback="hybrid",
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
            return text or title
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
    def _effective_filters(
        question: str,
        filters: dict[str, object] | None,
        query_type: str | None,
    ) -> dict[str, object]:
        result = dict(filters or {})
        explicit = VERSION_DATE_PATTERN.search(question)
        if explicit:
            year = int(explicit.group("year"))
            month = int(explicit.group("month"))
            day = int(explicit.group("day"))
            try:
                from datetime import date

                result["effective_at"] = date(year, month, day).isoformat()
            except ValueError:
                pass
        elif query_type == "versioned_policy" and isinstance(result.get("query_date"), str):
            result["effective_at"] = result["query_date"]
        return result

    @staticmethod
    def _adaptive(
        route: RouteName,
        strategy: str,
        graph_enabled: bool,
        search_query: str,
        reason: RouteReasonCode,
        *,
        cost_tier: str = "medium",
        latency_tier: str = "medium",
        degraded: bool = False,
        top_k: int = 5,
        filters: dict[str, object] | None = None,
        fallback: str | None = None,
        extra_reasons: tuple[RouteReasonCode, ...] = (),
    ) -> RetrievalRouteDecision:
        reasons = [reason if isinstance(reason, RouteReasonCode) else RouteReasonCode(reason)]
        reasons.extend(code for code in extra_reasons if code not in reasons)
        if route in {"exception_or_conflict", "cross_policy"} and not graph_enabled:
            reasons.append(RouteReasonCode.GRAPH_EXPANSION_DISABLED_BY_REQUEST)
        confidence = 0.85 if route in {"exception_or_conflict", "cross_policy", "structured_fallback"} else 0.95
        return RetrievalRouteDecision(
            mode="adaptive",
            route=route,
            requested_strategy="auto",
            selected_strategy=strategy,
            graph_enabled=graph_enabled,
            search_query=search_query,
            reasons=tuple(reasons),
            confidence=confidence,
            top_k=top_k,
            filters=filters or {},
            fallback=fallback,
            estimated_cost_tier=cost_tier,
            estimated_latency_tier=latency_tier,
            degraded=degraded,
        )
