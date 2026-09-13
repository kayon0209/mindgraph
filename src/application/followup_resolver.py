"""PR-12 服务端会话上下文与指代解析。

## 背景（任务书核对实测）

``ConversationService`` 是纯持久化：消息按 ``sequence_no`` 全量返回，**从不
回放进 prompt**——前端不拼历史时，"上一版""那个标准"这类续问无法被检索理解。

## 设计（两条线，职责分离）

- :class:`ConversationContextService` —— **取数层**：按 owner 隔离取最近窗口，
  截断预算显式（``max_turns`` × ``max_context_chars`` 双限取更严），
  归档会话拒绝提供上下文（retention 一致）。
- :class:`FollowupResolver` —— **解析层**：确定性规则做指代/槽位/纠错解析，
  **不用 LLM 改写**（红线：改写虚构的版本/条件/事实会直接污染检索）。
  每次替换都留 ``substitutions`` 证据；无法确定时**原样通过**（宁缺毋滥）。

## token 可观测（质量门禁硬要求）

``context_tokens`` 把多轮上下文的额外输入 token 量显式化——延迟与成本
增量由此可归因，不靠事后估算。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

# 确定性词表（解析规则的全部依据；扩规则 = 扩词表，不引模型）
_REFERENCE_WORDS = ("那个", "这项", "这个", "上面", "刚才", "它")
_VERSION_WORDS = ("上一版", "前一版", "旧版", "上一版本")
_CORRECTION_PATTERN = re.compile(r"^(不对|不是|错了|我说的?不是|更正)[，,。]?\s*")
_INHERIT_PROMPTS = ("那", "那么", "再问", "继续", "接着")
_TOPIC_STOP = ("的", "怎么", "如何", "是", "报", "呢", "？", "?", "标准", "时限", "费用")


@dataclass(frozen=True)
class Substitution:
    """一次解析替换的证据：什么指代被换成了什么。"""

    kind: str  # reference / version / slot_inheritance / correction
    marker: str  # 原文中的指代片段
    replacement: str  # 替换进的上下文内容（片段）

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "marker": self.marker, "replacement": self.replacement}

    def __getitem__(self, key: str) -> str:
        return self.to_dict()[key]


@dataclass(frozen=True)
class ResolutionResult:
    resolved_query: str
    substitutions: list[Substitution] = field(default_factory=list)
    context_tokens: int = 0
    history_turns_used: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolved_query": self.resolved_query,
            "substitutions": [item.to_dict() for item in self.substitutions],
            "context_tokens": self.context_tokens,
            "history_turns_used": self.history_turns_used,
        }


def _estimate_tokens(text: str) -> int:
    """粗估 token：CJK 每字 ~1，拉丁词 ~1.3 字符/token。经验值，仅用于归因。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if ord(ch) > 0x2E7F)
    other = len(text) - cjk
    return cjk + max(other // 4, 1 if other else 0)


class ConversationContextService:
    """按归属取最近对话窗口，预算显式。

    「轮」= 一条 user + 其后的 assistant 回复。取最近 N 轮并按字符预算从**旧端**
    截断——最近上下文优先保住，截掉的是旧历史（与人类对话衰减一致）。
    """

    def __init__(self, conversation_service, max_turns: int = 5, max_context_chars: int = 4000) -> None:
        if max_turns < 1:
            raise ValueError("max_turns must be >= 1")
        if max_context_chars < 100:
            raise ValueError("max_context_chars must be >= 100")
        self._conversations = conversation_service
        self.max_turns = max_turns
        self.max_context_chars = max_context_chars

    def recent_window(self, conversation_id: str, *, principal_id: str) -> list[dict[str, Any]]:
        """owner 隔离的最近窗口；归属不符/归档 → 抛底层服务的错误（串线 = 0）。"""
        messages = self._conversations.get_messages(
            conversation_id=conversation_id, principal_id=principal_id,
        )
        if not messages:
            return []
        # 归档会话：get_messages 已按归属放行（历史回放合法），但**上下文注入**
        # 不该再喂给检索——归档即会话终了。判断放在本层，不动持久化契约。
        status_row = self._conversations.database.fetch_one(
            "SELECT status FROM conversations WHERE conversation_id=?", (conversation_id,),
        )
        if status_row and status_row["status"] != "active":
            raise LookupError("conversation archived; no follow-up context")

        # 只从最近往前收 max_turns 轮：遇 user 先计数，超预算立即停；
        # break 前已入窗的孤儿 assistant（其配对 user 被排除）一并弹出——
        # 没有对应提问的回答对指代解析是噪声。
        selected: list[dict[str, Any]] = []
        turns = 0
        for message in reversed(messages):
            if message["role"] == "user":
                turns += 1
                if turns > self.max_turns:
                    while selected and selected[-1]["role"] != "user":
                        selected.pop()
                    break
            selected.append(message)
        selected.reverse()
        # 字符预算：从旧端截断
        while selected and sum(len(item["content"]) for item in selected) > self.max_context_chars:
            selected.pop(0)
        return selected


class FollowupResolver:
    """确定性续问解析：指代绑定、版本指代、槽位继承、纠错标记。

    全部规则可解释、可测试；没有任何规则命中时**原样返回**——绝不虚构
    用户没有提供的条件（红线见任务书范围外）。
    """

    def resolve(self, question: str, history: list[dict[str, Any]]) -> ResolutionResult:
        substitutions: list[Substitution] = []
        context_tokens = sum(_estimate_tokens(item.get("content", "")) for item in history)
        resolved = question

        if history:
            last_user = next(
                (item["content"] for item in reversed(history) if item.get("role") == "user"),
                "",
            )
            last_user = str(last_user)

            # 纠错优先：显式否定上一问 → 指向改后的主题，标记 correction
            correction_match = _CORRECTION_PATTERN.match(resolved)
            if correction_match:
                marker = correction_match.group(0)
                resolved = _CORRECTION_PATTERN.sub("", resolved, count=1)
                substitutions.append(Substitution("correction", marker, last_user[:40]))

            # 版本指代：上一版/旧版 → 绑定上文最近版本号或制度名
            for word in _VERSION_WORDS:
                if word in resolved:
                    version_topic = self._topic_of(last_user)
                    if version_topic:
                        resolved = resolved.replace(word, f"{version_topic}的上一版", 1)
                        substitutions.append(Substitution("version", word, version_topic))
                    break

            # 指代词：那个/这项/这个 → 绑定上文主题
            for word in _REFERENCE_WORDS:
                if word in resolved:
                    topic = self._topic_of(last_user)
                    if topic:
                        resolved = resolved.replace(word, topic, 1)
                        substitutions.append(Substitution("reference", word, topic))
                    break

            # 槽位继承：那/那么/继续 开头的省略问 → 直接前缀上文主题
            stripped = resolved.lstrip()
            if not substitutions and stripped.startswith(_INHERIT_PROMPTS):
                topic = self._topic_of(last_user)
                if topic and topic not in resolved:
                    resolved = f"{topic}，{resolved}"
                    substitutions.append(Substitution("slot_inheritance", stripped[:2], topic))

        return ResolutionResult(
            resolved_query=resolved,
            substitutions=substitutions,
            context_tokens=context_tokens,
            history_turns_used=len(history),
        )

    @staticmethod
    def _topic_of(previous_user_text: str) -> str:
        """从上一问提取主题短语（截到常见停词），供指代绑定。"""
        text = str(previous_user_text).strip()
        if not text:
            return ""
        cut = len(text)
        for stop in _TOPIC_STOP:
            index = text.find(stop)
            if 0 < index < cut:
                cut = index
        topic = text[:cut].strip()
        # 主题太短没有绑定价值（如单字"那"），交回原样通过
        return topic if len(topic) >= 2 else ""
