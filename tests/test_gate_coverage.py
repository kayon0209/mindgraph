"""P2 验收修复：mg- 与 m3- 写 CURRENT 的路径也必须过切分口径门禁。

背景：PR-04 只把门禁接在 m4（IndexLifecycleService.activate）上，而 09-09/
09-11 两次真实事故的路径分别是 m3（静默换口径）与 mg（主动剪枝）——
历史肇事路径恰好都不在门禁管辖内。

语义边界（与 m4 门禁一致）：
- mg 路径**主动剪枝被删除的笔记**，文档丢失是合法变更 → 只拦切分口径变化，
  文档删除交由既有 _report_shrinkage ERROR 告警（不阻断，语义已声明）；
- m3 已有 shrinkage 硬拦截（force 可越过）→ 补的是**切分口径**守卫。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from infrastructure.database import ProductDatabase


@pytest.fixture
def db(tmp_path: Path) -> ProductDatabase:
    database = ProductDatabase(tmp_path / "gate.sqlite3")
    database.initialize()
    return database


def _fake_note_rows(db: ProductDatabase) -> None:
    db.execute(
        "INSERT INTO notes (note_id, vault_path, title, content_hash, frontmatter_json, ai_access_level,"
        " index_status, acl_json, acl_public, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("n1", "doc.md", "Doc", "h1", "{}", "public", "pending", "{}", 1, "t", "t"),
    )


class _FakeProvider:
    model_name, model_revision, dimension = "fake", "local:x", 3

    def embed_documents(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


def _mg_service(db: ProductDatabase, vault: Path, index_root: Path, **kwargs):
    from application.mindgraph_index_service import MindGraphIndexService

    return MindGraphIndexService(db, vault, index_root, provider=_FakeProvider(), **kwargs)


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    (vault / "doc.md").write_text("---\ntitle: Doc\n---\n## S\n内容\n", encoding="utf-8")
    return vault


def test_mg_service_gate_method_exists():
    """MindGraphIndexService 必须有切分口径门禁（此前只有 ERROR 日志不阻断）。"""
    from application.mindgraph_index_service import MindGraphIndexService

    assert hasattr(MindGraphIndexService, "_chunking_gate"), \
        "mg 写 CURRENT 的路径必须过切分口径门禁（历史事故的肇事路径）"


def test_mg_blocks_policy_switch_before_activate(db: ProductDatabase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """mg 激活前：切分口径从 legacy_v1 换成 probe_v2 → 必须阻断，CURRENT 不被改写。"""
    from application import chunking_policy as cp
    from infrastructure.settings import get_settings

    vault = _vault(tmp_path)
    _fake_note_rows(db)
    index_root = tmp_path / "idx"
    service = _mg_service(db, vault, index_root)

    # 第一次构建（legacy 默认）成功激活
    first = service.build(force=True)
    current_file = index_root / "CURRENT"
    assert current_file.read_text(encoding="utf-8").strip() == first["index_version"]

    # 换切分策略（不同参数）→ 同一批笔记重建 → 激活必须被门禁拒绝
    probe = cp.ChunkingPolicy(name="probe_v2", version="1", child_size=90, parent_size=2000, overlap=10)
    monkeypatch.setitem(cp._PRESETS, "probe_v2", probe)
    monkeypatch.setenv("CHUNKING_POLICY", "probe_v2")
    get_settings.cache_clear()
    try:
        service = _mg_service(db, vault, index_root)  # 新实例读新 policy
        with pytest.raises(Exception, match="chunking|口径"):
            service.build(force=True)
        # CURRENT 仍指向旧版本（门禁在改写之前拦截）
        assert current_file.read_text(encoding="utf-8").strip() == first["index_version"]
    finally:
        get_settings.cache_clear()


def test_mg_same_policy_rebuild_not_blocked(db: ProductDatabase, tmp_path: Path):
    """同口径正常重建（笔记更新、重新嵌入）不被误伤。"""
    vault = _vault(tmp_path)
    _fake_note_rows(db)
    index_root = tmp_path / "idx2"
    service = _mg_service(db, vault, index_root)
    first = service.build(force=True)
    second = service.build(force=True)  # 同口径重建
    assert second["index_version"] != first["index_version"]  # 正常重建通过
    assert (index_root / "CURRENT").read_text(encoding="utf-8").strip() == second["index_version"]


def test_m3_rebuild_has_chunking_guard():
    """m3（knowledge_service.rebuild）必须有切分口径守卫——不只是 shrinkage。"""
    import inspect

    from application.knowledge_service import KnowledgeService

    source = inspect.getsource(KnowledgeService.rebuild)
    assert "chunking" in source.lower() and ("gate" in source.lower() or "fingerprint" in source.lower()), \
        "m3 rebuild 必须校验切分口径（历史上正是这条路径静默换掉了证据体系）"
