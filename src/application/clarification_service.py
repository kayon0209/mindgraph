"""PR-13 可恢复澄清协议：clarification_requests 表的业务消费方。

## 背景（任务书现场核对）

schema v14 已建好（principal_id NOT NULL、consumed_at 天然幂等键），
``verify_clarification_token`` 自 PR 以来一直保留为"token 语义的确定性定义
与测试面"——本模块就是接上这个断点：**持久化授权后的服务端 resume**。

## 语义边界（现场核对报告拍板，测试一一对应）

| 场景 | 返回 | 说明 |
|---|---|---|
| owner 有效期内首次 resume | ``resumed`` + 原问题集 | 一次性消费，consumed_at 落账 |
| 重复 resume | ``already_consumed`` + 原问题集 | 幂等：不重复副作用，consumed_at 不改写 |
| 过期 | ``expired`` | 不返回原问题内容 |
| 不存在 / 跨主体 / 校验失败 | ``not_found`` | 三者**不可区分**（不暴露存在性/内部原因） |

## 盐的跨进程前提（审查 F10 的延续）

``_clarification_salt`` 缺省时是**进程级随机盐**——同进程校验能过，跨进程
（重启、多 worker）必然失败。服务端 resume 是跨进程契约，因此部署必须显式
设置 ``MINDGRAPH_CLARIFICATION_SALT``；校验失败一律按 not_found 处理，
绝不以"盐没配"为由放过伪造 token（fail-closed）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import hmac
import json
import logging
from typing import Any

from infrastructure.database import ProductDatabase

logger = logging.getLogger("mindgraph.clarification")

RESUMED = "resumed"
ALREADY_CONSUMED = "already_consumed"
EXPIRED = "expired"
NOT_FOUND = "not_found"

# resume 恢复时沿用原请求的检索预算（clarification 写入时未存预算字段的
# 历史记录按此默认；新写入路径由 writer 显式传入）。
DEFAULT_RETRIEVAL_BUDGET: dict[str, Any] = {"final_top_k": 5, "graph_enabled": False}


@dataclass(frozen=True)
class ResumeOutcome:
    """一次 resume 的机器可判定结果（状态机全枚举，无隐藏分支）。"""

    state: str
    questions: list[str] = field(default_factory=list)
    original_request_hash: str | None = None
    retrieval_budget: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_RETRIEVAL_BUDGET))

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "questions": self.questions,
            "original_request_hash": self.original_request_hash,
            "retrieval_budget": self.retrieval_budget,
        }


class ClarificationService:
    """clarification_requests 的写入与一次性恢复。"""

    def __init__(self, database: ProductDatabase) -> None:
        self.database = database

    # ── 写入（stream_assist 澄清路由调用）──

    def record(
        self,
        *,
        clarification_id: str,
        principal_id: str,
        questions: list[str],
        context_hash: str,
        expires_at: str,
        conversation_id: str | None = None,
        original_question: str = "",
        retrieval_budget: dict[str, Any] | None = None,
    ) -> None:
        """持久化一张澄清卡。

        ⚠️ context_hash 的签名 key 是 ``original_request_hash``（问句 hash），
        **不是原问句**——DB 只存 hash，校验端只能以它重算。调用方（stream_assist
        澄清路由）生成 token 时必须用同一口径，否则 resume 校验必然失败
        （现场实测教训：make_clarification_token(request.question, ...) 与
        本表的校验口径不一致）。
        """
        import hashlib

        original_hash = hashlib.sha256(original_question.encode("utf-8")).hexdigest()[:16]
        payload = {"questions": questions, "budget": retrieval_budget or dict(DEFAULT_RETRIEVAL_BUDGET)}
        existing = self.database.fetch_one(
            "SELECT clarification_id FROM clarification_requests WHERE clarification_id=?",
            (clarification_id,),
        )
        if existing:
            return  # 幂等写入：同 id 不重复落（token 定位符冲突即静默保留首条）
        self.database.execute(
            "INSERT INTO clarification_requests (clarification_id, principal_id, conversation_id,"
            " original_request_hash, questions_json, context_hash, expires_at, consumed_at, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                clarification_id, principal_id, conversation_id,
                original_hash, json.dumps(payload, ensure_ascii=False), context_hash,
                expires_at, None, datetime.now(UTC).isoformat(),
            ),
        )

    # ── 恢复（resume 端点调用）──

    def resume(
        self,
        *,
        clarification_id: str,
        principal_id: str,
        answers: list[str],
        conversation_id: str | None = None,
    ) -> ResumeOutcome:
        """按主体恢复澄清上下文；所有拒绝路径与"不存在"不可区分。"""
        row = self.database.fetch_one(
            "SELECT * FROM clarification_requests WHERE clarification_id=?",
            (clarification_id,),
        )
        if row is None or row["principal_id"] != principal_id:
            return ResumeOutcome(NOT_FOUND)

        # 过期判定先于消费：过期请求永远不能被消费
        try:
            expired = datetime.fromisoformat(row["expires_at"]) < datetime.now(UTC)
        except ValueError:
            expired = True  # 损坏的 expires_at 按已过期处理（fail-closed）
        if expired:
            return ResumeOutcome(EXPIRED)

        payload = self._payload(row)
        questions = payload.get("questions", [])

        # 幂等：已消费 → 返回原语义，不重复副作用，不改写 consumed_at
        if row["consumed_at"] is not None:
            return ResumeOutcome(
                ALREADY_CONSUMED,
                questions=questions,
                original_request_hash=row["original_request_hash"],
                retrieval_budget=payload.get("budget") or dict(DEFAULT_RETRIEVAL_BUDGET),
            )

        # 签名校验：context_hash 必须与「原问句 + 问题集」在**部署盐**下一致。
        # 未配盐（进程随机）→ 校验必败 → not_found（宁拒勿伪造，不暴露原因）。
        if not self._context_hash_matches(row, questions):
            logger.warning("clarification_context_hash_mismatch", extra={"clarification_id": clarification_id})
            return ResumeOutcome(NOT_FOUND)

        consumed_at = datetime.now(UTC).isoformat()
        updated = self.database.execute(
            "UPDATE clarification_requests SET consumed_at=? WHERE clarification_id=? AND consumed_at IS NULL",
            (consumed_at, clarification_id),
        )
        if not updated:
            # 并发竞态：另一路刚好消费完 → 幂等返回
            return ResumeOutcome(
                ALREADY_CONSUMED,
                questions=questions,
                original_request_hash=row["original_request_hash"],
                retrieval_budget=payload.get("budget") or dict(DEFAULT_RETRIEVAL_BUDGET),
            )
        return ResumeOutcome(
            RESUMED,
            questions=questions,
            original_request_hash=row["original_request_hash"],
            retrieval_budget=payload.get("budget") or dict(DEFAULT_RETRIEVAL_BUDGET),
        )

    # ── 内部 ──

    @staticmethod
    def _payload(row) -> dict[str, Any]:
        """解析 questions_json；兼容历史纯数组形态（v14 表无业务消费方时期的测试数据）。"""
        raw = row["questions_json"] or "[]"
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"questions": [], "budget": dict(DEFAULT_RETRIEVAL_BUDGET)}
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"questions": parsed, "budget": dict(DEFAULT_RETRIEVAL_BUDGET)}
        return {"questions": [], "budget": dict(DEFAULT_RETRIEVAL_BUDGET)}

    @staticmethod
    def _context_hash_matches(row, questions: list[str]) -> bool:
        """部署盐下重算 context_hash 与存量比对；盐未配置时必败（fail-closed）。"""
        import os

        if not os.getenv("MINDGRAPH_CLARIFICATION_SALT", ""):
            return False
        from application.agent_service import make_clarification_token

        original = row["original_request_hash"]  # 定位符不参与签名；原始问句经 hash 绑定
        _, expected_hash, _ = make_clarification_token(original, questions)
        return hmac.compare_digest(expected_hash, row["context_hash"])
