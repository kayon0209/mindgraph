"""Assist 通道的类型化判定信封（M1，受治理的 agent 交付面）。

设计：Assist 不是新功能，而是把既有 ChatService 应用层以“冻结契约 +
机器可判定 verdict”的形式暴露给 agent：

- ``AssistRequest`` 复用 ``ChatRequest`` 的全部字段（同一次校验），契约独立演进；
- ``AssistResult`` 继承 ``AnswerResult``（含引用/检索 trace/用量/耗时），
  额外在最外层携带 ``verdict``（ErrorCode 取值面）——agent 只读 verdict 即可
  决定下一步，不需要解析自然语言答案或内层枚举字符串。

只读：本 schema 不携带任何写回/提议字段（写回见 M2 路线图，ADR-003）。
"""

from __future__ import annotations

from domain.models import AnswerResult, ChatRequest, ErrorCode


class AssistRequest(ChatRequest):
    """Agent 面请求：与 /chat 相同的字段，但作为独立契约冻结。"""


class AssistResult(AnswerResult):
    """Agent 面终态信封：AnswerResult 全字段 + 机器可判定 verdict。"""

    verdict: ErrorCode
