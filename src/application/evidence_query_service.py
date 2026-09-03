"""EvidenceQueryService：证据查询的统一应用服务（M1，实施方案 §3.1）。

从 ChatService 抽取 route→retrieve→citation→conflict 共享段，产出稳定的
EvidenceBundle v1（见 domain.evidence）。此后 ChatService（REST/SSE adapter）、
MCP 检索工具与后续 AgentService 均复用本服务，消除“Chat 一套、MCP 一套”的
双实现漂移。

边界（ADR-003）：
- 本服务不负责 LLM 生成（provider 调用留在 ChatService/AgentService）；
- 本服务不负责审计落库（通道各自 record_access_audit，保持通道可对账）；
- 不向调用方返回 access_scope 或完整 ACL 规则（避免权限侧信道）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import time
import uuid

from application.adaptive_retrieval_router import AdaptiveRetrievalRouter
from application.chat_service import ChatService
from application.policy_conflict_service import PolicyConflictService
from domain.evidence import (
    EvidenceBundle,
    EvidenceItem,
    EvidenceResultState,
    EvidenceRouteInfo,
    PolicyConflictEntry,
)
from domain.models import ChatRequest, Citation

logger = logging.getLogger("mindgraph.evidence")

# MCP 通道证据摘要截断（对齐 mcp_search 既有行为）
MCP_EXCERPT_LIMIT = 400
# Chat 通道证据摘要截断（对齐 ChatService._citations 既有行为）
CHAT_EXCERPT_LIMIT = 500


@dataclass
class EvidenceQueryResult:
    """共享检索段的完整产物：Bundle（对外契约）+ trace/citations（Chat 通道内部消费）。

    ChatService 需要原始 trace（SSE 事件）与 Citation 模型（AnswerResult 字段），
    因此两者与 Bundle 并行返回；Bundle 是唯一对外稳定契约。
    """

    bundle: EvidenceBundle
    trace: object | None = None
    citations: list[Citation] = field(default_factory=list)
    route_decision: object | None = None
    routing_ms: float = 0.0

    @property
    def result_state(self) -> EvidenceResultState:
        return self.bundle.result_state


def _result_state_for_trace(trace) -> EvidenceResultState:
    """把 ChatService 检索后的分支判定复述为 EvidenceResultState。

    与 chat_service 的 answer/stream 分支顺序一致：ACL 裁剪优先于“证据不足”
    （零引用时看 warnings 里的 access_denied_chunks_filtered）。
    """
    if "access_denied_chunks_filtered" in getattr(trace, "warnings", []):
        return EvidenceResultState.permission_denied
    return EvidenceResultState.insufficient_evidence


class EvidenceQueryService:
    def __init__(self, chat_service: ChatService) -> None:
        self.chat_service = chat_service
        self.database = chat_service.database
        self.policy_conflict_service = PolicyConflictService(self.database)

    def query(
        self,
        request: ChatRequest,
        *,
        access_scope: dict | None = None,
        excerpt_limit: int | None = None,
    ) -> EvidenceQueryResult:
        """执行共享证据查询段（route→retrieve→citations→conflicts）。

        不生成答案、不写审计、不落 query_logs——这些职责在通道层
        （ChatService/Assist/MCP 各自完成并保持可对账）。
        """
        started = time.perf_counter()
        decision, routing_ms = self.chat_service._route(request)
        trace = self.chat_service._retrieve(request, decision, routing_ms, access_scope=access_scope)
        citations = self.chat_service._citations(trace)
        conflicts = self.policy_conflict_service.find_for_policy_keys(
            {item.policy_key for item in citations if item.policy_key},
            as_of=request.query_date,
            include_historical=request.include_historical,
            access_scope=access_scope,
        )
        return EvidenceQueryResult(
            bundle=self._bundle(
                request, decision, trace, citations, conflicts,
                excerpt_limit=excerpt_limit,
                elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
            ),
            trace=trace,
            citations=citations,
            route_decision=decision,
            routing_ms=routing_ms,
        )

    def rebundle_with_conflicts(self, previous: EvidenceQueryResult, conflicts: list[dict]) -> EvidenceQueryResult:
        """复用既有检索产物，仅以新的冲突结果重建 bundle（增量步骤用，
        不重跑 route→retrieve——嵌入检索是最贵操作，见 AgentService 注释）。"""
        if previous.trace is None or previous.route_decision is None:
            # 无检索产物可复用（不应发生）：退回全链查询保正确性
            return previous
        return EvidenceQueryResult(
            bundle=self._bundle(
                previous_request := self._request_of(previous),
                previous.route_decision,
                previous.trace,
                previous.citations,
                conflicts,
                excerpt_limit=None,
                elapsed_ms=0.0,
            ),
            trace=previous.trace,
            citations=previous.citations,
            route_decision=previous.route_decision,
            routing_ms=previous.routing_ms,
        )

    @staticmethod
    def _request_of(previous: EvidenceQueryResult) -> ChatRequest:
        """从 bundle 反推查询语义所需的最小请求形状（仅 query/as_of 用于重建）。"""
        return ChatRequest(
            question=previous.bundle.query,
            retrieval_strategy="auto",
            query_date=previous.bundle.as_of.isoformat() if hasattr(previous.bundle.as_of, "isoformat") else (previous.bundle.as_of if isinstance(previous.bundle.as_of, str) else None),
            include_historical=False,
        )

    def _bundle(
        self,
        request: ChatRequest,
        decision,
        trace,
        citations: list[Citation],
        conflicts: list[dict],
        *,
        excerpt_limit: int | None,
        elapsed_ms: float,
    ) -> EvidenceBundle:
        if conflicts:
            state = EvidenceResultState.conflicting_evidence
        elif not citations:
            state = _result_state_for_trace(trace)
        else:
            state = EvidenceResultState.evidence_found
        limit = excerpt_limit or CHAT_EXCERPT_LIMIT
        return EvidenceBundle(
            trace_id=str(uuid.uuid4()),
            query=request.question,
            as_of=request.query_date,
            result_state=state,
            route=EvidenceRouteInfo(
                name=decision.route,
                reason_codes=[code.value for code in decision.reasons],
                selected_strategy=decision.selected_strategy,
            ),
            evidence=[
                EvidenceItem(
                    citation_id=item.citation_id,
                    document_id=item.document_id,
                    document_name=item.document_name,
                    chunk_id=item.chunk_id,
                    section_path=item.section_path,
                    excerpt=(item.excerpt or "")[:limit] if item.excerpt else None,
                    final_rank=item.final_rank,
                    document_version=item.document_version,
                    effective_from=item.effective_from,
                    effective_to=item.effective_to,
                    policy_status=item.policy_status,
                    policy_key=item.policy_key,
                    owner=item.owner,
                )
                for item in citations
            ],
            conflicts=[PolicyConflictEntry(policy_key=c.get("policy_key"), versions=c.get("versions", [])) for c in conflicts],
            warnings=[*getattr(trace, "warnings", [])],
            index_version=getattr(trace, "index_version", None),
            retryable=state is EvidenceResultState.retrieval_unavailable,
            next_action=None,  # 由 resolved_next_action() 确定性推导；显式置空避免双源
        )
