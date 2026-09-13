"""PR-11 条件式 Cross-Encoder Rerank：只对高收益请求付 rerank 延迟。

## 现状（任务书核对）

Router 已把 ``exception_or_conflict`` / ``cross_policy`` 两个高收益路由指向
``hybrid_rerank``，但管线只有"装了 reranker 就全量跑"一档。质量门禁要求
「条件式相对全量：质量下降 ≤1pp，P95/成本降 ≥20%」——前提是**能按路由
跳过不值得的请求**，并把"为什么跳过/为什么跑"记录成消融可比的数据。

## 决策语义（与降级严格区分）

- **跳过（skip）**：路由收益低 / 候选太少——这是省成本的**决策**，
  ``actual_strategy`` 仍是 ``hybrid_rerank``，不留 degraded 标记；
- **降级（degrade）**：reranker 未配置 / 超时 / 报错——这是**故障**，
  降回 hybrid 并留 ``degradation_reason``（现状语义，本 PR 不改）。

两层混在一起会让「Rerank 收益」被降级样本稀释（PR-11 任务书补充已指出）。
"""

from __future__ import annotations

from dataclasses import dataclass

# 高收益路由：例外/冲突/跨制度——证据排序错误的业务代价最高。
HIGH_VALUE_ROUTES = frozenset({"exception_or_conflict", "cross_policy"})
# 候选少于此数时 rerank 无法改变结论（top-k 已覆盖），纯浪费延迟。
MIN_CANDIDATES_FOR_VALUE = 3

REASON_HIGH_VALUE = "high_value_route"
REASON_LOW_VALUE = "low_value_route"
REASON_TOO_FEW = "too_few_candidates"


@dataclass(frozen=True)
class RerankDecision:
    should_rerank: bool
    reason: str
    route: str
    candidate_count: int

    def to_dict(self) -> dict:
        return {
            "should_rerank": self.should_rerank,
            "reason": self.reason,
            "route": self.route,
            "candidate_count": self.candidate_count,
        }


class ConditionalRerankPolicy:
    """按 route / 候选规模决定是否值得执行 rerank。

    路由名单与阈值集中在此处（加路由 = 加一行），消融统计按
    ``decision.reason`` 分层——off / all / conditional 三组对比的
    分母由此而来。
    """

    def should_rerank(self, *, route: str, candidate_count: int) -> RerankDecision:
        if candidate_count < MIN_CANDIDATES_FOR_VALUE:
            return RerankDecision(False, REASON_TOO_FEW, route, candidate_count)
        if route in HIGH_VALUE_ROUTES:
            return RerankDecision(True, REASON_HIGH_VALUE, route, candidate_count)
        return RerankDecision(False, REASON_LOW_VALUE, route, candidate_count)


def record_rank_changes(before: list, after: list) -> list[dict]:
    """记录 rerank 前后的逐候选排名变化（消融与人工抽样的数据基础）。

    ``before`` 是 rerank 前的融合排序；``after`` 是 rerank 后的最终排序。
    返回按 after 顺序的变更列表：``from``/``to``/``delta``。
    """
    before_rank = {
        candidate.chunk.chunk_id: rank
        for rank, candidate in enumerate(before, 1)
    }
    changes: list[dict] = []
    for rank, candidate in enumerate(after, 1):
        chunk_id = candidate.chunk.chunk_id
        previous = before_rank.get(chunk_id)
        changes.append({
            "chunk_id": chunk_id,
            "from": previous,
            "to": rank,
            "delta": rank - previous if previous is not None else None,
        })
    return changes
