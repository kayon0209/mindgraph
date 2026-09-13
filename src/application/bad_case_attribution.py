"""PR-14 Bad-case 全链路归因与重复失败升级。

## 现场核对结论（改动面的依据）

归因数据**已经在库里**：``query_logs.trace_json`` 含 route/variants/各阶段
候选/索引版本/degraded（RetrievalTraceModel 全量落库），bad_cases 只消费了
``final_chunks``。本模块不建第二事实源——归因 = **读取面 JOIN**：

- :func:`attribute_bad_case` —— bad_case JOIN query_logs，输出可归因报告
  （≥90% 可归因验收项的数据基础；无 trace 的历史记录显式标
  ``attribution_available=False``，不假装可归因）；
- :func:`check_escalation` / :func:`record_escalation_if_needed` —— 用
  ``query_logs.question_hash``（带盐、已有列）跨 request_id 检测重复失败：
  同问 ≥2 次 not_helpful 或同问 ≥3 次问答 → ``escalation_suggested``。

## 升级信号（additive）

写 ``bad_case_escalations`` 新表（不动 bad_cases schema——request_id
UNIQUE 不允许补行），幂等键 = request_id + reason，重复检查只落一条。
``BAD_CASE_ESCALATION_ENABLED``（默认关）是回滚开关。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

from infrastructure.database import ProductDatabase, dumps, loads

logger = logging.getLogger("mindgraph.badcase")

REASON_REPEATED_NOT_HELPFUL = "repeated_not_helpful"
REASON_REPEATED_QUESTION = "repeated_question"
REASON_NONE = "no_escalation"

NOT_HELPFUL_THRESHOLD = 2   # 同问 not_helpful ≥2 次
QUESTION_LOG_THRESHOLD = 3  # 同问问答 ≥3 次


@dataclass(frozen=True)
class EscalationDecision:
    escalation_suggested: bool
    reason: str
    related_request_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "escalation_suggested": self.escalation_suggested,
            "reason": self.reason,
            "related_request_ids": self.related_request_ids,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


@dataclass(frozen=True)
class BadCaseAttribution:
    request_id: str
    route: str | None
    selected_strategy: str | None
    query_variants: list[str]
    candidate_counts: dict[str, Any]
    index_version: str | None
    prompt_version: str | None
    degraded: bool | None
    attribution_available: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "route": self.route,
            "selected_strategy": self.selected_strategy,
            "query_variants": self.query_variants,
            "candidate_counts": self.candidate_counts,
            "index_version": self.index_version,
            "prompt_version": self.prompt_version,
            "degraded": self.degraded,
            "attribution_available": self.attribution_available,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


def attribute_bad_case(database: ProductDatabase, request_id: str) -> BadCaseAttribution:
    """bad_case → query_logs.trace_json 的全链路归因（只读 JOIN，不写）。"""
    row = database.fetch_one(
        "SELECT q.trace_json, q.prompt_version, q.index_version FROM query_logs q"
        " WHERE q.request_id=?",
        (request_id,),
    )
    if row is None:
        return BadCaseAttribution(
            request_id=request_id, route=None, selected_strategy=None,
            query_variants=[], candidate_counts={}, index_version=None,
            prompt_version=None, degraded=None, attribution_available=False,
        )
    trace = loads(row["trace_json"], {}) or {}
    if not isinstance(trace, dict):
        trace = {}
    route_decision = trace.get("route_decision") or {}
    if not trace or not isinstance(route_decision, dict):
        return BadCaseAttribution(
            request_id=request_id, route=None, selected_strategy=None,
            query_variants=[], candidate_counts={}, index_version=None,
            prompt_version=row["prompt_version"], degraded=None,
            attribution_available=False,
        )
    return BadCaseAttribution(
        request_id=request_id,
        route=route_decision.get("route"),
        selected_strategy=route_decision.get("selected_strategy"),
        query_variants=list(trace.get("query_variants") or []),
        candidate_counts=dict(trace.get("candidate_counts") or {}),
        index_version=trace.get("index_version") or row["index_version"],
        prompt_version=row["prompt_version"],
        degraded=bool(trace.get("degraded")) if "degraded" in trace else None,
        attribution_available=True,
    )


def _escalation_flag() -> bool:
    import os

    return os.getenv("BAD_CASE_ESCALATION_ENABLED", "").lower() in {"1", "true", "yes"}


def check_escalation(database: ProductDatabase, request_id: str) -> EscalationDecision:
    """按 question_hash 检测重复失败（跨 request_id；flag 关闭恒不升级）。

    近似匹配的语义边界：question_hash 带盐哈希，同 hash = 字面相同问句。
    语义近似（"怎么报"vs"如何报销"）不做——那需要嵌入，属后续能力，
    不在确定性升级里假装。误聚类防线因此天然成立：不同问句不同 hash。
    """
    if not _escalation_flag():
        return EscalationDecision(False, REASON_NONE)
    row = database.fetch_one(
        "SELECT question_hash FROM query_logs WHERE request_id=?", (request_id,),
    )
    if row is None or not row["question_hash"]:
        return EscalationDecision(False, REASON_NONE)
    question_hash = row["question_hash"]

    related = [
        item["request_id"]
        for item in database.fetch_all(
            "SELECT request_id FROM query_logs WHERE question_hash=? ORDER BY created_at",
            (question_hash,),
        )
    ]
    if len(related) >= QUESTION_LOG_THRESHOLD:
        return EscalationDecision(True, REASON_REPEATED_QUESTION, related)

    not_helpful_row = database.fetch_one(
        "SELECT COUNT(*) AS c FROM bad_cases b JOIN query_logs q ON b.request_id=q.request_id"
        " WHERE q.question_hash=?",
        (question_hash,),
    )
    not_helpful_count = int(not_helpful_row["c"]) if not_helpful_row is not None else 0
    if not_helpful_count >= NOT_HELPFUL_THRESHOLD:
        return EscalationDecision(True, REASON_REPEATED_NOT_HELPFUL, related)
    return EscalationDecision(False, REASON_NONE, related)


def record_escalation_if_needed(database: ProductDatabase, request_id: str) -> EscalationDecision:
    """检查并落账升级信号（幂等：request_id+reason 唯一键，重复只落一条）。"""
    decision = check_escalation(database, request_id)
    if not decision.escalation_suggested:
        return decision
    existing = database.fetch_one(
        "SELECT 1 FROM bad_case_escalations WHERE request_id=? AND reason=?",
        (request_id, decision.reason),
    )
    if existing is None:
        from datetime import UTC, datetime

        database.execute(
            "INSERT OR IGNORE INTO bad_case_escalations (request_id, reason, related_request_ids_json,"
            " created_at) VALUES (?,?,?,?)",
            (request_id, decision.reason, dumps(decision.related_request_ids),
             datetime.now(UTC).isoformat()),
        )
    return decision


__all__ = [
    "BadCaseAttribution", "EscalationDecision",
    "attribute_bad_case", "check_escalation", "record_escalation_if_needed",
    "REASON_REPEATED_NOT_HELPFUL", "REASON_REPEATED_QUESTION", "REASON_NONE",
]
