"""P2 验收修复：切分口径门禁的 force 逃生口（**mg 路径** + API 接线）。

背景：P0 修复（m4 manifest 同源）后，历史 m3 索引与新 m4 构建的 schema
必然不同 → 增量重建在存量环境恒 409。拦截是对的（防 09-11 类静默换口径），
但 build()/API 无逃生口 = 用户第一次撞上就无路可走。

force 语义（与 m3 /index/rebuild?force= 完全对齐）：
- 默认拦截，409 detail 携带 gate 报告与重试指引；
- force=true 显式放行——换口径是人的决策（已公布指标失效需重跑基线）。

覆盖范围（别读错文件）
----------------------
本文件覆盖两条**不同的**接线：

1. ``MindGraphIndexService.build(force=...)`` —— **mg 路径**（下面的
   ``_mg_service``）。门禁在 ``_chunking_gate``。
2. ``POST /knowledge/index/incremental-rebuild?force=`` → ``build(force=)``
   的参数透传（见文件末尾的 API 用例）。

**m4 路径**（``IndexLifecycleService``：``build``/``activate``/``_consistency_gate``）
的 force 行为在 ``tests/test_index_consistency_gate.py`` 里 —— 两条路径是同一
语义的两份实现，各测各的，避免"改了一处、另一处静默失效"。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from infrastructure.database import ProductDatabase


@pytest.fixture
def db(tmp_path: Path) -> ProductDatabase:
    database = ProductDatabase(tmp_path / "force.sqlite3")
    database.initialize()
    return database


def _fake_note_rows(db: ProductDatabase, vault_path: str = "doc.md") -> None:
    db.execute(
        "INSERT INTO notes (note_id, vault_path, title, content_hash, frontmatter_json, ai_access_level,"
        " index_status, acl_json, acl_public, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("n1", vault_path, "Doc", "h1", "{}", "public", "pending", "{}", 1, "t", "t"),
    )


class _FakeProvider:
    model_name, model_revision, dimension = "fake", "local:x", 3

    def embed_documents(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


def _mg_service(db: ProductDatabase, vault: Path, index_root: Path):
    from application.mindgraph_index_service import MindGraphIndexService

    return MindGraphIndexService(db, vault, index_root, provider=_FakeProvider())


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    (vault / "doc.md").write_text("---\ntitle: Doc\n---\n## S\n内容\n", encoding="utf-8")
    return vault


def test_m4_force_bypasses_chunking_gate(db: ProductDatabase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """m4：口径变化默认拦截 → force=true 放行激活（逃生口存在且生效）。"""
    from application import chunking_policy as cp
    from domain.errors import IndexConsistencyError
    from infrastructure.settings import get_settings

    vault = _vault(tmp_path)
    _fake_note_rows(db)
    index_root = tmp_path / "idx"
    service = _mg_service(db, vault, index_root)
    service.build(force=True)  # 首建（无 previous，直接成功）

    # build 后 notes 变 ready；第二次构建要真的跑（否则 noop 到不了门禁）
    db.execute("UPDATE notes SET index_status='pending'")

    probe = cp.ChunkingPolicy(name="probe_v2", version="1", child_size=90, parent_size=2000, overlap=10)
    monkeypatch.setitem(cp._PRESETS, "probe_v2", probe)
    monkeypatch.setenv("CHUNKING_POLICY", "probe_v2")
    get_settings.cache_clear()
    try:
        service = _mg_service(db, vault, index_root)
        # 默认拦截
        with pytest.raises(IndexConsistencyError) as exc_info:
            service.build()
        # 409 文案必须带重试指引（P2：用户第一次撞上有路可走）
        assert "force=true" in str(exc_info.value)
        # force 放行
        result = service.build(force=True)
        assert (index_root / "CURRENT").read_text(encoding="utf-8").strip() == result["index_version"]
    finally:
        get_settings.cache_clear()


def test_m4_gate_error_carries_detail(db: ProductDatabase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """409 的 detail 携带 gate 报告（reasons + documents_removed），可机判可审计。"""
    from application import chunking_policy as cp
    from domain.errors import IndexConsistencyError
    from infrastructure.settings import get_settings

    vault = _vault(tmp_path)
    _fake_note_rows(db)
    index_root = tmp_path / "idx2"
    service = _mg_service(db, vault, index_root)
    service.build(force=True)
    db.execute("UPDATE notes SET index_status='pending'")  # 让第二次构建真的执行

    probe = cp.ChunkingPolicy(name="probe_v2", version="1", child_size=90, parent_size=2000, overlap=10)
    monkeypatch.setitem(cp._PRESETS, "probe_v2", probe)
    monkeypatch.setenv("CHUNKING_POLICY", "probe_v2")
    get_settings.cache_clear()
    try:
        service = _mg_service(db, vault, index_root)
        with pytest.raises(IndexConsistencyError) as exc_info:
            service.build()
        detail = exc_info.value.detail or {}
        assert "reasons" in detail and "chunking_changed" in detail["reasons"]
        assert "report" in detail
    finally:
        get_settings.cache_clear()


def test_incremental_rebuild_route_forwards_force(tmp_path: Path) -> None:
    """API 层：``?force=true`` 必须真的传到 ``build(force=True)``。

    只测 service 层的 force 不够——路由把参数丢掉的话，service 的逃生口
    在用户侧等于不存在，而所有 service 层测试仍然全绿。
    同时锁住"空 POST 不得 422"：force 是 Query 参数，既有客户端发空 body
    也必须照常工作（用 Form 时它们会静默 422）。
    """
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import api.dependencies as deps
    from api.routes import knowledge as knowledge_route

    calls: list[bool] = []

    class _Recorder:
        def build(self, *, force: bool = False):
            calls.append(force)
            return {"index_version": "m4-test", "forced": force}

    original = deps._override
    deps._override = SimpleNamespace(index_lifecycle=_Recorder())
    try:
        app = FastAPI()
        app.include_router(knowledge_route.router, prefix="/api/v1")
        client = TestClient(app, raise_server_exceptions=False)

        plain = client.post("/api/v1/knowledge/index/incremental-rebuild")
        forced = client.post("/api/v1/knowledge/index/incremental-rebuild", params={"force": "true"})
    finally:
        deps._override = original

    assert plain.status_code == 200, f"空 POST 必须照常工作（Query 参数字段），实得 {plain.status_code}"
    assert forced.status_code == 200
    assert calls == [False, True], "路由必须把 force 原样传给 build"
    assert forced.json()["forced"] is True
