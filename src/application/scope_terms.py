"""越界（out-of-scope）词表的单一来源。

为什么要单独成模块
--------------------
这份词表原先直接定义在 ``chat_service.py`` 里。PR-10 的观测层（``query_analysis``）
需要用**同一份**词表来判断"这个问题是否越界"，否则 shadow 结论与生产拦截会各说各话
（生产说没越界、shadow 说越界，分歧报告就失去意义）。

但 ``query_analysis`` 又要被 ``chat_service`` 引用，直接互相 import 会形成环。
把词表下沉到这个无任何依赖的模块，两边都能安全引用。

⚠️ 改这个词表 = 改产品口径：新增词会让更多问题被拒答，已公布的指标随之失效。
"""
from __future__ import annotations

OUT_OF_SCOPE_TERMS: tuple[str, ...] = (
    "工资", "薪资", "年终奖", "股票", "请假", "年假", "辞职", "离职",
    "wifi", "食堂", "系统提示词", "ignore previous", "system prompt",
)
