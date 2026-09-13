"""PR-03 ChunkingPolicy 单一来源：切分参数的契约测试。

任务书测试矩阵：
1. 默认策略（legacy_v1）与修改前行为字节级一致（Markdown 切分 + 结构化切分）；
2. 非法 overlap/size 拒绝；
3. 索引 manifest 包含策略 name/version/parameters。

现场核对补充的锁定点：
- 线上索引构建点（mindgraph_index_service._load_note_chunks）的历史内联字面量
  ``_chunk_text(sec_body, 500, 50)`` 必须经由 policy 取值——散落常量 = 漂移点；
- ``document_loader.DEFAULT_CHUNK_SIZE / DEFAULT_CHUNK_OVERLAP`` 是被
  retrieval/indexing.py 导入的既有契约，导出名与数值不得变化。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ── 测试矩阵 1：默认策略与修改前行为一致 ──


def test_legacy_v1_preset_matches_historical_constants():
    """legacy_v1 = 历史散落常量的精确快照：500/1200/50。"""
    from application.chunking_policy import LEGACY_V1

    assert LEGACY_V1.name == "legacy_v1"
    assert LEGACY_V1.child_size == 500
    assert LEGACY_V1.parent_size == 1200
    assert LEGACY_V1.overlap == 50
    assert LEGACY_V1.version == "1"
    # 不可变：改字段必须失败（防运行时漂移）
    with pytest.raises(Exception):  # FrozenInstanceError
        LEGACY_V1.child_size = 999  # type: ignore[misc]


def test_markdown_chunks_byte_identical_under_default_policy(tmp_path: Path):
    """document_loader 的输出在 legacy_v1 下与历史常量直接调用完全一致。"""
    from application.chunking_policy import LEGACY_V1
    from document_loader import load_markdown_chunks

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "policy.md").write_text(
        "## 时限\n" + "出差结束后10个工作日内办理报销。" * 40 + "\n\n## 交通\n飞机经济舱。\n",
        encoding="utf-8",
    )

    # 经 policy 参数（新路径）
    via_policy = load_markdown_chunks(
        docs, chunk_size=LEGACY_V1.child_size, chunk_overlap=LEGACY_V1.overlap,
    )
    # 历史默认（不传参 = 等价旧行为）
    via_defaults = load_markdown_chunks(docs)

    assert via_policy == via_defaults
    assert via_policy, "fixture 不应切出空结果"
    # 具体切分数值快照：680 字正文按 500/50 切 2 块 + 「交通」1 块 = 3
    assert len(via_policy) == 3
    assert via_policy[0]["metadata"]["section_path"] == "时限"


def test_structured_chunker_default_matches_legacy_constants():
    """StructuredChunker() 默认实例化 = legacy_v1 参数（1200 parent 容纳）。"""
    from application.chunking_policy import LEGACY_V1
    from application.structured_chunker import StructuredChunker

    chunker = StructuredChunker()
    assert chunker.child_size == LEGACY_V1.child_size
    assert chunker.parent_size == LEGACY_V1.parent_size
    assert chunker.overlap == LEGACY_V1.overlap


def test_document_loader_constants_unchanged():
    """既有导出契约：常量名与数值不变（retrieval/indexing.py 依赖它们）。"""
    import document_loader

    assert document_loader.DEFAULT_CHUNK_SIZE == 500
    assert document_loader.DEFAULT_CHUNK_OVERLAP == 50


# ── 测试矩阵 2：非法参数拒绝 ──


@pytest.mark.parametrize(
    "kwargs",
    [
        {"child_size": 0, "parent_size": 1200, "overlap": 50},
        {"child_size": -1, "parent_size": 1200, "overlap": 50},
        {"child_size": 500, "parent_size": 0, "overlap": 50},
        {"child_size": 50, "parent_size": 1200, "overlap": 50},  # overlap >= child
        {"child_size": 500, "parent_size": 1200, "overlap": -1},
    ],
)
def test_invalid_policy_parameters_rejected(kwargs):
    from application.chunking_policy import ChunkingPolicy

    with pytest.raises(ValueError):
        ChunkingPolicy(name="bad", version="1", **kwargs)


def test_unknown_policy_name_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """配置选择策略：未知名拒绝，不静默回落（fail-closed）。"""
    from application.chunking_policy import ChunkingPolicy

    monkeypatch.setenv("CHUNKING_POLICY", "does_not_exist")
    with pytest.raises(ValueError, match="unknown"):
        ChunkingPolicy.from_settings()


def test_known_policy_name_selectable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """可通过配置选择策略；默认（未配置）仍 legacy_v1。"""
    from application.chunking_policy import LEGACY_V1, ChunkingPolicy

    monkeypatch.setenv("CHUNKING_POLICY", "legacy_v1")
    assert ChunkingPolicy.from_settings() == LEGACY_V1
    monkeypatch.delenv("CHUNKING_POLICY")
    assert ChunkingPolicy.from_settings() == LEGACY_V1


# ── 测试矩阵 3：manifest 记录策略版本 ──


def test_indexing_manifest_contains_policy(tmp_path: Path):
    """m3 文件扫描路径的 manifest 必须含 chunking_policy 块。"""
    from retrieval.indexing import corpus_hash, index_metadata, load_corpus

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("## A\n内容甲\n", encoding="utf-8")
    chunks = load_corpus([(docs, "official")])  # index_metadata 契约输入是 Chunk 对象
    metadata = index_metadata(chunks)
    assert metadata["chunking_policy"]["name"] == "legacy_v1"
    assert metadata["chunking_policy"]["version"] == "1"
    assert metadata["chunking_policy"]["child_size"] == 500
    assert metadata["chunking_policy"]["overlap"] == 50
    assert metadata["corpus_sha256"] == corpus_hash(chunks)


def test_mindgraph_index_manifest_contains_policy(tmp_path: Path):
    """线上索引（mg-）manifest 必须含 chunking_policy 块。"""
    from application.mindgraph_index_service import MindGraphIndexService
    from infrastructure.database import ProductDatabase

    db = ProductDatabase(tmp_path / "mg-policy.sqlite3")
    db.initialize()
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "n1.md").write_text("---\ntitle: N1\n---\n## S1\n内容乙\n", encoding="utf-8")
    db.execute(
        "INSERT INTO notes (note_id, vault_path, title, content_hash, index_status, ai_access_level,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        ("note-1", "n1.md", "N1", "hash-x", "pending", "public", "2026-01-01", "2026-01-01"),
    )

    class FakeProvider:
        model_name, model_revision, dimension = "fake", "local:x", 3

        def embed_documents(self, texts):
            return [[0.1, 0.2, 0.3] for _ in texts]

    service = MindGraphIndexService(db, vault, tmp_path / "indexes", provider=FakeProvider())
    manifest = service.build(force=True)
    policy = manifest["chunking_policy"]
    assert policy["name"] == "legacy_v1"
    assert policy["child_size"] == 500
    assert policy["parent_size"] == 1200
    assert policy["overlap"] == 50
    # 写盘的 manifest 与返回值一致
    on_disk = json.loads(
        (tmp_path / "indexes" / manifest["index_version"] / "manifest.json").read_text(encoding="utf-8")
    )
    assert on_disk["chunking_policy"] == policy


# ── 单一来源锁定（现场核对修正 1 的回归）──


def test_note_chunking_reads_policy_not_inline_literals(tmp_path: Path):
    """线上索引构建点必须从 policy 取切分参数。

    用 policy 注入一个可区分的参数构造 service，验证 _load_note_chunks 的
    切分边界跟随 policy——否则内联字面量仍在偷偷生效。
    """
    from application.chunking_policy import ChunkingPolicy
    from application.mindgraph_index_service import MindGraphIndexService
    from infrastructure.database import ProductDatabase

    db = ProductDatabase(tmp_path / "mg-inline.sqlite3")
    db.initialize()
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "n1.md").write_text(
        "---\ntitle: N1\n---\n## S\n" + "字" * 80 + "\n", encoding="utf-8"
    )
    db.execute(
        "INSERT INTO notes (note_id, vault_path, title, content_hash, index_status, ai_access_level,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        ("note-1", "n1.md", "N1", "hash-y", "pending", "public", "2026-01-01", "2026-01-01"),
    )

    class FakeProvider:
        model_name, model_revision, dimension = "fake", "local:x", 3

        def embed_documents(self, texts):
            return [[0.1, 0.2, 0.3] for _ in texts]

    tiny = ChunkingPolicy(name="tiny_probe", version="1", child_size=20, parent_size=2000, overlap=5)
    service = MindGraphIndexService(db, vault, tmp_path / "idx2", provider=FakeProvider(), policy=tiny)
    chunks = service._load_note_chunks(db.fetch_one("SELECT * FROM notes WHERE note_id='note-1'"))
    texts = [chunk.text for chunk in chunks]
    # 80 字正文按 child_size=20 切必须产生 >1 个 chunk；若内联 500 仍在，只会是 1 个
    assert len(texts) > 1, "切分边界未跟随 policy——内联字面量仍在生效"
    assert max(len(t) for t in texts) <= 20
