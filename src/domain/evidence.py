"""EvidenceBundle v1：对外证据查询的稳定契约（Agentic Evidence Layer，ADR-003）。

设计（实施方案 §3.1）：
- ChatService、MCP 与后续 AgentService 都通过同一应用服务产出本信封，
  避免“Chat 一套检索逻辑、MCP 另一套”的漂移；
- 所有判定字段机器可枚举（result_state / next_action / route），agent 只读
  这些字段即可决定下一步，不需要解析自然语言；
- 不携带完整 ACL 规则，不泄漏不可见资源的数量或标识（避免权限侧信道）。

契约冻结规则：字段与取值只做加法（optional 新字段、枚举新值）；删除/重命名
任何既有字段或取值属于破坏性变更，必须先升 schema_version 并出迁移说明。
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EvidenceResultState(str, Enum):
    """证据查询的可判定终态（与 ResultState 的检索子集对齐，另加 waiting_for_input）。

    - evidence_found：检索到当前主体可见的证据；
    - insufficient_evidence：可见范围内无足够证据；
    - permission_denied：证据被 ACL 裁剪导致不可见（零引用且 acl_filtered_all_candidates）；
    - conflicting_evidence：同一 policy_key 在查询日期存在多个有效版本（fail-closed，不生成）；
    - out_of_scope：问题超出系统知识域（scope 检查，不检索）；
    - retrieval_unavailable：检索基础设施故障；
    - waiting_for_input：需要用户澄清后以新请求恢复（M2 澄清协议；本版本仅登记）。
    """

    evidence_found = "evidence_found"
    insufficient_evidence = "insufficient_evidence"
    permission_denied = "permission_denied"
    conflicting_evidence = "conflicting_evidence"
    out_of_scope = "out_of_scope"
    retrieval_unavailable = "retrieval_unavailable"
    waiting_for_input = "waiting_for_input"


class EvidenceNextAction(str, Enum):
    """基于 result_state 的可行动下一步（agent 侧的决策提示，非权限描述）。"""

    generate = "generate"
    ask_clarification = "ask_clarification"
    request_access = "request_access"
    human_review = "human_review"
    retry = "retry"
    stop = "stop"


# result_state → next_action 的确定性映射（新增终态必须补映射，测试会校验全覆盖）
RESULT_STATE_NEXT_ACTION: dict[EvidenceResultState, EvidenceNextAction] = {
    EvidenceResultState.evidence_found: EvidenceNextAction.generate,
    EvidenceResultState.insufficient_evidence: EvidenceNextAction.ask_clarification,
    EvidenceResultState.permission_denied: EvidenceNextAction.request_access,
    EvidenceResultState.conflicting_evidence: EvidenceNextAction.human_review,
    EvidenceResultState.out_of_scope: EvidenceNextAction.stop,
    EvidenceResultState.retrieval_unavailable: EvidenceNextAction.retry,
    EvidenceResultState.waiting_for_input: EvidenceNextAction.ask_clarification,
}


class EvidenceRouteInfo(BaseModel):
    """检索路由摘要：让调用方知道“为什么走这条路”，不暴露内部阈值。"""

    name: str = Field(description="路由名（AdaptiveRetrievalRouter.RouteName）")
    reason_codes: list[str] = Field(default_factory=list, description="路由原因码（RouteReasonCode 值）")
    selected_strategy: str | None = Field(default=None, description="实际检索策略")


class EvidenceItem(BaseModel):
    """单条证据（citation 的证据层视图；不携带 ACL 规则，只带治理元数据）。"""

    citation_id: str
    document_id: str
    document_name: str | None = None
    chunk_id: str | None = None
    section_path: str | None = None
    excerpt: str | None = Field(default=None, description="证据片段（按通道裁剪长度）")
    final_rank: int | None = None
    document_version: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    policy_status: str | None = None
    policy_key: str | None = None
    owner: str | None = None


class PolicyConflictEntry(BaseModel):
    """版本冲突条目（PolicyConflictService 输出的可序列化视图）。"""

    policy_key: str | None = None
    title: str | None = None
    versions: list[dict[str, Any]] = Field(default_factory=list)


class EvidenceBundle(BaseModel):
    """一次证据查询的完整可判定信封（v1）。

    生产方：EvidenceQueryService（M1 起，从 ChatService 抽取检索/冲突/引用段）。
    消费方：ChatService（旧 REST/SSE adapter 转换层）、MCP 工具、Assist、评测。
    """

    schema_version: str = "1.0"
    trace_id: str = Field(description="请求/追踪标识（复用 request_id 语义）")
    query: str
    as_of: date | None = Field(default=None, description="版本时效判定基准日（query_date）")
    result_state: EvidenceResultState
    route: EvidenceRouteInfo | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    conflicts: list[PolicyConflictEntry] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    index_version: str | None = None
    retryable: bool = False
    next_action: EvidenceNextAction | None = None
    generated_at: datetime | None = None

    def resolved_next_action(self) -> EvidenceNextAction:
        """确定性推导 next_action（构造后调用方可依赖，不需要自带逻辑）。"""
        return RESULT_STATE_NEXT_ACTION[self.result_state]
