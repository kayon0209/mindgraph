"""PR-10｜QueryAnalyzer：结构化查询分析的 **shadow 观测层**。

## 它是 shadow，不是路由

``QueryUnderstandingService`` **已经在生产路径上**（``chat_service.py:91``），
做的是规则版查询改写/拆解，直接决定检索变体。本模块是**独立的观测层**：
输出只写 trace，**不参与路由、检索或生成**——否则就不是 shadow 而是改生产路由。

## 与现有 confidence 的区别

``adaptive_retrieval_router`` 的 confidence 是**两档硬编码**（0.85 / 0.95），
不随问题变化，因此不能当真实置信度用。这里的 confidence 由实际识别到的信号
**计算**得出，并在信号互相矛盾（如出现指代）时下调。

## 刻意不做

- 不调用 LLM 分类（范围外）；
- 默认输出**不含原始问题全文**（范围外：敏感原文不进日志），只留结构化特征。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from application.scope_terms import OUT_OF_SCOPE_TERMS

# ── 信号词表 ──────────────────────────────────────────────────────────────
# 制度域：用于判断"跨制度"。「报销」是动作不是域，不计入，否则
# 「差旅费报销的时限」会被误判成跨制度。
POLICY_DOMAINS: dict[str, tuple[str, ...]] = {
    "差旅": ("差旅", "出差", "交通", "住宿", "机票", "车票"),
    "餐补": ("餐补", "伙食", "用餐", "就餐"),
    "招待": ("招待", "客户"),
    "发票": ("发票", "票据", "开票"),
    "例外": ("例外", "超标准", "特批", "超标"),
}

_ANAPHORA_PATTERN = re.compile(r"该(制度|规定|办法|标准|条款)|这个|那个|它|其[中他]|上述|前者|后者|此(制度|规定)")
_YEAR_PATTERN = re.compile(r"(19|20)\d{2}\s*年?")
_VERSION_PATTERN = re.compile(r"版本|[Vv]\d|新版|旧版|最新版|现行|生效|取代|新旧|矛盾|冲突|以哪个为准")
_CONFLICT_PATTERN = re.compile(r"矛盾|冲突|以哪个为准|以何为准|哪个版本|取代|不一致|相抵触")
_COMPARISON_PATTERN = re.compile(r"区别|对比|相比|哪个更|有哪些不同|分别")
_PROCEDURE_PATTERN = re.compile(r"怎么|如何|流程|步骤|怎样")
_QUANTITY_PATTERN = re.compile(r"多少|几天|几日|时限|期限|多久|多少钱|比例")
_TITLE_PATTERN = re.compile(r"《([^》]{2,})》")

# 与 chat_service 的切分口径保持一致
_QUESTION_SPLIT_PATTERN = re.compile(r"[？?]+")
_NON_KEYWORD_PATTERN = re.compile(r"[，。！？；、,!?;:：\t\r\n（）()【】\[\]{}<>《》\-—~～·/\\|]+")

_BASE_CONFIDENCE = 0.5
_MIN_CONFIDENCE = 0.05
_MAX_CONFIDENCE = 0.95


@dataclass(frozen=True)
class QueryAnalysis:
    """一次查询理解的结构化结论。

    ``complexity`` 与 ``risk`` 是 dict 而非扁平字段：后续 PR 会往里加维度，
    扁平化会导致每次加字段都要改契约。
    """

    intent: str
    entities: tuple[str, ...] = ()
    missing_slots: tuple[str, ...] = ()
    complexity: dict[str, Any] = field(default_factory=dict)
    risk: dict[str, Any] = field(default_factory=dict)
    confidence: float = _BASE_CONFIDENCE
    reasons: tuple[str, ...] = ()
    sub_question_count: int = 1
    target_count: int = 1
    has_anaphora: bool = False
    has_date_version: bool = False
    is_cross_policy: bool = False
    expected_route: str | None = None
    # 归一化后的文本只用于内部计算与调试，**不进 to_dict**（敏感原文不落日志）
    normalized_question: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "entities": list(self.entities),
            "missing_slots": list(self.missing_slots),
            "complexity": dict(self.complexity),
            "risk": dict(self.risk),
            "confidence": round(self.confidence, 4),
            "reasons": list(self.reasons),
            "sub_question_count": self.sub_question_count,
            "target_count": self.target_count,
            "has_anaphora": self.has_anaphora,
            "has_date_version": self.has_date_version,
            "is_cross_policy": self.is_cross_policy,
            "expected_route": self.expected_route,
        }


class QueryAnalysisService:
    """确定性分析器：全部规则，不调用模型。"""

    def analyze(self, question: str) -> QueryAnalysis:
        raw = question or ""
        text = _NON_KEYWORD_PATTERN.sub(" ", raw)
        text = re.sub(r"\s+", " ", text).strip()
        lowered = raw.lower()
        reasons: list[str] = []

        # ── 信号 ──
        domains = sorted({name for name, terms in POLICY_DOMAINS.items() if any(t in raw for t in terms)})
        titles = _TITLE_PATTERN.findall(raw)
        entities = tuple(dict.fromkeys([*titles, *domains]))

        has_anaphora = bool(_ANAPHORA_PATTERN.search(raw))
        has_date_version = bool(_YEAR_PATTERN.search(raw) or _VERSION_PATTERN.search(raw))
        is_cross_policy = len(domains) > 1

        parts = [p.strip() for p in _QUESTION_SPLIT_PATTERN.split(raw) if p.strip()]
        sub_question_count = max(1, len(parts))

        # 目标数看**并列结构**，不看概念数：一句里出现 3 个概念词仍是 1 个目标
        target_count = 1
        if _COMPARISON_PATTERN.search(raw) and "分别" in raw:
            target_count = max(2, len(domains) + len(titles))

        # ── 缺槽 ──
        missing: list[str] = []
        if has_anaphora:
            missing.append("referent")
            reasons.append("anaphora_detected")
        asks_quantity = bool(_QUANTITY_PATTERN.search(raw))
        if asks_quantity and not entities:
            missing.append("policy_subject")
            reasons.append("quantity_asked_without_policy_subject")

        # ── 意图 ──
        if any(term in lowered for term in OUT_OF_SCOPE_TERMS):
            intent = "out_of_scope"
            reasons.append("out_of_scope_term")
        elif _CONFLICT_PATTERN.search(raw):
            intent = "conflict"
            reasons.append("conflict_signal")
        elif _COMPARISON_PATTERN.search(raw):
            intent = "comparison"
            reasons.append("comparison_signal")
        elif _PROCEDURE_PATTERN.search(raw):
            intent = "procedure"
            reasons.append("procedure_signal")
        elif entities:
            intent = "factual"
            reasons.append("policy_entity_present")
        else:
            intent = "unknown"
            reasons.append("no_signal")

        # 完全无关：既非越界词表命中，也无任何制度信号（如"今天天气怎么样"）。
        # 生产词表只覆盖了 HR 类越界词，这类问题当前**不会被拦截**——单独标出来，
        # 让词表缺口可见，而不是假装它是个正常的 factual。
        off_domain = intent not in ("out_of_scope",) and not entities and not asks_quantity
        if off_domain:
            reasons.append("off_domain_no_policy_signal")

        # ── 置信度：真计算，不是常量 ──
        confidence = _BASE_CONFIDENCE
        if entities:
            confidence += 0.15
        if not missing:
            confidence += 0.15
        if intent != "unknown":
            confidence += 0.10
        if has_anaphora:
            confidence -= 0.20
        if "policy_subject" in missing:
            confidence -= 0.15
        if is_cross_policy:
            confidence -= 0.10
        confidence = max(_MIN_CONFIDENCE, min(_MAX_CONFIDENCE, confidence))

        # ── shadow 建议路由（仅供参考，不参与决策）──
        if intent == "out_of_scope":
            expected_route = None  # 生产在 route 之前就拦了，不进路由枚举
        elif intent == "conflict" or has_date_version:
            expected_route = "exception_or_conflict"
        elif is_cross_policy:
            expected_route = "cross_policy"
        elif off_domain or missing:
            expected_route = "clarification_required"
        else:
            expected_route = "factual"

        return QueryAnalysis(
            intent=intent,
            entities=entities,
            missing_slots=tuple(missing),
            complexity={
                "target_count": target_count,
                "sub_question_count": sub_question_count,
                "has_anaphora": has_anaphora,
                "cross_policy": is_cross_policy,
                "date_version": has_date_version,
                "needs_calc": asks_quantity,
                "tool_budget": max(1, target_count),
            },
            risk={
                "ambiguous_reference": has_anaphora,
                "insufficient_evidence_risk": bool(missing),
                "multi_hop": is_cross_policy or target_count > 1,
                "off_domain": off_domain,
            },
            confidence=confidence,
            reasons=tuple(reasons),
            sub_question_count=sub_question_count,
            target_count=target_count,
            has_anaphora=has_anaphora,
            has_date_version=has_date_version,
            is_cross_policy=is_cross_policy,
            expected_route=expected_route,
            normalized_question=text,
        )


def analyze_disagreement(cases: list[dict[str, Any]], analyzer: QueryAnalysisService | None = None) -> dict[str, Any]:
    """把分析器的**建议路由**与生产实际路由对比，产出可解释的分歧报告。

    分歧不等于"生产错了"——分析器只是另一套确定性信号。报告的意义在于
    把「两套规则在哪些问题上不一致」变成可枚举的清单，供人工判断。
    """
    service = analyzer or QueryAnalysisService()
    agreements: list[dict[str, Any]] = []
    disagreements: list[dict[str, Any]] = []
    for case in cases:
        analysis = service.analyze(str(case.get("question") or ""))
        actual = case.get("route")
        expected = analysis.expected_route
        reasons: list[str] = list(analysis.reasons)
        if expected is not None and actual is not None and expected != actual:
            reasons.append(f"expected={expected}, actual={actual}")
        entry: dict[str, Any] = {
            "case_id": case.get("case_id"),
            "question_length": len(str(case.get("question") or "")),
            "intent": analysis.intent,
            "expected_route": expected,
            "actual_route": actual,
            "confidence": round(analysis.confidence, 4),
            "reasons": reasons,
        }
        if expected is None or actual is None or expected == actual:
            agreements.append(entry)
        else:
            disagreements.append(entry)
    return {
        "case_count": len(cases),
        "agreement_count": len(agreements),
        "disagreement_count": len(disagreements),
        "agreements": agreements,
        "disagreements": disagreements,
    }
