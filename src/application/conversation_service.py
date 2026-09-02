"""ConversationService：服务端会话（M3，实施方案 §7.1）。

治理语义（与既有通道一致）：
- 授权一律用稳定 principal_id（auth 的用户标识），不信任展示名；
- 跨主体访问返回 not_found（统一 deny，不暴露资源存在性）；
- 归档软删除（status='archived'），不做物理删除；
- 回放按 sequence_no 稳定排序；写消息在唯一约束上并发安全（重试由
  IntegrityError 转换为可判定的冲突错误）；
- 历史消息中的 evidence（citations_json）只是当时快照——回放接口不做
  ACL 重放裁剪之外的授权判断：消息可见性以会话归属为准；证据是否
  仍可见由 Chat 在新一轮问答里按当前 ACL 重新判定（方案 M3 验收语义）。

消费开关：CONVERSATION_PERSISTENCE_ENABLED（默认关；关闭时本服务不装配
路由，前端不提供迁移入口）。
"""

from __future__ import annotations

from datetime import UTC, datetime
import uuid
from typing import Any

from infrastructure.database import ProductDatabase, dumps

CONVERSATION_PAGE_SIZE = 50


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ConversationNotFoundError(LookupError):
    """统一 not-found：越权与不存在不可区分（不暴露存在性）。"""


class SequenceConflictError(RuntimeError):
    """并发续问撞 sequence：调用方可重试（读取当前最大值后重新追加）。"""


class ConversationService:
    def __init__(self, database: ProductDatabase) -> None:
        self.database = database

    def create_conversation(self, *, principal_id: str, title: str, workspace: str | None = None, department: str | None = None) -> dict[str, Any]:
        conversation_id = f"conv-{uuid.uuid4().hex[:16]}"
        now = _now_iso()
        self.database.execute(
            "INSERT INTO conversations (conversation_id, principal_id, title, workspace, department, status, created_at, updated_at)"
            " VALUES (?,?,?,?,?, 'active', ?, ?)",
            (conversation_id, principal_id, title[:200], workspace, department, now, now),
        )
        return {"conversation_id": conversation_id, "title": title, "status": "active", "created_at": now, "updated_at": now}

    def list_conversations(self, *, principal_id: str, cursor: str | None = None, limit: int = CONVERSATION_PAGE_SIZE) -> dict[str, Any]:
        """cursor 分页：cursor 为上一页最后一条的 conversation_id（按 updated_at 倒序稳定键）。"""
        bound = max(1, min(int(limit), 200))
        if cursor:
            anchor = self.database.fetch_one(
                "SELECT updated_at FROM conversations WHERE conversation_id=? AND principal_id=? AND status != 'archived'",
                (cursor, principal_id),
            )
            if anchor is None:
                raise ConversationNotFoundError(cursor)
            # 键序与主序完全一致（updated_at DESC, conversation_id DESC），
            # tie-break 用 <：同一毫秒创建的会话也能稳定翻页
            rows = self.database.fetch_all(
                "SELECT conversation_id, title, status, created_at, updated_at, retention_until"
                " FROM conversations WHERE principal_id=? AND status != 'archived'"
                " AND (updated_at < ? OR (updated_at = ? AND conversation_id < ?))"
                " ORDER BY updated_at DESC, conversation_id DESC LIMIT ?",
                (principal_id, anchor["updated_at"], anchor["updated_at"], cursor, bound + 1),
            )
        else:
            rows = self.database.fetch_all(
                "SELECT conversation_id, title, status, created_at, updated_at, retention_until"
                " FROM conversations WHERE principal_id=? AND status != 'archived'"
                " ORDER BY updated_at DESC, conversation_id DESC LIMIT ?",
                (principal_id, bound + 1),
            )
        # 哨兵行（bound+1）只用于探测是否还有下一页；next_cursor 取本页
        # 最后一行（items 内），下一页 WHERE 从 cursor 行之后开始——cursor
        # 行本身属于本页，不会被跳过或重复
        has_more = len(rows) > bound
        page_rows = rows[:bound]
        next_cursor = page_rows[-1]["conversation_id"] if has_more and page_rows else None
        items = [
            {
                "conversation_id": row["conversation_id"],
                "title": row["title"],
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in page_rows
        ]
        return {"items": items, "next_cursor": next_cursor}

    def _owned(self, conversation_id: str, principal_id: str) -> dict[str, Any]:
        row = self.database.fetch_one(
            "SELECT conversation_id, principal_id, title, status, created_at, updated_at, retention_until"
            " FROM conversations WHERE conversation_id=? AND principal_id=? AND status != 'archived'",
            (conversation_id, principal_id),
        )
        if row is None:
            raise ConversationNotFoundError(conversation_id)
        return row

    def get_messages(self, *, conversation_id: str, principal_id: str) -> list[dict[str, Any]]:
        self._owned(conversation_id, principal_id)
        rows = self.database.fetch_all(
            "SELECT message_id, conversation_id, sequence_no, role, content, citations_json, created_at, request_id"
            " FROM messages WHERE conversation_id=? ORDER BY sequence_no ASC",
            (conversation_id,),
        )
        import json

        return [
            {
                "message_id": row["message_id"],
                "conversation_id": row["conversation_id"],
                "sequence_no": row["sequence_no"],
                "role": row["role"],
                "content": row["content"],
                "citations": json.loads(row["citations_json"] or "[]"),
                "request_id": row["request_id"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def append_message(
        self,
        *,
        conversation_id: str,
        principal_id: str,
        role: str,
        content: str,
        citations: list[dict[str, Any]] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        self._owned(conversation_id, principal_id)
        row = self.database.fetch_one(
            "SELECT COALESCE(MAX(sequence_no), 0) AS max_seq FROM messages WHERE conversation_id=?",
            (conversation_id,),
        )
        next_seq = int(row["max_seq"]) + 1
        message_id = f"msg-{uuid.uuid4().hex[:16]}"
        try:
            self.database.execute(
                "INSERT INTO messages (message_id, conversation_id, sequence_no, role, content, citations_json, request_id, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (message_id, conversation_id, next_seq, role, content, dumps(citations or []), request_id, _now_iso()),
            )
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise SequenceConflictError(str(exc)) from exc
            raise
        self.database.execute(
            "UPDATE conversations SET updated_at=? WHERE conversation_id=?",
            (_now_iso(), conversation_id),
        )
        return {"message_id": message_id, "sequence_no": next_seq, "conversation_id": conversation_id}

    def archive_conversation(self, *, conversation_id: str, principal_id: str) -> None:
        """归档（软删除）：统一 not_found 语义；重复归档幂等。"""
        row = self.database.fetch_one(
            "SELECT status FROM conversations WHERE conversation_id=? AND principal_id=?",
            (conversation_id, principal_id),
        )
        if row is None:
            raise ConversationNotFoundError(conversation_id)
        self.database.execute(
            "UPDATE conversations SET status='archived', updated_at=? WHERE conversation_id=?",
            (_now_iso(), conversation_id),
        )

    # ── 显式迁移（localStorage → 服务端；方案 §8.2） ──

    def import_local_turns(
        self,
        *,
        conversation_id: str,
        principal_id: str,
        turns: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """把前端导出的本地轮次导入指定（已创建的）会话。

        幂等：以 local_turn_id 为 request_id 追加消息前先查重，重复导入
        不产生重复消息；返回映射表（local index → message_id/sequence_no）
        供前端校验轮次数。
        """
        self._owned(conversation_id, principal_id)
        mapping: list[dict[str, Any]] = []
        imported = 0
        skipped = 0
        for index, turn in enumerate(turns):
            local_id = str(turn.get("local_turn_id") or f"local-{index}")
            # 幂等查重：导入写入的 request_id 形如 "{local_id}:q" / "{local_id}:a"
            existing = self.database.fetch_one(
                "SELECT message_id, sequence_no FROM messages"
                " WHERE conversation_id=? AND (request_id=? OR request_id=?)",
                (conversation_id, f"{local_id}:q", f"{local_id}:a"),
            )
            if existing is not None:
                mapping.append({"local_turn_id": local_id, "message_id": existing["message_id"], "sequence_no": existing["sequence_no"], "imported": False})
                skipped += 1
                continue
            user_seq = self.append_message(
                conversation_id=conversation_id, principal_id=principal_id,
                role="user", content=str(turn.get("question") or ""), request_id=f"{local_id}:q",
            )
            assistant_seq = self.append_message(
                conversation_id=conversation_id, principal_id=principal_id,
                role="assistant", content=str(turn.get("answer") or ""),
                citations=turn.get("citations") if isinstance(turn.get("citations"), list) else None,
                request_id=f"{local_id}:a",
            )
            mapping.append({"local_turn_id": local_id, "user_sequence_no": user_seq["sequence_no"], "assistant_sequence_no": assistant_seq["sequence_no"], "imported": True})
            imported += 1
        return {"imported": imported, "skipped_existing": skipped, "mapping": mapping, "total_messages": imported * 2 + skipped * 0}
