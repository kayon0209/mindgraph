from __future__ import annotations

import hashlib
import inspect
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from domain.errors import ProviderUnavailableError, RetrievalUnavailableError
from domain.models import (
    AnswerResult, ChatRequest, Citation, ResultState, RetrievalTraceModel,
    TimingMetrics, UsageMetrics,
)
from infrastructure.database import ProductDatabase, dumps


OUT_OF_SCOPE = ("工资", "薪资", "年终奖", "股票", "请假", "年假", "辞职", "离职", "wifi", "食堂", "系统提示词", "ignore previous", "system prompt")
REFUSAL = "抱歉，我只能回答公司报销相关问题。"
INSUFFICIENT = "未在制度文件中找到足够依据。建议联系 HR/财务确认。"
logger = logging.getLogger("mindgraph.chat")

DEFAULT_SYSTEM_PROMPT = "你是企业报销政策助手。只能依据给定制度证据回答；不得编造。先给结论，再给简要依据，并使用 [citation-N] 标注引用。"


class ChatService:
    def __init__(self, database: ProductDatabase, pipeline_factory, provider, privacy_log_questions: bool = True, system_prompt: str | None = None) -> None:
        self.database = database
        self.pipeline_factory = pipeline_factory
        self.provider = provider
        self.privacy_log_questions = privacy_log_questions
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT

    def _provider(self, name: str | None = None, model: str | None = None):
        return self.provider.get(name, model) if hasattr(self.provider, "get") else self.provider

    def _retrieve(self, request: ChatRequest):
        pipeline = self.pipeline_factory(request.final_top_k)
        parameters = inspect.signature(pipeline.retrieve).parameters
        graph_enabled = getattr(request, "graph_enabled", True)
        if "graph_enabled" not in parameters:
            # 普通检索管线不支持图谱扩展，忽略该参数
            if "query_date" not in parameters:
                return pipeline.retrieve(request.question, request.retrieval_strategy)
            return pipeline.retrieve(
                request.question, request.retrieval_strategy, request.query_date,
                request.knowledge_categories, request.include_historical,
            )
        if "query_date" not in parameters:
            return pipeline.retrieve(request.question, request.retrieval_strategy, graph_enabled=graph_enabled)
        return pipeline.retrieve(
            request.question, request.retrieval_strategy, request.query_date,
            request.knowledge_categories, request.include_historical, graph_enabled=graph_enabled,
        )

    @staticmethod
    def _is_out_of_scope(question: str) -> bool:
        lowered = question.lower()
        return any(term in lowered for term in OUT_OF_SCOPE)

    @staticmethod
    def _trace_model(trace) -> RetrievalTraceModel:
        payload = trace.to_dict()
        return RetrievalTraceModel(
            requested_strategy=payload["requested_strategy"], actual_strategy=payload["actual_strategy"],
            candidate_counts=payload["candidate_counts"], dense_results=payload["dense_results"],
            sparse_results=payload["sparse_results"], fusion_results=payload["fused_results"],
            reranked_results=payload["reranked_results"], final_chunks=payload["final_selected_chunks"],
            stage_latency_ms=payload["latency_ms"], degraded=payload["degraded"],
            degradation_reason=payload["degradation_reason"],
            index_version=payload.get("index_version"), applied_filters=payload.get("applied_filters", {}),
            warnings=payload.get("warnings", []),
            graph_enabled=getattr(trace, "graph_enabled", False),
            graph_links=getattr(trace, "graph_links", []),
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
                authority_adjustment=candidate.authority_adjustment,
            ))
        return citations

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

    def _persist(self, result: AnswerResult) -> None:
        question = result.question if self.privacy_log_questions else None
        self.database.execute(
            """INSERT INTO query_logs (
                request_id,question,question_hash,answer,result_state,requested_strategy,actual_strategy,
                trace_json,citations_json,timing_json,usage_json,created_at,index_version,prompt_version,
                requested_provider,actual_provider,query_date,category_filter_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (result.request_id, question, hashlib.sha256((result.question + "expense-rag-salt").encode()).hexdigest(), result.answer,
             result.result_state.value, result.requested_strategy, result.actual_strategy,
             dumps(result.retrieval_trace.model_dump(mode="json") if result.retrieval_trace else {}),
             dumps([item.model_dump(mode="json") for item in result.citations]),
             dumps(result.timing.model_dump(mode="json")), dumps(result.usage.model_dump(mode="json")),
             result.created_at.isoformat(), result.index_version, result.prompt_version,
             result.requested_provider, result.actual_provider,
             result.retrieval_trace.applied_filters.get("query_date") if result.retrieval_trace else None,
             dumps(result.retrieval_trace.applied_filters.get("knowledge_categories", []) if result.retrieval_trace else [])),
        )

    def answer(self, request: ChatRequest) -> AnswerResult:
        started = time.perf_counter()
        request_id = str(uuid.uuid4())
        provider = self._provider(request.chat_provider, request.chat_model)
        if self._is_out_of_scope(request.question):
            result = AnswerResult(
                request_id=request_id, question=request.question, answer=REFUSAL, result_state=ResultState.out_of_scope,
                timing=self._timing(None, started, None, None), requested_strategy=request.retrieval_strategy,
                actual_strategy="scope_check", model=provider.model_name, requested_provider=request.chat_provider or provider.provider_name,
                actual_provider=provider.provider_name,
            )
            self._persist(result)
            return result
        try:
            trace = self._retrieve(request)
        except Exception as exc:
            raise RetrievalUnavailableError("Retrieval is unavailable") from exc
        citations = self._citations(trace)
        trace_model = self._trace_model(trace) if request.include_retrieval_trace else None
        if not citations:
            result = AnswerResult(
                request_id=request_id, question=request.question, answer=INSUFFICIENT,
                result_state=ResultState.insufficient_evidence, citations=[], retrieval_trace=trace_model,
                timing=self._timing(trace, started, None, None), requested_strategy=request.retrieval_strategy,
                actual_strategy=trace.actual_strategy, degraded=trace.degraded,
                degradation_reason=trace.degradation_reason, model=provider.model_name,
                requested_provider=request.chat_provider or provider.provider_name, actual_provider=provider.provider_name,
                index_version=trace.index_version,
            )
            self._persist(result)
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
        self._persist(result)
        logger.info("chat_completed", extra={"request_id": result.request_id, "requested_strategy": result.requested_strategy, "actual_strategy": result.actual_strategy, "result_state": result.result_state.value, "degraded": result.degraded, "total_ms": result.timing.total_ms, "usage_source": result.usage.usage_source.value})
        return result

    def stream(self, request: ChatRequest) -> Iterable[dict[str, Any]]:
        started = time.perf_counter()
        request_id = str(uuid.uuid4())
        provider = self._provider(request.chat_provider, request.chat_model)
        timestamp = lambda: datetime.now(timezone.utc).isoformat()
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
            self._persist(result)
            yield event("citations", {"citations": []})
            yield event("usage", result.usage.model_dump(mode="json"))
            yield event("completed", result.model_dump(mode="json"))
            return
        yield event("retrieval_started", {})
        try:
            trace = self._retrieve(request)
        except Exception:
            yield event("error", {"code": "retrieval_unavailable", "message": "Retrieval is unavailable"})
            return
        yield event("retrieval_completed", {"actual_strategy": trace.actual_strategy, "candidate_counts": trace.candidate_counts})
        if request.retrieval_strategy == "hybrid_rerank":
            yield event("rerank_completed", {"degraded": trace.degraded})
        if trace.degraded:
            yield event("degraded", {"reason": trace.degradation_reason, "actual_strategy": trace.actual_strategy})
        citations = self._citations(trace)
        if not citations:
            yield event("answer_delta", {"text": INSUFFICIENT, "stream_mode": "deterministic"})
            result = AnswerResult(request_id=request_id, question=request.question, answer=INSUFFICIENT,
                result_state=ResultState.insufficient_evidence, citations=[], retrieval_trace=self._trace_model(trace),
                timing=self._timing(trace, started, None, 0.0), requested_strategy=request.retrieval_strategy,
                actual_strategy=trace.actual_strategy, degraded=trace.degraded, degradation_reason=trace.degradation_reason,
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
        self._persist(result)
        yield event("citations", {"citations": [item.model_dump(mode="json") for item in citations]})
        yield event("usage", result.usage.model_dump(mode="json"))
        yield event("completed", result.model_dump(mode="json"))
