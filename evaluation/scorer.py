"""评分逻辑：关键词匹配 + LLM 幻觉检测（混合评分）。

评分规则：
- 关键题（expected_type != reject）：关键词命中 ≥ 1 个 → 关键词分 1.0，否则 0.0
- 拒答题（reject_expected=True）：包含拒答话术 → 1.0，否则 0.0
- LLM 幻觉检测：调用同一模型判定答案是否与检索上下文一致（0/1）
- 综合分 = 关键词分 × 0.6 + 幻觉检测分 × 0.4
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from zhipuai import ZhipuAI


# 拒答话术关键词
_REJECT_PHRASES = [
    "我只能回答公司报销相关问题",
    "抱歉",
    "与报销无关",
    "超出我的服务范围",
    "无法回答",
    "不在我",
]


@dataclass
class ScoreResult:
    case_id: int
    category: str
    question: str
    answer: str
    expected_type: str
    reject_expected: bool
    keyword_score: float          # 0.0 or 1.0
    hallucination_score: float    # 0.0 or 1.0
    final_score: float            # keyword * 0.6 + hallucination * 0.4
    keyword_hits: List[str]
    hallucination_reason: str


def keyword_match(answer: str, expected_keywords: List[str]) -> tuple[float, List[str]]:
    """检查答案中是否命中期望关键词，命中任意一个即得满分。"""
    if not expected_keywords:
        return 1.0, []  # 无关键词要求时默认通过
    hits = [kw for kw in expected_keywords if kw in answer]
    return (1.0 if hits else 0.0), hits


def reject_check(answer: str) -> float:
    """检查答案是否包含拒答话术。"""
    return 1.0 if any(p in answer for p in _REJECT_PHRASES) else 0.0


def llm_hallucination_check(
    client: ZhipuAI,
    question: str,
    answer: str,
    context: str,
    model: str = "glm-4.5-air",
) -> tuple[float, str]:
    """调用 LLM 判断答案是否存在幻觉（与检索上下文矛盾/编造信息）。

    返回 (score, reason)：score=1.0 表示无幻觉，0.0 表示有幻觉。
    """
    if not context:
        # 无上下文时，如果答案有实质内容且非拒答，视为幻觉
        has_reject = any(p in answer for p in _REJECT_PHRASES)
        if has_reject:
            return 1.0, "无上下文但正确拒答"
        return 0.0, "无上下文却给出了实质回答，可能为幻觉"

    prompt = f"""请判断以下回答是否基于给定的制度原文，是否存在编造或与原文矛盾的信息。

制度原文：
{context}

用户问题：{question}

系统回答：
{answer}

请只输出 JSON，格式如下：
{{"hallucination": true/false, "reason": "一句话说明"}}

如果回答严格基于原文或正确拒答，hallucination 为 false；如果包含原文中不存在的信息或与原文矛盾，hallucination 为 true。"""

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        content = resp.choices[0].message.content or ""
        # 简单解析
        if '"hallucination": false' in content or '"hallucination":false' in content:
            reason = ""
            for line in content.split('"reason"'):
                if ":" in line:
                    reason = line.split(":")[-1].strip(' ",}')
                    break
            return 1.0, reason or "LLM判定无幻觉"
        else:
            reason = ""
            for line in content.split('"reason"'):
                if ":" in line:
                    reason = line.split(":")[-1].strip(' ",}')
                    break
            return 0.0, reason or "LLM判定存在幻觉"
    except Exception as e:
        return 0.5, f"LLM检测调用失败: {e}"


def score_case(
    case: Dict,
    answer: str,
    context: str,
    *,
    client: Optional[ZhipuAI] = None,
    model: str = "glm-4.5-air",
    enable_llm_check: bool = True,
) -> ScoreResult:
    """对单个测试用例评分。"""
    expected_keywords = case.get("expected_keywords", [])
    reject_expected = case.get("reject_expected", False)

    # 关键词评分
    if reject_expected:
        kw_score = reject_check(answer)
        kw_hits = [p for p in _REJECT_PHRASES if p in answer]
    else:
        kw_score, kw_hits = keyword_match(answer, expected_keywords)

    # 幻觉检测
    if enable_llm_check and client is not None:
        hal_score, hal_reason = llm_hallucination_check(
            client, case["question"], answer, context, model=model
        )
    else:
        # 无 LLM 检测时，关键词通过即认为无幻觉
        hal_score = 1.0 if kw_score > 0 else 0.5
        hal_reason = "未启用LLM检测"

    final = round(kw_score * 0.6 + hal_score * 0.4, 2)

    return ScoreResult(
        case_id=case["id"],
        category=case["category"],
        question=case["question"],
        answer=answer,
        expected_type=case["expected_type"],
        reject_expected=reject_expected,
        keyword_score=kw_score,
        hallucination_score=hal_score,
        final_score=final,
        keyword_hits=kw_hits,
        hallucination_reason=hal_reason,
    )
