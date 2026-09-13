"""m4 索引构建的替身（供多个测试文件共用）。

为什么要有它：要断言 ``build()`` **真正写进 manifest** 的切分口径，唯一诚实的做法
是真调一次 ``build()``——而 ``build()`` 会构造 ``BGEEmbeddingProvider`` 并读
``documents.active_chunks()``。过去为了绕开这件事，测试退化成 ``inspect.getsource``
字符串断言，于是"重构即假红、留字符串即假绿"，而真正要守的性质从没被验证过。
替身把真调用变成可能：只替换 embedding provider 与文档来源，build() 其余全是真代码。

（``tests/`` 在 pytest 的 pythonpath 上，直接用 ``from index_build_fixture import ...``。
``stub_embedding`` fixture 在 ``tests/conftest.py``，原因见文件末尾注释。）
"""

from __future__ import annotations

from pathlib import Path

from infrastructure.database import ProductDatabase


class StubDocuments:
    """只提供 build() 真正用到的那一个方法。"""

    def __init__(self, rows) -> None:
        self._rows = rows

    def active_chunks(self, include_historical: bool = False):
        return list(self._rows)


class StubEmbedding:
    model_name, model_revision, dimension = "stub-embed", "local:stub", 3

    def embed_documents(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


def chunk_rows(count: int = 2) -> list[dict]:
    """能通过 build() 的 active_chunks 行的最小集合。"""
    return [
        {
            "checksum": f"c{index}", "text": f"第 {index} 条制度", "child_chunk_id": f"chunk-{index}",
            "document_id": "doc-1", "heading_path": ["报销"], "logical_document_id": "doc-1",
            "document_version": "v1", "document_status": "active",
        }
        for index in range(count)
    ]


# 注意：``stub_embedding`` fixture **不在这里**，而在 ``tests/conftest.py``。
# 非 conftest 模块定义 fixture 不会进入 pytest 的搜索路径（只有 conftest 与显式
# ``pytest_plugins`` 才会），放这里等于没放 —— 曾经因此让 ``test_acceptance_fixes.py``
# 整体报 "fixture 'stub_embedding' not found"。替身类留在本文件，与文档替身同处。


def make_index_service(database: ProductDatabase, index_root: Path, rows=None):
    from application.index_lifecycle_service import IndexLifecycleService

    return IndexLifecycleService(database, StubDocuments(rows if rows is not None else chunk_rows()), index_root)
