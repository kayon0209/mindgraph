"""M5-A 工具 C：mindgraph_propose_relation（高风险，最后一个受控写工具）。

方案 M5-A 高风险语义（逐条落地）：
- 独立 feature flag（AGENT_PROPOSE_RELATION_TOOL_ENABLED，默认关）；
- **两段确认模式**：action=preview 展示 source/target/evidence 的标题、
  类型、影响范围与可见性校验结果（不写任何数据）；用户逐次确认后
  action=submit 才写入。每次调用都是独立确认，不缓存"已确认"状态；
- **只创建 proposed**：status='proposed'，绝不自动 confirmed——confirmed
  必须走既有 HITL 审核流（关系审核页），进入检索扩展的唯一路径不变；
- **三端 ACL 可见性**：source/target/evidence 三者都必须对当前主体可见；
  不可见 → 统一 not found（不暴露存在性、不提示哪端不可见）；
- **幂等**：与既有抽取入口同一去重语义（pair 已存在于 note_relations
  任意状态/任一方向 → 不重复写入，返回 already_exists）；
- **relation_type 白名单**：ALLOWED_RELATION_TYPES 之外的值拒绝；
- 审计脱敏：evidence 说明文本不进明文。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from application.evidence_tools.contracts import ToolSpec
from application.evidence_tools.registry import ToolExecutionRejected
from infrastructure.database import ProductDatabase


PROPOSE_RELATION_SPEC = ToolSpec(
    name="mindgraph_propose_relation",
    description=(
        "提出两个笔记之间的关系候选（进入人工审核队列，绝不自动生效）。"
        "两步确认：先 action=preview 查看两端笔记标题、关系类型与影响范围，"
        "经用户逐次确认后再 action=submit 提交。只创建 proposed 状态候选；"
        "确认后才会进入图谱检索扩展。source、target、evidence 三者你都必须"
        "有权访问。同一对笔记不重复创建。"
    ),
    mode="write",
    risk="high",
    allowed_in=frozenset({"external_mcp"}),
    timeout_seconds=5.0,
    requires_approval=False,  # 高风险确认由 preview+submit 两段模式 + HITL 审核流承担
    redact_fields=frozenset({"evidence"}),
    input_schema={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["preview", "submit"]},
            "source_note_id": {"type": "string", "minLength": 1, "maxLength": 64},
            "target_note_id": {"type": "string", "minLength": 1, "maxLength": 64},
            "relation_type": {"type": "string", "enum": [
                "related_to", "references", "elaborates", "APPLIES_TO",
                "REQUIRES_APPROVAL", "HAS_LIMIT", "EXCEPTION_TO", "SUPERSEDES", "CONTRADICTS",
            ]},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "evidence_note_id": {"type": "string", "maxLength": 64},
            "evidence": {"type": "string", "maxLength": 2000, "description": "支撑该关系的说明（供审核者参考）"},
        },
        "required": ["action", "source_note_id", "target_note_id", "relation_type"],
        "additionalProperties": False,
    },
)


def make_propose_relation_handler():
    from application.access_control import note_acl_matches, build_access_scope
    from application.relation_extraction_service import ALLOWED_RELATION_TYPES

    def _handler(
        principal: dict[str, Any],
        scope: dict[str, Any] | None,
        arguments: dict[str, Any],
        deadline: float | None,
    ) -> dict[str, Any]:
        from infrastructure.settings import get_settings

        if not get_settings().AGENT_PROPOSE_RELATION_TOOL_ENABLED:
            raise ToolExecutionRejected("propose tool disabled (AGENT_PROPOSE_RELATION_TOOL_ENABLED=false)")
        if str(arguments.get("relation_type")) not in ALLOWED_RELATION_TYPES:
            raise ToolExecutionRejected("relation_type 不在白名单内")

        database: ProductDatabase = _database()
        source_id = str(arguments["source_note_id"]).strip()
        target_id = str(arguments["target_note_id"]).strip()
        if source_id == target_id:
            raise ToolExecutionRejected("source 与 target 不能是同一篇笔记")

        # 三端可见性：source/target/evidence 任一不可见 → 统一 not found
        # （不区分哪端、不暴露存在性）
        def _visible_note(note_id: str) -> dict[str, Any] | None:
            if not note_id:
                return None
            row = database.fetch_one(
                "SELECT note_id, title, vault_path, document_version, acl_json, acl_public, workspace, department"
                " FROM notes WHERE note_id=?",
                (note_id,),
            )
            if row is None or not note_acl_matches(row, scope):
                return None
            return row

        source = _visible_note(source_id)
        target = _visible_note(target_id)
        evidence_id = str(arguments.get("evidence_note_id") or "").strip() or None
        evidence_note = _visible_note(evidence_id) if evidence_id else None
        if source is None or target is None or (evidence_id is not None and evidence_note is None):
            raise ToolExecutionRejected("source/target/evidence 不存在或你无权访问（不区分具体哪一项）")

        relation_type = str(arguments["relation_type"])
        confidence = arguments.get("confidence", 0.6)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ToolExecutionRejected("confidence 必须是 0-1 的数值")
        evidence_text = str(arguments.get("evidence") or "").strip() or None

        # 幂等（与既有抽取入口同语义）：pair 已存在于任意状态/任一方向
        def _pair_exists() -> dict[str, Any] | None:
            return (
                database.fetch_one(
                    "SELECT relation_id, status FROM note_relations WHERE"
                    " ((source_note_id=? AND target_note_id=?) OR (source_note_id=? AND target_note_id=?))",
                    (source_id, target_id, target_id, source_id),
                )
            )

        existing = _pair_exists()
        action = arguments["action"]

        # ── preview：只读展示（高风险确认第一步；绝不写） ──
        if action == "preview":
            return {
                "preview": {
                    "source": {"note_id": source_id, "title": source["title"]},
                    "target": {"note_id": target_id, "title": target["title"]},
                    "evidence_note": {"note_id": evidence_id, "title": evidence_note["title"]} if evidence_note else None,
                    "relation_type": relation_type,
                    "confidence": float(confidence),
                    "visibility_check": "passed（三端均可见）",
                    "already_exists": bool(existing),
                    "existing_status": existing["status"] if existing else None,
                },
                "impact": "提交后创建 proposed 候选，进入关系审核队列；只有人工确认后才进入图谱检索扩展。",
                "note": "请向用户完整展示以上信息并逐次确认；确认后用 action=submit 提交（同一确认不缓存）。",
            }

        # ── submit ──
        if existing is not None:
            return {
                "status": "already_exists",
                "relation_id": existing["relation_id"],
                "existing_status": existing["status"],
                "note": "这对笔记已存在关系候选（任意方向/状态均去重）；本次未重复写入。",
            }

        import uuid

        relation_id = f"rel-{uuid.uuid4().hex[:16]}"
        now = datetime.now(UTC).isoformat()
        database.execute(
            "INSERT INTO note_relations (relation_id, source_note_id, target_note_id, relation_type, direction,"
            " status, evidence_chunk_id, confidence, model_version, prompt_version, proposed_at,"
            " evidence_span, evidence_section, source_document_version, effective_from, effective_to, extraction_method)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                relation_id, source_id, target_id, relation_type, "outgoing", "proposed",
                evidence_id, float(confidence), "mcp-proposal", "m5a-tool-c", now,
                evidence_text, None, None, None, None, "mcp_propose_relation",
            ),
        )
        return {
            "status": "proposed_created",
            "relation_id": relation_id,
            "note": "已创建 proposed 候选（绝不自动生效）；待人工在关系审核页确认。",
        }

    return _handler


def _database() -> ProductDatabase:
    from application.evidence_tools.handlers import _database as handlers_database

    return handlers_database()
