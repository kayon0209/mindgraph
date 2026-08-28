"""日期解析容错工具。

背景：vault frontmatter 与关系抽取的 effective_from/effective_to 以文本入库，
历史上未强制 ISO 格式；而检索管线用 ``date.fromisoformat`` 解析，任何一条
非 ISO 日期都会让相关文档的检索整体 503。本模块提供读侧容错与写侧校验：

- ``parse_date_safe``：解析失败返回 None（调用方按"缺省日期"处理并告警）；
- ``is_iso_date``：写侧校验，frontmatter 同步时标记 ``invalid_effective_date_format``。
"""
from __future__ import annotations

from datetime import date


def parse_date_safe(value: object | None) -> date | None:
    """尽力把标量解析为 date；失败返回 None，绝不抛出。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def is_iso_date(value: object | None) -> bool:
    """值是否为可解析的 ISO 日期（YYYY-MM-DD）；空值视为合法（缺失由别处校验）。"""
    if value is None:
        return True
    return parse_date_safe(value) is not None
