"""评测侧的条件式重排：路由解析。

生产链路里 rerank 路由由 ``chat_service`` 注入（``chat_service.py:166``）；
评测直接调 ``pipeline.retrieve``，若不把路由接进来，条件式重排会因为
``route=""`` 而**全部跳过**（``ConditionalRerankPolicy`` 对空路由判 low_value），
指标看起来等于"没开重排"，延迟收益被误记为"重排没成本"。本模块锁的是路由解析
函数——它是评测口径与生产口径的唯一接缝。
"""

from __future__ import annotations

from evaluation.retrieval_eval import resolve_rerank_route


def test_exception_or_conflict_question_gets_high_value_route() -> None:
    assert resolve_rerank_route("无法取得发票的例外情况怎么处理") == "exception_or_conflict"


def test_cross_policy_question_gets_high_value_route() -> None:
    assert resolve_rerank_route("差旅费和招待费能否同时报销") == "cross_policy"


def test_plain_factual_question_is_not_high_value() -> None:
    """普通事实问题落 low_value 路由 → 条件式重排应跳过它，从而省掉延迟。"""
    assert resolve_rerank_route("差旅费报销的时限是多久") == "factual"


def test_explicit_title_question_keeps_its_own_route() -> None:
    """《制度名》命中走 exact_title 路由，同样不该被当成高收益。"""
    assert resolve_rerank_route("《差旅费报销管理办法》里的住宿标准") == "exact_title"
