from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
import hashlib
import inspect
import logging
import time
from typing import Any
import uuid

from application.adaptive_retrieval_router import AdaptiveRetrievalRouter, RetrievalRouteDecision
from application.policy_conflict_service import PolicyConflictService
from application.query_analysis import QueryAnalysisService
from application.query_understanding import QueryUnderstandingService
from application.scope_terms import OUT_OF_SCOPE_TERMS
from domain.contracts import error_event_data
from domain.errors import RetrievalUnavailableError
from domain.models import (
    AnswerResult,
    ChatRequest,
    Citation,
    ResultState,
    RetrievalTraceModel,
    TimingMetrics,
    UsageMetrics,
)
from infrastructure.database import ProductDatabase, dumps

# PR-10：词表下沉到 scope_terms，供生产拦截与 query_analysis 的 shadow 观测共用同一份。
OUT_OF_SCOPE = OUT_OF_SCOPE_TERMS
REFUSAL = "抱歉，我只能回答公司报销相关问题。"
INSUFFICIENT = "未在制度文件中找到足够依据。建议联系 HR/财务确认。"
PERMISSION_DENIED = "当前账号没有权限访问相关制度内容。请联系管理员申请对应工作区/部门的访问权限。"
CONFLICTING = "检测到同一制度在查询日期存在多个有效版本，已停止生成答案。请由制度责任人确认有效版本。"
logger = logging.getLogger("mindgraph.chat")


def _acl_filtered_everything(trace) -> bool:
    """判断“零引用”是否由 ACL 裁剪导致（计划 3.3：权限不足 → 拒绝/安全提示）。"""
    return bool(trace) and "access_denied_chunks_filtered" in getattr(trace, "warnings", [])

DEFAULT_SYSTEM_PROMPT = "你是企业报销政策助手。只能依据给定制度证据回答；不得编造。先给结论，再给简要依据，并使用 [citation-N] 标注引用。"


class ChatService:
    def __init__(
        self,
        database: ProductDatabase,
        pipeline_factory,
        provider,
        privacy_log_questions: bool = True,
        system_prompt: str | None = None,
        retrieval_router: AdaptiveRetrievalRouter | None = None,
        query_understanding: QueryUnderstandingService | None = None,
        query_analysis: QueryAnalysisService | None = None,
        graph_default_enabled: bool = False,
        on_question_logged: Callable[[], None] | None = None,
    ) -> None:
        self.database = database
        self.pipeline_factory = pipeline_factory
        self.provider = provider
        self.privacy_log_questions = privacy_log_questions
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self.policy_conflict_service = PolicyConflictService(database)
        self.retrieval_router = retrieval_router or AdaptiveRetrievalRouter()
        self.query_understanding = query_understanding or QueryUnderstandingService()
        # PR-10：shadow 观测层（只写 trace，不参与路由/检索/生成）
        self.query_analysis = query_analysis or QueryAnalysisService()
        # 计划 Phase 5 发布闸门的配置消费方：消融达标后由 GRAPH_DEFAULT_ENABLED
        # 打开服务端默认图路由；客户端 graph_enabled=false 始终可以关闭。
        self.graph_default_enabled = graph_default_enabled
        # 阶段B：提问落库后的可选回调（容器注入，用于问题概念挖掘的自动触发计数）。
        self.on_question_logged = on_question_logged

    def _provider(self, name: str | None = None, model: str | None = None):
        return self.provider.get(name, model) if hasattr(self.provider, "get") else self.provider

    def _route(self, request: ChatRequest) -> tuple[RetrievalRouteDecision, float]:
        started = time.perf_counter()
        graph_allowed = bool(getattr(request, "graph_enabled", False)) or self.graph_default_enabled
        decision = self.retrieval_router.decide(
            request.question,
            requested_strategy=request.retrieval_strategy,
            graph_allowed=graph_allowed,
            top_k=request.final_top_k,
            filters={
                "query_date": request.query_date,
                "knowledge_categories": request.knowledge_categories or [],
                "include_historical": request.include_historical,
            },
            query_type=getattr(request, "query_type", None),
        )
        return decision, round((time.perf_counter() - started) * 1000, 3)

    def _merge_query_variants(self, decision: RetrievalRouteDecision, request: ChatRequest) -> tuple[str, tuple[str, ...], str]:
        plan = self.query_understanding.plan(request.question, decision)
        planned = plan.variants or (decision.search_query,)
        variants = tuple(dict.fromkeys(item for item in planned if item and item.strip()))
        return plan.mode, variants, plan.reasons[0] if plan.reasons else "no_query_understanding_required"

    @staticmethod
    def _merge_candidates(candidate_groups: Iterable[list[Any]], limit: int) -> list[Any]:
        merged: dict[str, Any] = {}
        for group in candidate_groups:
            for candidate in group:
                chunk_id = candidate.chunk.chunk_id
                current = merged.get(chunk_id)
                if current is None:
                    merged[chunk_id] = candidate
                    continue
                current_score = max(
                    value for value in (
                        current.reranker_score, current.rrf_score,
                        current.dense_score, current.sparse_score, current.original_score,
                    ) if value is not None
                ) if any(value is not None for value in (
                    current.reranker_score, current.rrf_score,
                    current.dense_score, current.sparse_score, current.original_score,
                )) else 0.0
                candidate_score = max(
                    value for value in (
                        candidate.reranker_score, candidate.rrf_score,
                        candidate.dense_score, candidate.sparse_score, candidate.original_score,
                    ) if value is not None
                ) if any(value is not None for value in (
                    candidate.reranker_score, candidate.rrf_score,
                    candidate.dense_score, candidate.sparse_score, candidate.original_score,
                )) else 0.0
                if candidate_score > current_score:
                    merged[chunk_id] = candidate
        output = sorted(
            merged.values(),
            key=lambda item: (
                max(value for value in (
                    item.reranker_score, item.rrf_score,
                    item.dense_score, item.sparse_score, item.original_score,
                ) if value is not None) if any(value is not None for value in (
                    item.reranker_score, item.rrf_score,
                    item.dense_score, item.sparse_score, item.original_score,
                )) else 0.0,
                item.chunk.chunk_id,
            ),
            reverse=True,
        )
        for rank, candidate in enumerate(output[:limit], 1):
            candidate.final_rank = rank
        return output[:limit]

    def _retrieve(self, request: ChatRequest, decision: RetrievalRouteDecision, routing_ms: float, access_scope: dict | None = None):
        pipeline = self.pipeline_factory(request.final_top_k)
        parameters = inspect.signature(pipeline.retrieve).parameters
        kwargs: dict[str, Any] = {}
        if access_scope is not None and "access_scope" in parameters:
            kwargs["access_scope"] = access_scope
        effective_query_date = (decision.filters or {}).get("effective_at") or request.query_date
        mode, variants, reason = self._merge_query_variants(decision, request)

        def retrieve_variant(query_text: str):
            if "graph_enabled" not in parameters:
                if "query_date" not in parameters:
                    return pipeline.retrieve(query_text, decision.selected_strategy, **kwargs)
                return pipeline.retrieve(
                    query_text, decision.selected_strategy, effective_query_date,
                    request.knowledge_categories, request.include_historical, **kwargs,
                )
            # graph_hops 仅在管线支持时传递（计划 4.4 两跳能力由请求显式驱动）
            graph_hops_kwargs: dict[str, Any] = {}
            if "graph_hops" in parameters:
                graph_hops_kwargs["graph_hops"] = getattr(request, "graph_hops", 1) or 1
            if "query_date" not in parameters:
                return pipeline.retrieve(
                    query_text, decision.selected_strategy,
                    graph_enabled=decision.graph_enabled, **graph_hops_kwargs, **kwargs,
                )
            return pipeline.retrieve(
                query_text, decision.selected_strategy, effective_query_date,
                request.knowledge_categories, request.include_historical,
                graph_enabled=decision.graph_enabled, **graph_hops_kwargs, **kwargs,
            )

        traces = [retrieve_variant(query_text) for query_text in variants]
        trace = traces[0]
        if len(traces) > 1:
            trace.dense_results = self._merge_candidates((item.dense_results for item in traces), len(trace.dense_results))
            trace.sparse_results = self._merge_candidates((item.sparse_results for item in traces), len(trace.sparse_results))
            trace.fused_results = self._merge_candidates((item.fused_results for item in traces), len(trace.fused_results))
            trace.reranked_results = self._merge_candidates((item.reranked_results for item in traces), len(trace.reranked_results))
            trace.final_selected_chunks = self._merge_candidates(
                (item.final_selected_chunks for item in traces),
                len(trace.final_selected_chunks),
            )
            trace.candidate_counts = {
                "dense": len(trace.dense_results),
                "sparse": len(trace.sparse_results),
                "fused": len(trace.fused_results),
                "reranked": len(trace.reranked_results),
                "final": len(trace.final_selected_chunks),
            }
            trace.latency_ms["variant_count"] = float(len(variants))
        trace.query_variants = list(variants)
        trace.original_query = request.question
        trace.warnings.append(f"query_understanding:{mode}:{reason}")
        trace.requested_strategy = request.retrieval_strategy
        trace.route_decision = decision.to_dict()
        trace.latency_ms["routing_ms"] = routing_ms
        trace.latency_ms["total_retrieval_ms"] = round(
            sum(value for key, value in trace.latency_ms.items() if key != "total_retrieval_ms"),
            3,
        )
        trace.latency_ms["query_understanding_ms"] = 0.0
        self._attach_query_analysis(trace, request.question)
        return trace

    def _attach_query_analysis(self, trace, question: str) -> None:
        """PR-10：把结构化查询分析以 **shadow** 方式挂到 trace 上。

        三条硬约束：
        1. **只写 trace**，不参与路由、检索或生成 —— 否则就不是 shadow；
        2. 任何异常都必须吞掉并记 warning —— 观测层出错可以接受，
           因为它拖垮生产问答不可接受；
        3. 输出不含原始问题全文（见 ``QueryAnalysis.to_dict``）。
        """
        try:
            started = time.perf_counter()
            trace.query_analysis = self.query_analysis.analyze(question).to_dict()
            trace.latency_ms["query_analysis_ms"] = round((time.perf_counter() - started) * 1000, 3)
        except Exception as exc:  # noqa: BLE001 -- shadow 不得影响生产路径
            logger.warning("query_analysis_shadow_failed", extra={"error": str(exc)[:200]})
            trace.query_analysis = {}

    @staticmethod
    def _is_out_of_scope(question: str) -> bool:
        lowered = question.lower()
        return any(term in lowered for term in OUT_OF_SCOPE)

    @staticmethod
    def _trace_model(trace) -> RetrievalTraceModel:
        payload = trace.to_dict()
        # 权限侧信道修正（审查发现）：applied_filters.access_scope 携带主体的
        # allow/deny ACL 规则，不得持久化进 query_logs（trace 可经只读端点读取）。
        # 持久化面只留布尔标记 acl_applied；内存中的 trace 对象不受影响。
        applied_filters = dict(payload.get("applied_filters", {}))
        if "access_scope" in applied_filters:
            applied_filters["acl_applied"] = applied_filters["access_scope"] is not None
            applied_filters.pop("access_scope")
        return RetrievalTraceModel(
            requested_strategy=payload["requested_strategy"], actual_strategy=payload["actual_strategy"],
            candidate_counts=payload["candidate_counts"], dense_results=payload["dense_results"],
            sparse_results=payload["sparse_results"], fusion_results=payload["fused_results"],
            reranked_results=payload["reranked_results"], final_chunks=payload["final_selected_chunks"],
            stage_latency_ms=payload["latency_ms"], degraded=payload["degraded"],
            degradation_reason=payload["degradation_reason"],
            index_version=payload.get("index_version"), applied_filters=applied_filters,
            warnings=payload.get("warnings", []),
            graph_enabled=getattr(trace, "graph_enabled", False),
            graph_hops=getattr(trace, "graph_hops", 1),
            graph_links=getattr(trace, "graph_links", []),
            route_decision=payload.get("route_decision", {}),
            query_variants=payload.get("query_variants", []),
            original_query=payload.get("original_query"),
        )

    @staticmethod
    def _citations(trace) -> list[Citation]:
        citations = []
        for candidate in trace.final_selected_chunks:
            score = candidate.original_score
            metadata = candidate.chunk.metadata
            citations.append(Citation(
                citation_id=f"citation-{candidate.final_rank}", document_id=candidate.chunk.document_id,
                document_name=metadata.get("document_title") or metadata.get("title") or candidate.chunk.document_id, chunk_id=candidate.chunk.chunk_id,
                section_path=candidate.chunk.section_path, excerpt=candidate.chunk.text[:500],
                final_rank=candidate.final_rank or 0, retrieval_score=score,
                reranker_score=candidate.reranker_score, document_version=metadata.get("document_version"),
                owner=metadata.get("owner"), effective_from=metadata.get("effective_from"),
                effective_to=metadata.get("effective_to"), policy_status=metadata.get("policy_status"),
                authority_level=metadata.get("authority_level"), knowledge_category=metadata.get("knowledge_category"),
                authority_adjustment=candidate.authority_adjustment, vault_path=metadata.get("vault_path"),
                policy_key=metadata.get("policy_key"),
            ))
        return citations

    def _policy_conflicts(self, citations: list[Citation], request: ChatRequest, *, access_scope: dict | None = None) -> list[dict[str, Any]]:
        return self.policy_conflict_service.find_for_policy_keys(
            {item.policy_key for item in citations if item.policy_key},
            as_of=request.query_date,
            include_historical=request.include_historical,
            access_scope=access_scope,
        )

    def _messages(self, question: str, citations: list[Citation], graph_links: list[dict] | None = None) -> list[dict[str, str]]:
        context = "\n\n".join(f"[{item.citation_id}] {item.document_name} / {item.section_path or '-'}\n{item.excerpt}" for item in citations)
        system = self.system_prompt
        if graph_links:
            links = "\n".join(
                f"- 通过「{g['relation_type']}」关系关联到《{g['target_title']}》"
                for g in graph_links
            )
            system += (
                "\n\n【知识关联提示】本次检索通过知识图谱关系扩展了以下关联笔记，"
                "其引用片段已包含在证据中。回答时若使用了关联笔记内容，请照常使用 [citation-N] 标注，"
                "并在必要时说明其来源关系，便于用户溯源。"
                f"\n关联笔记：\n{links}"
            )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": f"制度证据：\n{context}\n\n问题：{question}"},
        ]

    @staticmethod
    def _timing(trace, started: float, generation_ms: float | None, ttft_ms: float | None) -> TimingMetrics:
        latency = trace.latency_ms if trace else {}
        return TimingMetrics(
            embedding_ms=latency.get("query_embedding_ms"), dense_retrieval_ms=latency.get("dense_retrieval_ms"),
            sparse_retrieval_ms=latency.get("bm25_retrieval_ms"), fusion_ms=latency.get("fusion_ms"),
            rerank_ms=latency.get("reranker_ms"), generation_ms=generation_ms, ttft_ms=ttft_ms,
            total_ms=round((time.perf_counter() - started) * 1000, 3),
        )

    def _persist(self, result: AnswerResult, principal: str | None = None) -> None:
        # M0 引用保真：所有终态结果（answer/stream 的每一条路径）都汇入
        # _persist，在这里统一做确定性标注检查——warning-first，不阻断。
        try:
            from application.evidence_fidelity import check_citation_fidelity, fidelity_warning

            report = check_citation_fidelity(result.answer, [item.final_rank for item in result.citations])
            result.citation_fidelity = report.ok if report.applicable else None
            # P0：把「正文实际标注引用的证据」独立记下来。``citations`` 是候选证据，
            # 不能代表模型真的用了它们；引用正确性等评测必须看这个子集。
            referenced_ranks = set(report.referenced)
            result.cited_citation_ids = [
                item.citation_id for item in result.citations if item.final_rank in referenced_ranks
            ]
            if not report.ok and result.retrieval_trace is not None:
                warning = fidelity_warning(report)
                if warning and warning not in result.retrieval_trace.warnings:
                    result.retrieval_trace.warnings.append(warning)
        except Exception:
            # 保真检查是纯函数，理论上不会失败；万一失败绝不影响应答主路径。
            logger.exception("citation_fidelity_check_failed", extra={"request_id": result.request_id})
        # 持久化失败不应让已经算出的答案/引用在客户端面前炸掉：
        # 记录错误并继续（query_logs 仅用于审计与回归，丢失一条可接受）。
        try:
            self._persist_or_raise(result, principal)
        except Exception:
            logger.exception(
                "query_log_persist_failed",
                extra={"request_id": result.request_id, "result_state": result.result_state.value},
            )
            return
        # 阶段B：提问成功落库后触发概念挖掘计数（fire-and-forget，绝不影响应答路径）。
        if self.on_question_logged is not None:
            try:
                self.on_question_logged()
            except Exception:
                logger.exception("concept_mine_trigger_failed", extra={"request_id": result.request_id})

    def _persist_or_raise(self, result: AnswerResult, principal: str | None = None) -> None:
        question = result.question if self.privacy_log_questions else None
        self.database.execute(
            """INSERT INTO query_logs (
                request_id,question,question_hash,answer,result_state,requested_strategy,actual_strategy,
                trace_json,citations_json,timing_json,usage_json,created_at,index_version,prompt_version,
                requested_provider,actual_provider,query_date,category_filter_json,principal_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            # 盐更名（expense-rag-salt → mindgraph-question-salt）：历史 question_hash
            # 失效可接受，该字段仅用于同题去重，不承载跨版本可追溯承诺。
            (result.request_id, question, hashlib.sha256((result.question + "mindgraph-question-salt").encode()).hexdigest(), result.answer,
             result.result_state.value, result.requested_strategy, result.actual_strategy,
             dumps(result.retrieval_trace.model_dump(mode="json") if result.retrieval_trace else {}),
             dumps([item.model_dump(mode="json") for item in result.citations]),
             dumps(result.timing.model_dump(mode="json")), dumps(result.usage.model_dump(mode="json")),
             result.created_at.isoformat(), result.index_version, result.prompt_version,
             result.requested_provider, result.actual_provider,
             result.retrieval_trace.applied_filters.get("query_date") if result.retrieval_trace else None,
             dumps(result.retrieval_trace.applied_filters.get("knowledge_categories", []) if result.retrieval_trace else []),
             principal or "anonymous"),
        )

    def answer(self, request: ChatRequest, access_scope: dict | None = None) -> AnswerResult:
        started = time.perf_counter()
        principal = (access_scope or {}).get("user") if access_scope else None
        request_id = str(uuid.uuid4())
        provider = self._provider(request.chat_provider, request.chat_model)
        if self._is_out_of_scope(request.question):
            result = AnswerResult(
                request_id=request_id, question=request.question, answer=REFUSAL, result_state=ResultState.out_of_scope,
                timing=self._timing(None, started, None, None), requested_strategy=request.retrieval_strategy,
                actual_strategy="scope_check", model=provider.model_name, requested_provider=request.chat_provider or provider.provider_name,
                actual_provider=provider.provider_name,
            )
            self._persist(result, principal)
            return result
        try:
            decision, routing_ms = self._route(request)
            trace = self._retrieve(request, decision, routing_ms, access_scope=access_scope)
        except Exception as exc:
            raise RetrievalUnavailableError("Retrieval is unavailable") from exc
        citations = self._citations(trace)
        trace_model = self._trace_model(trace) if request.include_retrieval_trace else None
        conflicts = self._policy_conflicts(citations, request, access_scope=access_scope)
        if conflicts:
            conflict_trace = trace_model or self._trace_model(trace)
            conflict_trace.policy_conflicts = conflicts
            result = AnswerResult(
                request_id=request_id, question=request.question, answer=CONFLICTING,
                result_state=ResultState.conflicting_evidence, citations=citations, retrieval_trace=conflict_trace,
                timing=self._timing(trace, started, None, None), requested_strategy=request.retrieval_strategy,
                actual_strategy=trace.actual_strategy, degraded=trace.degraded,
                degradation_reason=trace.degradation_reason, model=provider.model_name,
                requested_provider=request.chat_provider or provider.provider_name,
                actual_provider=provider.provider_name, index_version=trace.index_version,
            )
            self._persist(result, principal)
            return result
        if not citations:
            denied = _acl_filtered_everything(trace)
            result = AnswerResult(
                request_id=request_id, question=request.question,
                answer=PERMISSION_DENIED if denied else INSUFFICIENT,
                result_state=ResultState.permission_denied if denied else ResultState.insufficient_evidence,
                citations=[], retrieval_trace=trace_model,
                timing=self._timing(trace, started, None, None), requested_strategy=request.retrieval_strategy,
                actual_strategy=trace.actual_strategy, degraded=trace.degraded,
                degradation_reason="acl_filtered_all_candidates" if denied else trace.degradation_reason,
                model=provider.model_name,
                requested_provider=request.chat_provider or provider.provider_name, actual_provider=provider.provider_name,
                index_version=trace.index_version,
            )
            self._persist(result, principal)
            return result
        if not provider.available:
            answer = "已找到相关制度证据，但生成模型未配置。请直接查看下方引用。"
            state, usage, degradation = ResultState.model_unavailable, UsageMetrics(), "provider_not_configured"
            generation_ms = None
        else:
            generation_start = time.perf_counter()
            try:
                answer, raw_usage = provider.complete(self._messages(request.question, citations, trace.graph_links if trace else None))
                usage, state, degradation = UsageMetrics(**raw_usage), ResultState.answered, trace.degradation_reason
            except Exception as exc:
                answer = "生成模型暂时不可用。已返回检索到的制度证据，请以引用原文为准。"
                usage, state, degradation = UsageMetrics(), ResultState.model_unavailable, getattr(exc, "code", "provider_error")
            generation_ms = round((time.perf_counter() - generation_start) * 1000, 3)
        result = AnswerResult(
            request_id=request_id, question=request.question, answer=answer, result_state=state,
            citations=citations, retrieval_trace=trace_model, usage=usage,
            timing=self._timing(trace, started, generation_ms, None), requested_strategy=request.retrieval_strategy,
            actual_strategy=trace.actual_strategy, degraded=trace.degraded or degradation is not None,
            degradation_reason=degradation, model=provider.model_name,
            requested_provider=request.chat_provider or provider.provider_name, actual_provider=provider.provider_name,
            index_version=trace.index_version,
        )
        self._persist(result, principal)
        logger.info("chat_completed", extra={"request_id": result.request_id, "requested_strategy": result.requested_strategy, "actual_strategy": result.actual_strategy, "result_state": result.result_state.value, "degraded": result.degraded, "total_ms": result.timing.total_ms, "usage_source": result.usage.usage_source.value})
        return result

    def stream(self, request: ChatRequest, access_scope: dict | None = None) -> Iterable[dict[str, Any]]:
        started = time.perf_counter()
        principal = (access_scope or {}).get("user") if access_scope else None
        request_id = str(uuid.uuid4())
        provider = self._provider(request.chat_provider, request.chat_model)
        timestamp = lambda: datetime.now(UTC).isoformat()
        event = lambda name, data: {"request_id": request_id, "event": name, "timestamp": timestamp(), "data": data}
        yield event("request_started", {"strategy": request.retrieval_strategy})
        out_of_scope = self._is_out_of_scope(request.question)
        yield event("scope_check_completed", {"out_of_scope": out_of_scope})
        if out_of_scope:
            yield event("answer_delta", {"text": REFUSAL, "stream_mode": "deterministic"})
            result = AnswerResult(request_id=request_id, question=request.question, answer=REFUSAL,
                result_state=ResultState.out_of_scope, timing=self._timing(None, started, None, 0.0),
                requested_strategy=request.retrieval_strategy, actual_strategy="scope_check", model=provider.model_name,
                requested_provider=request.chat_provider or provider.provider_name, actual_provider=provider.provider_name)
            self._persist(result, principal)
            yield event("citations", {"citations": []})
            yield event("usage", result.usage.model_dump(mode="json"))
            yield event("completed", result.model_dump(mode="json"))
            return
        try:
            decision, routing_ms = self._route(request)
        except Exception as exc:
            logger.exception("mindgraph_routing_failed", extra={"request_id": request_id})
            yield event("error", error_event_data(
                "retrieval_unavailable", "检索服务暂不可用，请稍后重试。",
                detail=f"{type(exc).__name__}: {exc}",
            ))
            return
        yield event("retrieval_routed", {**decision.to_dict(), "routing_ms": routing_ms})
        yield event("retrieval_started", {"strategy": decision.selected_strategy})
        try:
            trace = self._retrieve(request, decision, routing_ms, access_scope=access_scope)
        except Exception as exc:
            logger.exception("mindgraph_retrieval_unavailable", extra={"request_id": request_id})
            yield event("error", error_event_data(
                "retrieval_unavailable", "检索服务暂不可用，请稍后重试。",
                detail=f"{type(exc).__name__}: {exc}",
            ))
            return
        yield event("retrieval_completed", {
            "actual_strategy": trace.actual_strategy,
            "candidate_counts": trace.candidate_counts,
            "route_decision": trace.route_decision,
        })
        if decision.selected_strategy == "hybrid_rerank":
            yield event("rerank_completed", {"degraded": trace.degraded})
        if trace.degraded:
            yield event("degraded", {"reason": trace.degradation_reason, "actual_strategy": trace.actual_strategy})
        citations = self._citations(trace)
        conflicts = self._policy_conflicts(citations, request, access_scope=access_scope)
        if conflicts:
            yield event("policy_conflict_detected", {"conflicts": conflicts})
            yield event("answer_delta", {"text": CONFLICTING, "stream_mode": "deterministic"})
            conflict_trace = self._trace_model(trace)
            conflict_trace.policy_conflicts = conflicts
            result = AnswerResult(request_id=request_id, question=request.question, answer=CONFLICTING,
                result_state=ResultState.conflicting_evidence, citations=citations, retrieval_trace=conflict_trace,
                timing=self._timing(trace, started, None, 0.0), requested_strategy=request.retrieval_strategy,
                actual_strategy=trace.actual_strategy, degraded=trace.degraded, degradation_reason=trace.degradation_reason,
                model=provider.model_name, requested_provider=request.chat_provider or provider.provider_name,
                actual_provider=provider.provider_name, index_version=trace.index_version)
        elif not citations:
            denied = _acl_filtered_everything(trace)
            no_evidence_text = PERMISSION_DENIED if denied else INSUFFICIENT
            yield event("answer_delta", {"text": no_evidence_text, "stream_mode": "deterministic"})
            result = AnswerResult(request_id=request_id, question=request.question, answer=no_evidence_text,
                result_state=ResultState.permission_denied if denied else ResultState.insufficient_evidence,
                citations=[], retrieval_trace=self._trace_model(trace),
                timing=self._timing(trace, started, None, 0.0), requested_strategy=request.retrieval_strategy,
                actual_strategy=trace.actual_strategy, degraded=trace.degraded,
                degradation_reason="acl_filtered_all_candidates" if denied else trace.degradation_reason,
                model=provider.model_name, requested_provider=request.chat_provider or provider.provider_name,
                actual_provider=provider.provider_name, index_version=trace.index_version)
        elif not provider.available:
            yield event("degraded", {"reason": "provider_not_configured", "actual_strategy": trace.actual_strategy})
            text = "已找到制度证据，但生成模型未配置。"
            yield event("answer_delta", {"text": text, "stream_mode": "deterministic"})
            result = AnswerResult(request_id=request_id, question=request.question, answer=text,
                result_state=ResultState.model_unavailable, citations=citations, retrieval_trace=self._trace_model(trace),
                timing=self._timing(trace, started, None, 0.0), requested_strategy=request.retrieval_strategy,
                actual_strategy=trace.actual_strategy, degraded=True, degradation_reason="provider_not_configured", model=provider.model_name,
                requested_provider=request.chat_provider or provider.provider_name, actual_provider=provider.provider_name,
                index_version=trace.index_version)
        else:
            yield event("generation_started", {"stream_mode": "provider_native"})
            text_parts, usage, first_delta = [], UsageMetrics(), None
            generation_start = time.perf_counter()
            try:
                for item in provider.stream(self._messages(request.question, citations, trace.graph_links if trace else None)):
                    if item.get("delta"):
                        if first_delta is None:
                            first_delta = (time.perf_counter() - started) * 1000
                        text_parts.append(item["delta"])
                        yield event("answer_delta", {"text": item["delta"], "stream_mode": "provider_native"})
                    if item.get("usage"):
                        usage = UsageMetrics(**item["usage"])
                result = AnswerResult(request_id=request_id, question=request.question, answer="".join(text_parts),
                    result_state=ResultState.answered, citations=citations, retrieval_trace=self._trace_model(trace), usage=usage,
                    timing=self._timing(trace, started, (time.perf_counter()-generation_start)*1000, first_delta),
                    requested_strategy=request.retrieval_strategy, actual_strategy=trace.actual_strategy,
                    degraded=trace.degraded, degradation_reason=trace.degradation_reason, model=provider.model_name,
                    requested_provider=request.chat_provider or provider.provider_name, actual_provider=provider.provider_name,
                    index_version=trace.index_version)
            except Exception as exc:
                reason = getattr(exc, "code", "provider_error")
                yield event("degraded", {"reason": reason, "actual_strategy": trace.actual_strategy})
                fallback = "生成模型暂时不可用，请查看引用原文。"
                yield event("answer_delta", {"text": fallback, "stream_mode": "deterministic_fallback"})
                result = AnswerResult(request_id=request_id, question=request.question, answer=fallback,
                    result_state=ResultState.model_unavailable, citations=citations, retrieval_trace=self._trace_model(trace),
                    timing=self._timing(trace, started, None, None), requested_strategy=request.retrieval_strategy,
                    actual_strategy=trace.actual_strategy, degraded=True, degradation_reason=reason, model=provider.model_name,
                    requested_provider=request.chat_provider or provider.provider_name, actual_provider=provider.provider_name,
                    index_version=trace.index_version)
        self._persist(result, principal)
        yield event("citations", {"citations": [item.model_dump(mode="json") for item in citations]})
        yield event("usage", result.usage.model_dump(mode="json"))
        yield event("completed", result.model_dump(mode="json"))
