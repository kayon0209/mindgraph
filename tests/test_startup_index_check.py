"""启动自检必须用 MindGraph 索引根来对照 ``notes`` 表。

背景：``_warn_if_index_diverges`` 原先用 ``INDEX_ROOT``（``retrieval_indexes``，
索引 ``knowledge/`` 语料 4 篇）去对照 ``notes`` 表（vault 笔记 25 篇）——两套语料
本就是**两个命名空间**（``application/index_snapshot.py`` 明确写了"不跨根比较"），
于是每次启动都打 ``index_corpus_divergence`` ERROR。

同一 ``db_path`` 下实测：

    retrieval_indexes  → declared=25 indexed=5  undeclared=1  → divergence
    mindgraph_indexes  → declared=25 indexed=25 undeclared=0  → consistent

长期恒真的告警会训练运维忽略启动日志，真正的事故反而藏进去（该函数自己的 docstring
就是这么写的），所以这个错配必须锁住。
"""

from __future__ import annotations

from infrastructure.retrieval_factory import INDEX_ROOT, MINDGRAPH_INDEX_ROOT


def test_two_index_roots_are_distinct_namespaces() -> None:
    assert INDEX_ROOT.name == "retrieval_indexes"
    assert MINDGRAPH_INDEX_ROOT.name == "mindgraph_indexes"
    assert INDEX_ROOT != MINDGRAPH_INDEX_ROOT


def test_startup_consistency_check_targets_mindgraph_index_root(monkeypatch) -> None:
    """自检实际传给 ``audit_index_consistency`` 的必须是 MindGraph 索引根。"""
    import application.index_metadata as index_metadata

    captured: dict[str, object] = {}

    def _fake_audit(*, index_root, db_path, included_subtrees):  # noqa: ANN001
        captured["index_root"] = index_root
        return {"consistent": True}

    monkeypatch.setattr(index_metadata, "audit_index_consistency", _fake_audit)

    from api import main as api_main

    api_main._warn_if_index_diverges()

    assert captured.get("index_root") == MINDGRAPH_INDEX_ROOT
