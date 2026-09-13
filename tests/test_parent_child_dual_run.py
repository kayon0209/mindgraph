"""PR-09 Parent–Child 语义切分双跑：前置修复 + 消费端接入的契约测试。

前置（PR-03 遗留的生效端缺口，PR-09 开工条件）：
- CHUNKING_POLICY 选非 legacy 预设时，``load_corpus`` 链路的**实际切分**
  必须跟随（历史：不传参落回 DEFAULT_CHUNK_SIZE，选谁都是 500/50）；
- ``index_metadata()`` 的 ``chunk_size`` 必须与 ``chunking_policy.child_size``
  同源（历史：两者可以各说各话，双跑差异报告会不可解释）。

消费端（任务书修正 2：parent 产出后检索层零消费）：
- 命中 child 且带 parent lineage 时，按预算用 parent_text 补上下文；
- 默认关闭（feature flag），关闭时检索输出与历史完全一致。
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ── 前置 A：切分参数真正贯通到 load_corpus 生效路径 ────────────────────


def _write_policy_probe_files(docs: Path, size_hint: int) -> None:
    """正文长度 > 任何候选 child_size，使切分块数能区分不同参数。"""
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "doc.md").write_text(
        "## 唯一标题\n" + "报销条款内容。" * (size_hint // 6) + "\n",
        encoding="utf-8",
    )


def _inject_probe_v2(monkeypatch: pytest.MonkeyPatch, *, child_size: int, overlap: int) -> None:
    """注入第二个预设（PR-09 的真实场景：双预设并存）。"""
    from application import chunking_policy as cp

    probe = cp.ChunkingPolicy(name="probe_v2", version="1",
                              child_size=child_size, parent_size=4000, overlap=overlap)
    monkeypatch.setitem(cp._PRESETS, "probe_v2", probe)
    monkeypatch.setenv("CHUNKING_POLICY", "probe_v2")


def test_load_corpus_follows_selected_policy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """选 probe_v2(300/30) 后，实际切分必须产出更小的块——不是只改 manifest。"""
    _inject_probe_v2(monkeypatch, child_size=300, overlap=30)
    from document_loader import load_markdown_chunks

    docs = tmp_path / "docs"
    _write_policy_probe_files(docs, size_hint=900)

    chunks = load_markdown_chunks(docs)  # 不传参 = 从 policy 生效路径取值
    assert chunks, "fixture 不应切出空结果"
    assert max(len(item["text"]) for item in chunks) <= 300, \
        "load_markdown_chunks 未跟随 CHUNKING_POLICY——生效端缺口仍在"


def test_load_all_kb_chunks_follows_selected_policy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """合并入口同样必须跟随（load_corpus 的下一层）。"""
    _inject_probe_v2(monkeypatch, child_size=200, overlap=20)
    from document_loader import load_all_kb_chunks

    docs = tmp_path / "docs"
    _write_policy_probe_files(docs, size_hint=700)
    chunks = load_all_kb_chunks([(docs, "official")])
    assert chunks
    assert max(len(item["text"]) for item in chunks) <= 200


def test_load_corpus_top_level_follows_selected_policy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """retrieval/indexing.load_corpus（m3 文件扫描路径）必须跟随。"""
    _inject_probe_v2(monkeypatch, child_size=250, overlap=25)
    from retrieval.indexing import load_corpus

    docs = tmp_path / "docs"
    _write_policy_probe_files(docs, size_hint=800)
    chunks = load_corpus([(docs, "official")])
    assert chunks
    assert max(len(chunk.text) for chunk in chunks) <= 250


def test_manifest_chunk_size_agrees_with_policy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """index_metadata 的 chunk_size 与 chunking_policy.child_size 必须同源。"""
    _inject_probe_v2(monkeypatch, child_size=333, overlap=30)
    from retrieval.indexing import index_metadata, load_corpus

    docs = tmp_path / "docs"
    _write_policy_probe_files(docs, size_hint=1000)
    chunks = load_corpus([(docs, "official")])
    metadata = index_metadata(chunks)
    assert metadata["chunking_policy"]["child_size"] == 333
    assert metadata["chunk_size"] == 333, "manifest 自相矛盾：chunk_size 与 policy 各说各话"


def test_default_settings_still_legacy(tmp_path: Path):
    """未选策略时行为不变（默认 legacy_v1：500/50 切分照旧）。"""
    from document_loader import load_markdown_chunks

    docs = tmp_path / "docs"
    _write_policy_probe_files(docs, size_hint=1200)
    chunks = load_markdown_chunks(docs)
    assert chunks
    assert max(len(item["text"]) for item in chunks) <= 500


# ── 消费端 B：命中 child 后按预算补 parent 上下文 ──────────────────────


def _chunk_with_parent(child_text: str, parent_text: str, rank: int = 1, parent_id: str = "parent-1"):
    """构造带 parent lineage 的检索候选（m4 路径 metadata 携带 parent_text）。"""
    from retrieval.types import Chunk, RetrievalCandidate

    chunk = Chunk(
        chunk_id=f"child-{rank}", text=child_text, document_id="doc-1",
        chunk_index=rank, section_path="时限",
        metadata={
            "child_chunk_id": f"child-{rank}",
            "parent_chunk_id": parent_id,
            "parent_text": parent_text,
            "heading_path": ["时限"],
        },
    )
    return RetrievalCandidate(chunk=chunk, final_rank=rank, rrf_score=0.9)


def test_expand_to_parent_replaces_child_text_with_budget():
    """parent 扩展：预算内用 parent_text 替换 child 正文，保留 child id 可追溯。"""
    from application.context_expansion import expand_to_parent

    candidates = [_chunk_with_parent("半句续页的后", "第十条 报销应当在出差结束后三十个工作日内提交，其中交通费按标准执行。")]
    expanded, report = expand_to_parent(candidates, max_context_chars=200)
    assert len(expanded) == 1
    assert "三十个工作日内" in expanded[0].chunk.text
    assert expanded[0].chunk.metadata["parent_chunk_id"] == "parent-1"
    assert report["expanded"] == 1
    assert report["reason_codes"] == ["parent_within_budget"]


def test_expand_to_parent_respects_budget():
    """parent 超预算：不扩展并记原因（宁缺毋滥，不截断出误导性半文）。"""
    from application.context_expansion import expand_to_parent

    long_parent = "第" + "十" * 400 + "条 很长的条款"
    candidates = [_chunk_with_parent("child", long_parent)]
    expanded, report = expand_to_parent(candidates, max_context_chars=50)
    assert expanded[0].chunk.text == "child"  # 原文不动
    assert report["expanded"] == 0
    assert report["reason_codes"] == ["parent_exceeds_budget"]


def test_expand_to_parent_no_lineage_passthrough():
    """无 parent lineage（mg-/m3- 路径）：原样通过，不记扩展。"""
    from application.context_expansion import expand_to_parent
    from retrieval.types import Chunk, RetrievalCandidate

    chunk = Chunk(chunk_id="flat-1", text="扁平块", document_id="doc-2",
                  chunk_index=0, section_path=None, metadata={})
    candidates = [RetrievalCandidate(chunk=chunk, final_rank=1, rrf_score=0.8)]
    expanded, report = expand_to_parent(candidates, max_context_chars=200)
    assert expanded[0].chunk.text == "扁平块"
    assert report["expanded"] == 0
    assert report["reason_codes"] == ["no_parent_lineage"]


def test_expand_to_parent_deduplicates_same_parent():
    """同 parent 的多个 child：上下文只补一份，不重复膨胀 prompt。"""
    from application.context_expansion import expand_to_parent

    parent = "第十条 报销应当在出差结束后三十个工作日内提交。"
    candidates = [
        _chunk_with_parent("child-A 片段", parent, rank=1),
        _chunk_with_parent("child-B 片段", parent, rank=2),
    ]
    expanded, report = expand_to_parent(candidates, max_context_chars=500)
    assert report["expanded"] == 1  # 只扩展第一个命中的 child
    assert report["duplicates_skipped"] == 1
    joined = "".join(candidate.chunk.text for candidate in expanded)
    assert joined.count(parent) == 1, "同 parent 文本不得重复进入上下文"
