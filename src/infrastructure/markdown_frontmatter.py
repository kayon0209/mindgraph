"""Markdown Frontmatter 读写（MindGraph 稳定 ID 注入）。

设计原则（对齐 PDF「AI 不改正文」）：
- 解析 Frontmatter 优先用 YAML；失败时回退极简解析（仅顶层标量）。
- 注入 ``mindgraph_id`` 采用「定点插入」：仅在 Frontmatter 块内、开头
  ``---`` 之后追加一行，**绝不重写正文，也不整体重排用户已有 Frontmatter**。
"""
from __future__ import annotations

import re
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - 运行环境必须具备 PyYAML
    yaml = None  # type: ignore[assignment]

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str, str | None]:
    """返回 ``(frontmatter_dict, body_去frontmatter, raw_frontmatter_str_or_None)``。"""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text, None
    raw = m.group(1)
    body = text[m.end():]
    return (_load_yaml(raw) or {}, body, raw)


def _load_yaml(raw: str) -> dict[str, Any] | None:
    if yaml is not None:
        try:
            data = yaml.safe_load(raw)
            if isinstance(data, dict):
                return data
        except yaml.YAMLError:
            pass
    return _minimal_yaml_load(raw)


def _minimal_yaml_load(raw: str) -> dict[str, Any]:
    """极简回退：仅解析 ``key: value`` 顶层标量，跳过列表/嵌套/注释。"""
    out: dict[str, Any] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        key, val = key.strip(), val.strip()
        if not key or val.startswith(("[", "{")):
            continue
        out[key] = _coerce(val)
    return out


def _coerce(val: str) -> Any:
    low = val.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "~"):
        return None
    return val


def has_frontmatter(text: str) -> bool:
    return bool(_FRONTMATTER_RE.match(text))


def inject_mindgraph_id(text: str, mindgraph_id: str) -> str:
    """在 Frontmatter 中定点注入/替换 ``mindgraph_id``。

    - 已含 Frontmatter：在开头 ``---`` 之后写入一行；若已存在 ``mindgraph_id:`` 行则替换之（不叠加）。
    - 不含 Frontmatter：新建最小 Frontmatter 块。
    仅改动 Frontmatter 块，绝不正文；统一以 ``\\n`` 行结尾（Obsidian 兼容）。
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return f"---\nmindgraph_id: {mindgraph_id}\n---\n\n{text}"
    fm_block = text[: m.end()]
    body = text[m.end():]
    out: list[str] = []
    for i, line in enumerate(fm_block.split("\n")):
        out.append(line)
        if i == 0:  # 开头 ``---``：其后插入新 ID
            out.append(f"mindgraph_id: {mindgraph_id}")
        elif re.match(r"^\s*mindgraph_id:.*$", line):  # 已有旧 ID：删除刚写入的旧行
            out.pop()
    return "\n".join(out) + body
