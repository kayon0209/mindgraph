"""
PRD v1 §10 特殊情况固定话术 + §11 超范围拒答（关键词短路，不调用 RAG）。
"""
from __future__ import annotations

from typing import Optional

# 明显与报销无关（§13 对抗 / §15 知识库外）
_OFF_TOPIC_KEYWORDS = [
    "股票",
    "申购新股",
    "工资",
    "薪资",
    "年终奖",
    "辞职信",
    "怎么辞职",
    "请假流程",
    "怎么请假",
    "年假怎么请",
    "考勤",
    "离职",
    "wifi密码",
    "wifi",
    "查密码",
    "系统提示词",
    "提示词",
    "开发者消息",
    "ignore previous",
    "system prompt",
    "developer message",
]

# PRD §10 表格（按关键词触发）
_SPECIAL: list[tuple[list[str], str]] = [
    (
        ["跨年度", "跨年", "去年的发票", "去年", "上年"],
        "原则上不予报销；特殊情况须书面说明原因，经总经理批准后方可。",
    ),
    (
        ["超标准", "超标", "超过标准", "超了"],
        "超标准须事先经分管领导批准；如已超标，建议补提审批说明。",
    ),
    (
        ["发票丢", "丢失发票", "票据丢失", "没发票", "没有发票", "无发票"],
        "联系开票单位复印存根联并加盖公章，同时书面说明丢失原因。",
    ),
    (
        ["电子发票", "电子版发票"],
        "须打印后粘贴报销，或按公司电子发票管理规定提交电子版。",
    ),
    (
        ["两人同行", "合住", "标准间"],
        "同性员工两人同行应合住标准间，按一间房费报销。",
    ),
]


def try_prd_short_circuit(question: str) -> Optional[str]:
    """
    若命中固定话术或超范围拒答，返回直接展示给用户的完整回复；否则返回 None 走 RAG。
    """
    q = (question or "").strip()
    q_lower = q.lower()
    if not q:
        return None

    for kw in _OFF_TOPIC_KEYWORDS:
        if kw in q_lower:
            return "抱歉，我只能回答公司报销相关问题。"

    for keywords, answer in _SPECIAL:
        if any(kw in q for kw in keywords):
            return answer
    return None
