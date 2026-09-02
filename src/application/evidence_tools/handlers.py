"""Evidence Tool Handlers 与注册（M1，实施方案 §5.2）。

P0 三个只读工具：
- mindgraph_get_policy_history：按 policy_key 返回当前主体可见的版本族；
  不可借此推断不可见版本的数量（只返回可见项，不返回 any-visible-hidden 计数）。
- mindgraph_concept_gaps：复用 QuestionConceptMiner 的聚合缺口视图；
  只返回主体口径的聚合信息（概念缺口本身不承载 note 级 ACL，但保持只读）。
- mindgraph_verify_citations：citation integrity 检查（marker 格式/越界/重复/
  未用引用）。文档与工具描述均明确：不验证语义支持（主张是否被证据支撑
  属于 answer evaluation 的 claim-support 指标）。

所有 handler 只经 EvidenceToolRegistry.call 进入（参数校验/ACL/审计在
registry 统一完成）；handler 内部不再重复 ACL 判定（可见性裁剪除外）。
"""

from __future__ import annotations

import json
from typing import Any

from application.access_control import note_acl_matches
from application.citation_integrity import CitationIntegrityValidator
from application.evidence_tools.contracts import ToolSpec
from application.evidence_tools.registry import EvidenceToolRegistry
from infrastructure.database import ProductDatabase

MAX_HISTORY_LIMIT = 50
MAX_GAPS_LIMIT = 50


def build_default_registry(database: ProductDatabase, *, question_miner=None) -> EvidenceToolRegistry:
    """构造 M1 默认只读工具集。question_miner 为容器内 QuestionConceptMiner。"""
    registry = EvidenceToolRegistry(database)
    registry.register(POLICY_HISTORY_SPEC, _handle_policy_history)
    registry.register(CONCEPT_GAPS_SPEC, _make_concept_gaps_handler(question_miner))
    registry.register(VERIFY_CITATIONS_SPEC, _handle_verify_citations)
    return registry


# ── mindgraph_get_policy_history ──

POLICY_HISTORY_SPEC = ToolSpec(
    name="mindgraph_get_policy_history",
    description=(
        "按 policy_key 返回当前主体可见的制度版本族（版本/生效期/状态/责任部门）。"
        "只包含调用主体有权访问的版本；本工具不生成答案。"
    ),
    mode="read",
    risk="low",
    allowed_in=frozenset({"external_mcp"}),
    timeout_seconds=5.0,
    input_schema={
        "type": "object",
        "properties": {
            "policy_key": {"type": "string", "minLength": 1, "maxLength": 200},
            "include_historical": {"type": "boolean", "default": False},
        },
        "required": ["policy_key"],
        "additionalProperties": False,
    },
)


def _handle_policy_history(
    principal: dict[str, Any],
    scope: dict[str, Any] | None,
    arguments: dict[str, Any],
    deadline: float | None,
) -> dict[str, Any]:
    database: ProductDatabase = _database()
    policy_key = arguments["policy_key"].strip()
    include_historical = bool(arguments.get("include_historical", False))
    statuses = ("active", "archived", "expired", "superseded") if include_historical else ("active",)
    placeholders = ",".join("?" for _ in statuses)
    rows = database.fetch_all(
        "SELECT note_id, title, vault_path, document_version, effective_from, effective_to, "
        f"policy_status, owner, workspace, department, acl_json, acl_public FROM notes WHERE policy_key=? AND policy_status IN ({placeholders}) "  # nosec B608 -- placeholders 仅由常量 '?' 拼接
        "ORDER BY effective_from, document_version, note_id",
        (policy_key, *statuses),
    )
    visible = [row for row in rows if note_acl_matches(row, scope)]
    # 只返回可见项；不返回“被隐藏的数量”，避免存在性侧信道
    return {
        "policy_key": policy_key,
        "versions": [
            {
                "note_id": row["note_id"],
                "title": row["title"],
                "vault_path": row["vault_path"],
                "version": row["document_version"],
                "effective_from": row["effective_from"],
                "effective_to": row["effective_to"],
                "policy_status": row["policy_status"],
                "owner": row["owner"],
            }
            for row in visible
        ],
    }


# ── mindgraph_concept_gaps ──

CONCEPT_GAPS_SPEC = ToolSpec(
    name="mindgraph_concept_gaps",
    description=(
        "返回高频但未收录的概念缺口（聚合视图，只读）。用于发现知识库应补充的制度主题；"
        "不返回提问原文，仅返回概念词与出现次数。"
    ),
    mode="read",
    risk="low",
    allowed_in=frozenset({"external_mcp"}),
    timeout_seconds=5.0,
    input_schema={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": MAX_GAPS_LIMIT},
        },
        "additionalProperties": False,
    },
)


def _make_concept_gaps_handler(question_miner):
    def _handle_concept_gaps(
        principal: dict[str, Any],
        scope: dict[str, Any] | None,
        arguments: dict[str, Any],
        deadline: float | None,
    ) -> dict[str, Any]:
        if question_miner is None:
            return {"gaps": [], "total": 0, "note": "concept mining not available in this deployment"}
        limit = min(max(int(arguments.get("limit", 10)), 1), MAX_GAPS_LIMIT)
        gaps = question_miner.top_gaps(limit=limit)
        return {"gaps": gaps, "total": question_miner.gap_total()}

    return _handle_concept_gaps


# ── mindgraph_verify_citations ──

VERIFY_CITATIONS_SPEC = ToolSpec(
    name="mindgraph_verify_citations",
    description=(
        "校验一段回答文本中的 [citation-N] 标注完整性：格式合法、未越界、无重复、"
        "引用全部被使用。这是确定性的标注完整性检查（citation integrity），"
        "不验证“结论是否被证据支持”（语义支持属于 answer evaluation）。"
    ),
    mode="read",
    risk="low",
    allowed_in=frozenset({"external_mcp"}),
    timeout_seconds=5.0,
    max_input_bytes=65536,
    # 回答正文属于用户内容，默认脱敏不进审计明文
    redact_fields=frozenset({"answer"}),
    input_schema={
        "type": "object",
        "properties": {
            "answer": {"type": "string", "minLength": 1, "maxLength": 50000},
            "citation_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "本次回答实际返回的引用 ID（形如 citation-N）",
            },
        },
        "required": ["answer", "citation_ids"],
        "additionalProperties": False,
    },
)


def _handle_verify_citations(
    principal: dict[str, Any],
    scope: dict[str, Any] | None,
    arguments: dict[str, Any],
    deadline: float | None,
) -> dict[str, Any]:
    answer = arguments["answer"]
    citation_ids = [item for item in arguments.get("citation_ids", []) if isinstance(item, str)]
    validator = CitationIntegrityValidator(citation_ids=citation_ids)
    report = validator.validate(answer)
    return {
        "passed": report.passed,
        "applicable": report.applicable,
        "checks": {
            "malformed_markers": report.malformed_markers,
            "unknown_markers": report.unknown_markers,
            "duplicate_markers": report.duplicate_markers,
            "unused_citations": report.unused_citations,
        },
        "scope_note": "citation integrity only; semantic claim-support is evaluated in answer evaluation",
    }


# handler 与数据库解耦：由 registry 构造时闭包注入；这里用模块级桥接保持简单
_DATABASE: ProductDatabase | None = None


def _database() -> ProductDatabase:
    assert _DATABASE is not None, "registry database not initialized"
    return _DATABASE


def set_handler_database(database: ProductDatabase) -> None:
    """注入 handler 使用的数据库（由装配层调用；避免全局单例猜测）。"""
    global _DATABASE
    _DATABASE = database


def _json_default(obj: Any) -> str:
    return json.dumps(obj, default=str)
