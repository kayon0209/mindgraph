"""PR-11 条件式 Cross-Encoder Rerank：按路由条件启用 + 排名变化可观测。

任务书测试矩阵：
- 条件命中与不命中（高收益路由 rerank，简单事实问题跳过）；
- 模型不可用、超时、空候选（降级路径完整）；
- ACL 过滤前后不可泄漏；
- 记录 rerank 前后排名变化（消融三层对比 off/all/conditional 的数据基础）。

不变性：``CONDITIONAL_RERANK_ENABLED`` 默认关 → 行为与现状完全一致。
"""

from __future__ import annotations

# ── 条件策略：按路由决定是否值得付 rerank 延迟 ─────────────────────────


def test_policy_hits_high_value_routes():
    """高收益路由（例外/冲突/跨制度）命中 → 建议 rerank。"""
    from application.conditional_rerank import ConditionalRerankPolicy

    policy = ConditionalRerankPolicy()
    for route in ("exception_or_conflict", "cross_policy"):
        decision = policy.should_rerank(route=route, candidate_count=8)
        assert decision.should_rerank is True, route
        assert decision.reason == "high_value_route"


def test_policy_skips_low_value_routes():
    """简单事实/标题精确路由不命中 → 跳过并说明（省延迟不是降级）。"""
    from application.conditional_rerank import ConditionalRerankPolicy

    policy = ConditionalRerankPolicy()
    for route in ("factual", "exact_title", "structured_fallback"):
        decision = policy.should_rerank(route=route, candidate_count=8)
        assert decision.should_rerank is False, route
        assert decision.reason == "low_value_route"


def test_policy_skips_tiny_candidate_sets():
    """候选太少时 rerank 无意义（top-k 已覆盖）→ 跳过。"""
    from application.conditional_rerank import ConditionalRerankPolicy

    policy = ConditionalRerankPolicy()
    decision = policy.should_rerank(route="exception_or_conflict", candidate_count=2)
    assert decision.should_rerank is False
    assert decision.reason == "too_few_candidates"


def test_policy_decision_is_explainable():
    """决策必须可解释：带 reason 与全部评估字段（消融统计的基础）。"""
    from application.conditional_rerank import ConditionalRerankPolicy

    decision = ConditionalRerankPolicy().should_rerank(route="factual", candidate_count=10)
    payload = decision.to_dict()
    assert {"should_rerank", "reason", "route", "candidate_count"} <= set(payload)


# ── 管线集成：条件跳过 ≠ 降级 ───────────────────────────────────────────


class _StubReranker:
    """可观测的假 reranker：记录调用并按反转顺序重排。"""

    def __init__(self):
        self.calls: list[str] = []

    def rerank(self, query, candidates, top_k):
        self.calls.append(query)
        ranked = sorted(candidates, key=lambda c: c.chunk.chunk_id, reverse=True)[:top_k]
        for rank, candidate in enumerate(ranked, 1):
            candidate.reranker_score, candidate.final_rank = 1.0 / rank, rank
        return ranked


def _pipeline_with(reranker, **flags):
    from retrieval.pipeline import RetrievalPipeline

    class _Dense:
        chunks, metadata = [], {}

        def search(self, query, top_k, **kwargs):
            return [], {}

    class _Sparse:
        def search(self, query, top_k, **kwargs):
            return [], {}

    class _Fusion:
        def fuse(self, lists, top_k):
            return []

    return RetrievalPipeline(_Dense(), _Sparse(), _Fusion(), reranker, **flags)


def _candidate(chunk_id: str):
    from retrieval.types import Chunk, RetrievalCandidate

    chunk = Chunk(chunk_id=chunk_id, text="内容", document_id="d", chunk_index=0,
                  section_path=None, metadata={})
    return RetrievalCandidate(chunk=chunk, rrf_score=0.5, fused_rank=1)


def test_conditional_rerank_skipped_is_not_degradation():
    """条件不命中跳过 rerank：不设 degraded（省延迟是决策，模型坏才是降级）。"""
    pipeline = _pipeline_with(_StubReranker(), conditional_rerank=True)
    trace = pipeline.retrieve("简单事实问题", "hybrid_rerank")
    # stub 检索器无候选 → 候选不足路径；重点验证 trace 语义：
    assert trace.degraded is False
    assert trace.actual_strategy == "hybrid_rerank"


def test_rerank_rank_changes_recorded():
    """rerank 后排名变化必须可观测（before/after + delta）。"""
    from application.conditional_rerank import record_rank_changes

    before = [_candidate("a"), _candidate("b"), _candidate("c")]
    after = [before[2], before[0], before[1]]  # 重排：c→1, a→2, b→3
    changes = record_rank_changes(before, after)
    by_id = {item["chunk_id"]: item for item in changes}
    assert by_id["c"]["from"] == 3 and by_id["c"]["to"] == 1 and by_id["c"]["delta"] == -2
    assert by_id["a"]["from"] == 1 and by_id["a"]["to"] == 2


def test_rerank_model_missing_degrades_to_hybrid():
    """模型缺失 → 降级 hybrid + degradation_reason（现状语义必须保留）。"""
    pipeline = _pipeline_with(None)  # 无 reranker
    trace = pipeline.retrieve("例外情况怎么处理", "hybrid_rerank")
    assert trace.degraded is True
    assert trace.actual_strategy == "hybrid"
    assert trace.degradation_reason == "reranker_disabled"


def test_acl_no_leak_across_rerank_boundary():
    """ACL 过滤面在 rerank 前后都生效：reranked 结果必须再次过 ACL。"""
    from retrieval.pipeline import RetrievalPipeline

    class _Dense:
        chunks, metadata = [], {}

        def search(self, query, top_k, **kwargs):
            from retrieval.types import Chunk, RetrievalCandidate
            allowed = Chunk(chunk_id="ok-1", text="可见", document_id="d", chunk_index=0,
                            section_path=None, metadata={"workspace": "w1"})
            denied = Chunk(chunk_id="denied-1", text="不可见", document_id="d", chunk_index=1,
                           section_path=None, metadata={"workspace": "w2", "acl_json": '{"deny":["user"]}'} )
            return [
                RetrievalCandidate(chunk=allowed, dense_score=0.9, dense_rank=1),
                RetrievalCandidate(chunk=denied, dense_score=0.8, dense_rank=2),
            ], {}

    class _Sparse:
        def search(self, query, top_k, **kwargs):
            return [], {}

    class _Fusion:
        def fuse(self, lists, top_k):
            return lists[0]

    scope = {"user": "user-a", "allow": ["workspace:w1"], "deny": []}
    pipeline = RetrievalPipeline(_Dense(), _Sparse(), _Fusion(), _StubReranker(),
                                 conditional_rerank=False)
    trace = pipeline.retrieve("测试", "hybrid_rerank", access_scope=scope)
    ids = [c.chunk.chunk_id for c in trace.final_selected_chunks]
    assert "denied-1" not in ids, "rerank 面不得重新引入 ACL 已拒绝的候选"
