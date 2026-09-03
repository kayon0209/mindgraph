"""M5-A 工具 B：mindgraph_submit_evidence_feedback（中风险）。

方案要求：
- 独立 feature flag（AGENT_FEEDBACK_TOOL_ENABLED，默认关）；
- 提交前显示目标 request 与反馈摘要 —— 工具提供 preview 动作（Agent 先取
  摘要向用户展示，确认后 submit；preview 不写任何数据）；
- 调用现有 FeedbackService.create_feedback（不直接写表）：request_id 必须
  已存在于 query_logs、一 request 一 feedback、not_helpful 按既有规则进
  bad_cases —— 这些语义全部沿用，不复制实现；
- 幂等读回：同 request_id 重复提交返回已存在记录的确认（不报错、不覆盖）；
- 审计脱敏：comment 不进 access_audit 明文。
"""

from __future__ import annotations

from typing import Any

from application.evidence_tools.contracts import ToolSpec
from application.evidence_tools.registry import ToolExecutionRejected
from infrastructure.database import ProductDatabase

# 预览动作值域（submit 前的确认模式：Agent 先 preview 给用户看，再 submit）
PREVIEW_RATINGS = ("helpful", "not_helpful")
REASON_CODE_WHITELIST = frozenset({
    "correct", "wrong_answer", "incomplete", "missing_source", "outdated_version",
    "bad_citation", "confusing", "slow", "other",
})


SUBMIT_FEEDBACK_SPEC = ToolSpec(
    name="mindgraph_submit_evidence_feedback",
    description=(
        "对一次已完成的回答提交质量反馈（有帮助/没帮助），进入质量账本。"
        "两步确认模式：先 action=preview 获取目标回答摘要（不写数据），向用户"
        "展示确认后，再用 action=submit 提交。每个回答只能有一条反馈。"
        "not_helpful 会按既有规则自动进入 bad_cases 供人工复核。"
    ),
    mode="write",
    risk="medium",
    allowed_in=frozenset({"external_mcp"}),
    timeout_seconds=5.0,
    requires_approval=False,  # 中风险：preview+确认由调用方（Agent）承担，审计留痕
    # comment 与 preview 摘要是用户内容，不进审计明文
    redact_fields=frozenset({"comment", "preview_question"}),
    input_schema={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["preview", "submit"], "default": "preview"},
            "request_id": {"type": "string", "minLength": 1, "maxLength": 64},
            "rating": {"type": "string", "enum": ["helpful", "not_helpful"]},
            "reason_codes": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
            "comment": {"type": "string", "maxLength": 2000},
        },
        "required": ["action", "request_id"],
        "additionalProperties": False,
    },
)


def make_submit_feedback_handler():
    from application.feedback_service import FeedbackService
    from domain.errors import ConflictError, NotFoundError

    def _handler(
        principal: dict[str, Any],
        scope: dict[str, Any] | None,
        arguments: dict[str, Any],
        deadline: float | None,
    ) -> dict[str, Any]:
        from infrastructure.settings import get_settings

        if not get_settings().AGENT_FEEDBACK_TOOL_ENABLED:
            raise ToolExecutionRejected("feedback tool disabled (AGENT_FEEDBACK_TOOL_ENABLED=false)")

        database: ProductDatabase = _database()
        action = arguments["action"]
        request_id = str(arguments["request_id"]).strip()
        query = database.fetch_one(
            "SELECT question, answer, result_state, created_at FROM query_logs WHERE request_id=?",
            (request_id,),
        )
        if query is None:
            # 与 NotFoundError 同语义：不暴露存在性细节
            raise ToolExecutionRejected("request_id 不存在（无法对不存在的回答提交反馈）")

        # ── preview：只读摘要（确认模式第一步；绝不写） ──
        if action == "preview":
            existing = database.fetch_one(
                "SELECT feedback_id, rating FROM feedback WHERE request_id=?", (request_id,)
            )
            return {
                "preview": {
                    "request_id": request_id,
                    "question": (query["question"] or "")[:120],
                    "answer_excerpt": (query["answer"] or "")[:200],
                    "result_state": query["result_state"],
                    "created_at": query["created_at"],
                    "already_submitted": bool(existing),
                    "previous_rating": existing["rating"] if existing else None,
                },
                "note": "请向用户展示以上摘要并确认；确认后用 action=submit 提交。",
            }

        # ── submit：经 FeedbackService（不直接写表） ──
        rating = arguments.get("rating")
        if rating not in PREVIEW_RATINGS:
            raise ToolExecutionRejected("submit 需要 rating（helpful / not_helpful）")
        reason_codes = [str(code) for code in (arguments.get("reason_codes") or [])]
        unknown = [code for code in reason_codes if code not in REASON_CODE_WHITELIST]
        if unknown:
            raise ToolExecutionRejected(f"reason_codes 不在白名单内: {unknown}")
        comment = arguments.get("comment")
        if comment is not None and not isinstance(comment, str):
            raise ToolExecutionRejected("comment 必须是字符串")

        # 幂等读回：已有反馈 → 返回确认（不报错、不覆盖）
        existing = database.fetch_one(
            "SELECT feedback_id, rating, created_at FROM feedback WHERE request_id=?", (request_id,)
        )
        if existing is not None:
            return {
                "status": "already_submitted",
                "feedback_id": existing["feedback_id"],
                "rating": existing["rating"],
                "created_at": existing["created_at"],
                "note": "该回答已有反馈（一回答一反馈）；本次未重复写入。",
            }

        from domain.models import FeedbackCreate

        payload = FeedbackCreate(
            request_id=request_id,
            rating=rating,  # type: ignore[arg-type]
            reason_codes=reason_codes,
            comment=comment if isinstance(comment, str) and comment.strip() else None,
        )
        try:
            record = FeedbackService(database).create_feedback(payload)
        except ConflictError:
            # 并发竞态（preview 后另一路提交）→ 读回返回，语义同幂等
            row = database.fetch_one("SELECT feedback_id, rating, created_at FROM feedback WHERE request_id=?", (request_id,))
            return {
                "status": "already_submitted",
                "feedback_id": row["feedback_id"],
                "rating": row["rating"],
                "created_at": row["created_at"],
                "note": "并发提交已合并为已有反馈。",
            }
        except NotFoundError as exc:
            raise ToolExecutionRejected(str(exc)) from exc
        bad_case_note = "；已进入 bad_cases 待人工复核" if rating == "not_helpful" else ""
        return {
            "status": "submitted",
            "feedback_id": record.feedback_id,
            "rating": record.rating,
            "note": f"反馈已记录{bad_case_note}。",
        }

    return _handler


# 数据库经 handlers 的模块级桥接注入（装配层 set_handler_database）——
# 与其余工具同源，避免第二份全局状态
def _database() -> ProductDatabase:
    from application.evidence_tools.handlers import _database as handlers_database

    return handlers_database()
