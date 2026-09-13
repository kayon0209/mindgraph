"""ChunkingPolicy：全仓 Markdown / 上传文档 / 全量与增量索引的切分参数单一来源（PR-03）。

## 为什么需要它

2026-09-10 实测，同一仓库的切分参数散落在三处且互不知情：

===========  ================================================  ==========================
站点          位置                                              参数
===========  ================================================  ==========================
结构化切分    ``StructuredChunker``（上传文档路径）              500/1200/50（构造默认）
旧加载器      ``document_loader.DEFAULT_CHUNK_SIZE/OVERLAP``     500/50（模块常量）
线上索引      ``mindgraph_index_service._load_note_chunks``      **内联字面量** 500/50
===========  ================================================  ==========================

第三处不读任何常量——线上 ``mg-`` 索引（25 篇 / 581 chunks）的构建点一旦漂移，
没有任何代码层信号。本模块把三个站点收敛到一个不可变对象上；**本 PR 不改任何
数值**，``legacy_v1`` 就是历史参数的精确快照（PR-09 的 parent-child 双跑才会
引入新策略）。

## 契约

- 不可变（frozen dataclass），字段带校验：size 正整数、``overlap < child_size``；
- ``from_settings()`` 经 ``CHUNKING_POLICY`` 选择预设；未知名 **fail-closed**
  （不静默回落 legacy——静默回落等于让漂移合法化）；
- 索引 manifest 必须记录 ``chunking_policy``（name/version/parameters），
  评测与追溯据此绑定"结果是在哪套切分参数下产生的"。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChunkingPolicy:
    """一次索引构建所用的切分参数。数值变更 = 语义变更，必须换 name/version。"""

    name: str
    version: str
    child_size: int
    parent_size: int
    overlap: int

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.version.strip():
            raise ValueError("chunking policy name/version must be non-empty")
        for field in ("child_size", "parent_size"):
            value = getattr(self, field)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{field} must be a positive integer")
        if not isinstance(self.overlap, int) or isinstance(self.overlap, bool) or self.overlap < 0:
            raise ValueError("overlap must be a non-negative integer")
        if self.overlap >= self.child_size:
            raise ValueError("overlap must be smaller than child size")

    def manifest_payload(self) -> dict:
        """写入索引 manifest 的投影：可追溯到具体参数。"""
        return {
            "name": self.name,
            "version": self.version,
            "child_size": self.child_size,
            "parent_size": self.parent_size,
            "overlap": self.overlap,
        }

    @staticmethod
    def from_settings() -> ChunkingPolicy:
        """当前生效的切分策略（``CHUNKING_POLICY`` 未配置 = legacy_v1）。"""
        from infrastructure.settings import get_settings

        return get_policy(get_settings().CHUNKING_POLICY or DEFAULT_POLICY_NAME)


# 历史参数的精确快照（2026-09-11 冻结）：三个散落站点的共同数值。
# 改这里的任何数值都会改变切分输出、chunk ID 分母与已公布的检索指标，
# 必须以新 name/version 发新策略，禁止原地改。
LEGACY_V1 = ChunkingPolicy(
    name="legacy_v1", version="1", child_size=500, parent_size=1200, overlap=50,
)

_PRESETS: dict[str, ChunkingPolicy] = {LEGACY_V1.name: LEGACY_V1}
DEFAULT_POLICY_NAME = LEGACY_V1.name


def get_policy(name: str) -> ChunkingPolicy:
    """按名取预设；未知名拒绝（fail-closed，不静默回落）。"""
    try:
        return _PRESETS[name]
    except KeyError:
        known = ", ".join(sorted(_PRESETS))
        raise ValueError(f"unknown chunking policy {name!r}; known: {known}") from None
