"""PR-04｜全量与增量切分一致性门禁。

锁住的真实事故（2026-09-11 实测，非假设）
----------------------------------------
``data/retrieval_indexes/`` 里 **69 chunks 与 98 chunks 两个版本并存**，
且 ``CURRENT`` 在它们之间被切换过：

===========================  =======  ==========================================
版本                          chunk    切分口径
===========================  =======  ==========================================
``m3-20260910T073532Z-…``        69   ``chunk_size=500/overlap=50``（扁平）
``m4-20260909T123040Z-…``        98   ``chunker{500,1200,50}``（StructuredChunker）
===========================  =======  ==========================================

**这不是参数漂移，是同一语料的两条切分路径**——69 与 98 的 chunk_id 命名空间
完全不同（``差旅费报销管理办法.md::6`` vs 32 位 hex），一次切换 = 换掉全部证据 ID，
而已公布的检索指标（R@5 0.587）只对应其中一套。切换当时**没有任何机制阻止**。

本文件的职责：让「未经认可的口径切换」在 **CURRENT 被改写之前** 变成硬失败。

测试矩阵（任务书）
------------------
1. 正常全量/增量一致 → 放行；
2. 缺文档、改参数、漏 metadata 均能被捕获；
3. 旧 CURRENT 在失败后保持不变。
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from application.index_snapshot import (
    chunking_fingerprint,
    compare_snapshots,
    evaluate_activation_gate,
    load_snapshot,
)
from domain.errors import IndexConsistencyError
from index_snapshot_fixture import (
    STYLE_ABSENT,
    STYLE_CHUNKER,
    STYLE_FLAT,
    STYLE_POLICY,
    activate,
    build_snapshot_dir,
)
from infrastructure.database import ProductDatabase, dumps
from application.document_lifecycle_service import DocumentLifecycleService
from application.index_lifecycle_service import IndexLifecycleService

DOCS = {"a.md": 3, "b.md": 2, "c.md": 4}  # 9 chunks


# ── 矩阵 1：切分口径提取必须容忍全部四种 manifest 形态 ──────────────────────


def test_fingerprint_reads_flat_schema():
    """m2/m3：chunk_size + chunk_overlap。"""
    fp = chunking_fingerprint({"chunk_size": 500, "chunk_overlap": 50})
    assert fp["child_size"] == 500 and fp["overlap"] == 50
    assert fp["comparable"] is True


def test_fingerprint_reads_chunker_schema():
    """m4：chunker{child_size,parent_size,overlap}。"""
    fp = chunking_fingerprint({"chunker": {"child_size": 500, "parent_size": 1200, "overlap": 50}})
    assert fp["child_size"] == 500 and fp["parent_size"] == 1200 and fp["overlap"] == 50
    assert fp["comparable"] is True


def test_fingerprint_reads_policy_schema():
    """PR-03 起的新构建：chunking_policy（优先于同 manifest 里的旧字段）。"""
    fp = chunking_fingerprint({
        "chunk_size": 500,
        "chunking_policy": {"name": "legacy_v1", "child_size": 800, "parent_size": 2000, "overlap": 100},
    })
    assert fp["child_size"] == 800 and fp["overlap"] == 100


def test_fingerprint_absent_is_not_comparable():
    """老 mg 索引没有切分字段 → 不可比，**但不许冒充成 500**。"""
    fp = chunking_fingerprint({"index_version": "mg-x", "chunk_count": 581})
    assert fp["comparable"] is False
    assert fp["child_size"] is None


def test_fingerprint_tolerates_none_and_partial():
    assert chunking_fingerprint(None)["comparable"] is False
    # 只有 size 没有 overlap：仍然可比 size，但整体标记不完整
    fp = chunking_fingerprint({"chunk_size": 500})
    assert fp["child_size"] == 500 and fp["overlap"] is None


# ── 快照加载：缺 metadata 是常态，要跳过并计数，不许抛异常 ──────────────────


def test_load_snapshot_collects_chunks_and_documents(tmp_path: Path):
    build_snapshot_dir(tmp_path, "v1", documents=DOCS, style=STYLE_FLAT)
    snap = load_snapshot(tmp_path, "v1")
    assert snap is not None
    assert snap["chunk_count"] == 9
    assert len(snap["chunk_ids"]) == 9
    assert snap["document_keys"] == {"a.md", "b.md", "c.md"}
    assert snap["metadata_missing"] is False


def test_load_snapshot_flags_missing_metadata(tmp_path: Path):
    """全仓有 4 个版本目录没有 metadata.json —— 必须能读 chunk、标记缺失。"""
    build_snapshot_dir(tmp_path, "v1", documents=DOCS, write_metadata=False)
    snap = load_snapshot(tmp_path, "v1")
    assert snap is not None
    assert snap["metadata_missing"] is True
    assert snap["chunk_count"] == 9  # chunk 仍在，只是没 manifest


def test_load_snapshot_returns_none_when_no_chunks(tmp_path: Path):
    (tmp_path / "v1").mkdir()
    assert load_snapshot(tmp_path, "v1") is None


# ── 矩阵 2：比较器 ────────────────────────────────────────────────────────


def test_compare_identical_is_clean(tmp_path: Path):
    build_snapshot_dir(tmp_path, "v1", documents=DOCS, style=STYLE_FLAT)
    build_snapshot_dir(tmp_path, "v2", documents=DOCS, style=STYLE_FLAT)
    report = compare_snapshots(load_snapshot(tmp_path, "v1"), load_snapshot(tmp_path, "v2"))
    assert report["chunking_changed"] is False
    assert report["documents_removed"] == []
    assert report["chunk_ids_removed"] == 0 and report["chunk_ids_added"] == 0


def test_compare_detects_chunking_change(tmp_path: Path):
    build_snapshot_dir(tmp_path, "v1", documents=DOCS, style=STYLE_FLAT, child_size=500)
    build_snapshot_dir(tmp_path, "v2", documents=DOCS, style=STYLE_FLAT, child_size=800, overlap=100)
    report = compare_snapshots(load_snapshot(tmp_path, "v1"), load_snapshot(tmp_path, "v2"))
    assert report["chunking_changed"] is True
    assert report["chunking"]["previous"]["child_size"] == 500
    assert report["chunking"]["candidate"]["child_size"] == 800


def test_compare_detects_document_loss(tmp_path: Path):
    build_snapshot_dir(tmp_path, "v1", documents=DOCS, style=STYLE_FLAT)
    build_snapshot_dir(tmp_path, "v2", documents={"a.md": 3, "b.md": 2}, style=STYLE_FLAT)
    report = compare_snapshots(load_snapshot(tmp_path, "v1"), load_snapshot(tmp_path, "v2"))
    assert report["documents_removed"] == ["c.md"]


def test_compare_is_order_insensitive(tmp_path: Path):
    """顺序无关：同样的 chunk 集，写盘顺序不同也算一致。"""
    build_snapshot_dir(tmp_path, "v1", documents={"a.md": 2, "b.md": 2}, style=STYLE_FLAT)
    build_snapshot_dir(tmp_path, "v2", documents={"b.md": 2, "a.md": 2}, style=STYLE_FLAT)
    report = compare_snapshots(load_snapshot(tmp_path, "v1"), load_snapshot(tmp_path, "v2"))
    assert report["chunk_ids_added"] == 0 and report["chunk_ids_removed"] == 0


def test_compare_detects_disjoint_chunk_namespace(tmp_path: Path):
    """69 vs 98 的真实形态：同一批文档、同一批参数，但 chunk_id 完全换了一套。"""
    build_snapshot_dir(tmp_path, "m3", documents=DOCS, style=STYLE_FLAT, id_style="document")
    build_snapshot_dir(tmp_path, "m4", documents=DOCS, style=STYLE_CHUNKER, id_style="hex")
    report = compare_snapshots(load_snapshot(tmp_path, "m3"), load_snapshot(tmp_path, "m4"))
    # 数值都是 500/50，光比数值抓不到；差异在**构建入口**（schema）→ 算口径变化。
    # 这正是「69 vs 98 不是参数漂移」的编码。
    assert report["chunking_changed"] is True
    assert report["chunk_overlap_ratio"] == 0.0
    assert report["chunk_namespace_disjoint"] is True


def test_compare_marks_incomparable_chunking(tmp_path: Path):
    """一方没有切分字段（老 mg）→ 不做切分比较，而不是拿 None 去比。"""
    build_snapshot_dir(tmp_path, "mg-old", documents=DOCS, style=STYLE_ABSENT)
    build_snapshot_dir(tmp_path, "mg-new", documents=DOCS, style=STYLE_POLICY)
    report = compare_snapshots(load_snapshot(tmp_path, "mg-old"), load_snapshot(tmp_path, "mg-new"))
    assert report["chunking_comparable"] is False
    assert report["chunking_changed"] is False  # 不可比 ≠ 变了


# ── 矩阵 3：门禁结论 ──────────────────────────────────────────────────────


def test_gate_first_build_not_blocked(tmp_path: Path):
    """没有 previous（首次构建）→ 无从比较 → 放行，不冒充判断。"""
    build_snapshot_dir(tmp_path, "v1", documents=DOCS, style=STYLE_FLAT)
    gate = evaluate_activation_gate(None, load_snapshot(tmp_path, "v1"))
    assert gate["blocked"] is False
    assert "no_previous" in gate["reasons"]


def test_gate_blocks_chunking_change(tmp_path: Path):
    build_snapshot_dir(tmp_path, "v1", documents=DOCS, style=STYLE_FLAT, child_size=500)
    build_snapshot_dir(tmp_path, "v2", documents=DOCS, style=STYLE_FLAT, child_size=800, overlap=100)
    gate = evaluate_activation_gate(load_snapshot(tmp_path, "v1"), load_snapshot(tmp_path, "v2"))
    assert gate["blocked"] is True
    assert "chunking_changed" in gate["reasons"]


def test_gate_allows_chunking_change_when_explicitly_permitted(tmp_path: Path):
    """口径取舍属产品决策 —— 产品认可后必须能放行（任务书修正 4）。"""
    build_snapshot_dir(tmp_path, "v1", documents=DOCS, style=STYLE_FLAT, child_size=500)
    build_snapshot_dir(tmp_path, "v2", documents=DOCS, style=STYLE_FLAT, child_size=800, overlap=100)
    gate = evaluate_activation_gate(
        load_snapshot(tmp_path, "v1"), load_snapshot(tmp_path, "v2"), allow_chunking_change=True
    )
    assert gate["blocked"] is False


def test_gate_blocks_disjoint_chunk_namespace(tmp_path: Path):
    build_snapshot_dir(tmp_path, "m3", documents=DOCS, style=STYLE_FLAT, id_style="document")
    build_snapshot_dir(tmp_path, "m4", documents=DOCS, style=STYLE_CHUNKER, id_style="hex")
    gate = evaluate_activation_gate(load_snapshot(tmp_path, "m3"), load_snapshot(tmp_path, "m4"))
    assert gate["blocked"] is True
    # 按**口径变化**阻断（schema 不同）；命名空间不相交只作诊断，不单独阻断——
    # 否则"文档换版本导致 chunk_id 全变"这种正常重建会被误伤。
    assert "chunking_changed" in gate["reasons"]
    assert gate["report"]["chunk_namespace_disjoint"] is True


def test_gate_blocks_unexplained_document_loss(tmp_path: Path):
    build_snapshot_dir(tmp_path, "v1", documents=DOCS, style=STYLE_FLAT)
    build_snapshot_dir(tmp_path, "v2", documents={"a.md": 3}, style=STYLE_FLAT)
    gate = evaluate_activation_gate(load_snapshot(tmp_path, "v1"), load_snapshot(tmp_path, "v2"))
    assert gate["blocked"] is True
    assert "documents_removed" in gate["reasons"]


def test_gate_does_not_block_on_missing_metadata_alone(tmp_path: Path):
    """缺 manifest 不该变成"什么都激活不了"——只降级为不可比。"""
    build_snapshot_dir(tmp_path, "v1", documents=DOCS, write_metadata=False)
    build_snapshot_dir(tmp_path, "v2", documents=DOCS, style=STYLE_FLAT)
    gate = evaluate_activation_gate(load_snapshot(tmp_path, "v1"), load_snapshot(tmp_path, "v2"))
    assert gate["blocked"] is False


def test_gate_blocks_when_candidate_unreadable(tmp_path: Path):
    build_snapshot_dir(tmp_path, "v1", documents=DOCS, style=STYLE_FLAT)
    gate = evaluate_activation_gate(load_snapshot(tmp_path, "v1"), None)
    assert gate["blocked"] is True
    assert "candidate_unreadable" in gate["reasons"]


def test_gate_report_is_minimal_and_actionable(tmp_path: Path):
    """差异报告要能直接看出"哪篇丢了、参数从几变到几"，不是只报总数。"""
    build_snapshot_dir(tmp_path, "v1", documents=DOCS, style=STYLE_FLAT, child_size=500)
    build_snapshot_dir(tmp_path, "v2", documents={"a.md": 3}, style=STYLE_FLAT, child_size=800)
    gate = evaluate_activation_gate(load_snapshot(tmp_path, "v1"), load_snapshot(tmp_path, "v2"))
    report = gate["report"]
    assert report["documents_removed"] == ["b.md", "c.md"]
    assert report["chunking"]["previous"]["child_size"] == 500
    assert report["chunking"]["candidate"]["child_size"] == 800


# ── 集成：门禁真的接在 CURRENT 改写之前（矩阵 3 最后一条）────────────────


def _lifecycle(tmp_path: Path) -> IndexLifecycleService:
    db = ProductDatabase(tmp_path / "product.sqlite3")
    db.initialize()
    documents = DocumentLifecycleService(db, tmp_path / "documents")
    return db, IndexLifecycleService(db, documents, tmp_path / "indexes")


def _register(db: ProductDatabase, version: str, previous: str | None) -> None:
    now = datetime.now(UTC).isoformat()
    db.execute(
        "INSERT OR REPLACE INTO index_builds VALUES (?,?,?,?,?,?,?)",
        (version, "validated", dumps({"index_version": version}), previous, now, None, None),
    )


def test_activation_blocked_keeps_current_unchanged(tmp_path: Path):
    """核心保障：门禁拒绝时，CURRENT **必须**还是旧版本。"""
    db, indexes = _lifecycle(tmp_path)
    root = tmp_path / "indexes"
    build_snapshot_dir(root, "m3-old", documents=DOCS, style=STYLE_FLAT, id_style="document")
    build_snapshot_dir(root, "m4-new", documents=DOCS, style=STYLE_CHUNKER, id_style="hex")
    activate(root, "m3-old")
    _register(db, "m3-old", None)
    _register(db, "m4-new", "m3-old")

    with pytest.raises(IndexConsistencyError):
        indexes.activate("m4-new")
    assert (root / "CURRENT").read_text(encoding="utf-8").strip() == "m3-old"


def test_activation_proceeds_when_consistent(tmp_path: Path):
    db, indexes = _lifecycle(tmp_path)
    root = tmp_path / "indexes"
    build_snapshot_dir(root, "v1", documents=DOCS, style=STYLE_FLAT)
    build_snapshot_dir(root, "v2", documents=DOCS, style=STYLE_FLAT)
    activate(root, "v1")
    _register(db, "v1", None)
    _register(db, "v2", "v1")

    indexes.activate("v2", reason="consistent rebuild")
    assert (root / "CURRENT").read_text(encoding="utf-8").strip() == "v2"


def test_gate_can_be_disabled_by_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """回滚路径：关掉 flag 即恢复旧激活流程（任务书「回滚」）。"""
    db, indexes = _lifecycle(tmp_path)
    root = tmp_path / "indexes"
    build_snapshot_dir(root, "m3-old", documents=DOCS, style=STYLE_FLAT, id_style="document")
    build_snapshot_dir(root, "m4-new", documents=DOCS, style=STYLE_CHUNKER, id_style="hex")
    activate(root, "m3-old")
    _register(db, "m3-old", None)
    _register(db, "m4-new", "m3-old")

    monkeypatch.setenv("INDEX_CONSISTENCY_GATE", "false")
    from infrastructure.settings import get_settings

    get_settings.cache_clear()
    indexes.activate("m4-new")  # 不再阻断
    assert (root / "CURRENT").read_text(encoding="utf-8").strip() == "m4-new"


# ── P2：force 逃生口（m4 路径）────────────────────────────────────────────
# 注意分工：tests/test_gate_force_escape.py 覆盖的是 **mg** 路径
# （MindGraphIndexService），本文件覆盖 **m4**（IndexLifecycleService）。
# 两条路径的门禁是同一语义的两份实现——只测一条，另一条在重构后会静默
# 失去逃生口，而"打不开的逃生口"与"没有逃生口"在用户侧是同一种绝望。


def test_activation_force_allows_blocked_chunking_change(tmp_path: Path):
    """先拦后放：默认仍 fail-closed，force=true 才改 CURRENT，且错误可机判。"""
    db, indexes = _lifecycle(tmp_path)
    root = tmp_path / "indexes"
    build_snapshot_dir(root, "m3-old", documents=DOCS, style=STYLE_FLAT, id_style="document")
    build_snapshot_dir(root, "m4-new", documents=DOCS, style=STYLE_CHUNKER, id_style="hex")
    activate(root, "m3-old")
    _register(db, "m3-old", None)
    _register(db, "m4-new", "m3-old")

    with pytest.raises(IndexConsistencyError) as exc_info:
        indexes.activate("m4-new")
    assert (root / "CURRENT").read_text(encoding="utf-8").strip() == "m3-old"  # 拒绝时不改 CURRENT
    # 门禁类错误必须有自己的 code：靠 409+文案区分不了"缩水拦截"和"口径拦截"
    assert exc_info.value.code == "index_consistency_blocked"
    assert "force=true" in str(exc_info.value), "第一次撞上的人必须知道有逃生口"
    assert exc_info.value.detail and exc_info.value.detail["reasons"]

    indexes.activate("m4-new", reason="operator confirmed chunking change", force=True)
    assert (root / "CURRENT").read_text(encoding="utf-8").strip() == "m4-new"


class _StubDocuments:
    """只提供 build() 真正用到的方法——为测 force 透传，不必建整套文档生命周期。"""

    def __init__(self, rows) -> None:
        self._rows = rows

    def active_chunks(self, include_historical: bool = False):
        return list(self._rows)


class _StubEmbedding:
    model_name, model_revision, dimension = "stub-embed", "local:stub", 3

    def embed_documents(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


def _chunk_rows(count: int = 2):
    return [
        {
            "checksum": f"c{index}", "text": f"第 {index} 条制度", "child_chunk_id": f"chunk-{index}",
            "document_id": "doc-1", "heading_path": ["报销"], "logical_document_id": "doc-1",
            "document_version": "v1", "document_status": "active",
        }
        for index in range(count)
    ]


def test_build_force_reaches_activate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """build(force=True) 必须把 force 一路传到 activate。

    否则「API 有逃生口」是假的：路由确实收到了 force，但 build 内部吞掉它，
    用户第二次调用仍然 409——最坏的一种修法（看起来修了）。
    """
    from application import chunking_policy as cp
    from infrastructure.settings import get_settings

    monkeypatch.setattr("application.index_lifecycle_service.BGEEmbeddingProvider", _StubEmbedding)
    db = ProductDatabase(tmp_path / "build.sqlite3")
    db.initialize()
    root = tmp_path / "idx"
    indexes = IndexLifecycleService(db, _StubDocuments(_chunk_rows()), root)
    first = indexes.build()

    probe = cp.ChunkingPolicy(name="probe_v2", version="1", child_size=90, parent_size=2000, overlap=10)
    monkeypatch.setitem(cp._PRESETS, "probe_v2", probe)
    monkeypatch.setenv("CHUNKING_POLICY", "probe_v2")
    get_settings.cache_clear()
    try:
        with pytest.raises(IndexConsistencyError):
            indexes.build()
        assert (root / "CURRENT").read_text(encoding="utf-8").strip() == first["index_version"]
        forced = indexes.build(force=True)
        assert forced["index_version"] != first["index_version"]
        assert (root / "CURRENT").read_text(encoding="utf-8").strip() == forced["index_version"]
        assert forced["chunking_policy"]["child_size"] == 90  # 确实是新口径
    finally:
        get_settings.cache_clear()
