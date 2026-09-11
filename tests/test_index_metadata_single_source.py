"""索引元数据单一事实源 + 缩水准入守卫的回归测试（2026-09-10 P0）。

锁住三个真实事故面：

1. ``m3-`` 文件扫描路径产出的 chunk 没有任何 ACL / 命名空间字段，一旦它的版本
   成为 ``CURRENT``，按源过滤与按权限过滤会同时静默失效；
2. 那次重建把 25 篇元数据齐全的索引静默换成 4 篇薄元数据索引；
3. ``notes`` 表声明与活跃索引实际覆盖的文档分叉时，全链路没有任何提示。

测试全部使用临时目录与临时 SQLite，不触碰业务库。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from application import knowledge_service as ks
from application.index_metadata import (
    audit_index_consistency,
    classify_divergence,
    document_key,
    document_subtree,
    enrich_records_with_notes,
    evaluate_index_shrinkage,
    index_document_keys,
    is_document_in_scope,
    load_note_index,
    parse_included_subtrees,
)
from domain.errors import IndexShrinkageError


def _make_notes_db(path: Path, rows: list[dict]) -> Path:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE notes ("
        "note_id TEXT, vault_path TEXT, source_path TEXT, title TEXT, "
        "ai_access_level TEXT, workspace TEXT, department TEXT, acl_json TEXT, "
        "acl_public INTEGER, source_id TEXT, policy_status TEXT)"
    )
    for row in rows:
        connection.execute(
            "INSERT INTO notes VALUES (:note_id,:vault_path,:source_path,:title,"
            ":ai_access_level,:workspace,:department,:acl_json,:acl_public,:source_id,:policy_status)",
            {
                "note_id": row.get("note_id", "n1"),
                "vault_path": row.get("vault_path"),
                "source_path": row.get("source_path", row.get("vault_path")),
                "title": row.get("title", ""),
                "ai_access_level": row.get("ai_access_level", "local_only"),
                "workspace": row.get("workspace", "knowledge"),
                "department": row.get("department"),
                "acl_json": row.get("acl_json", '{"workspace": "knowledge"}'),
                "acl_public": row.get("acl_public", 0),
                "source_id": row.get("source_id", "D:/demo/mindgraph/knowledge"),
                "policy_status": row.get("policy_status", "active"),
            },
        )
    connection.commit()
    connection.close()
    return path


# ── 1. 文档键归一：三个 builder 的路径基准不同 ──────────────────────────────


def test_document_key_normalizes_across_builder_path_conventions() -> None:
    """m3- 的 relative_path 相对项目根，notes 的 vault_path 相对 vault 根。"""
    assert document_key({"relative_path": "knowledge\\差旅费报销管理办法.md"}) == "差旅费报销管理办法.md"
    assert document_key({"vault_path": "差旅费报销管理办法.md"}) == "差旅费报销管理办法.md"
    assert document_key({"source_path": "external/public/gitlab.md"}) == "external/public/gitlab.md"
    # 优先级：vault_path > relative_path > source_path > doc_name
    assert document_key({"vault_path": "a.md", "relative_path": "knowledge/b.md"}) == "a.md"
    assert document_key({}) is None


# ── 2. 元数据注入 ──────────────────────────────────────────────────────────


def test_enrich_injects_acl_and_namespace_fields_from_notes(tmp_path: Path) -> None:
    db = _make_notes_db(
        tmp_path / "product.sqlite3",
        [{"vault_path": "差旅费报销管理办法.md", "workspace": "knowledge",
          "acl_json": '{"workspace": "knowledge", "allow": ["workspace:knowledge"]}'}],
    )
    note_index = load_note_index(db)
    records = [{"metadata": {"relative_path": "knowledge\\差旅费报销管理办法.md", "doc_name": "差旅费报销管理办法.md"}}]

    report = enrich_records_with_notes(records, note_index)

    assert (report.total, report.matched, report.unmatched) == (1, 1, 0)
    assert report.coverage == 1.0
    metadata = records[0]["metadata"]
    assert metadata["workspace"] == "knowledge"
    assert metadata["acl_json"] == '{"workspace": "knowledge", "allow": ["workspace:knowledge"]}'
    assert metadata["acl_public"] is False
    assert metadata["vault_path"] == "差旅费报销管理办法.md"  # 归一后的键
    assert metadata["metadata_source"] == "notes"
    # 不改正文/分块字段，召回不受影响
    assert metadata["doc_name"] == "差旅费报销管理办法.md"


def test_enrich_marks_unattributed_documents_instead_of_silently_passing(tmp_path: Path) -> None:
    db = _make_notes_db(tmp_path / "product.sqlite3", [{"vault_path": "known.md"}])
    note_index = load_note_index(db)
    records = [{"metadata": {"relative_path": "knowledge/unknown.md"}}, {"metadata": {"relative_path": "known.md"}}]

    report = enrich_records_with_notes(records, note_index)

    assert report.matched == 1 and report.unmatched == 1
    assert report.unmatched_samples == ["unknown.md"]
    assert records[0]["metadata"]["metadata_source"] == "filesystem_only"
    assert "workspace" not in records[0]["metadata"]


def test_enrich_never_injects_retrieval_gating_fields(tmp_path: Path) -> None:
    """状态/日期字段直接参与检索准入，不能在这个模块顺手注入。

    实测教训（2026-09-10）：往 m3 索引注入 ``policy_status='unspecified'`` 后，
    本机 4 篇中文制度整批不可检索（``dense.py`` 里
    ``status = document_status or policy_status``，非 active 直接丢弃），
    召回从 5 条掉到 0 条。这份测试把边界钉死。
    """
    from application.index_metadata import NAMESPACE_FIELDS, RETRIEVAL_GATING_FIELDS

    assert not set(NAMESPACE_FIELDS) & set(RETRIEVAL_GATING_FIELDS)

    db = _make_notes_db(tmp_path / "product.sqlite3", [{"vault_path": "a.md", "policy_status": "unspecified"}])
    records = [{"metadata": {"relative_path": "knowledge/a.md"}}]

    enrich_records_with_notes(records, load_note_index(db))

    injected = set(records[0]["metadata"])
    assert injected & set(RETRIEVAL_GATING_FIELDS) == set()
    assert "workspace" in injected  # ACL / 命名空间仍然照常注入


def test_note_index_degrades_without_raising_when_source_missing(tmp_path: Path) -> None:
    missing = load_note_index(tmp_path / "nope.sqlite3")
    assert missing.available is False and missing.reason == "database_file_missing"
    assert load_note_index(None).available is False

    records = [{"metadata": {"relative_path": "a.md"}}]
    report = enrich_records_with_notes(records, missing)
    assert report.source_available is False
    assert records[0]["metadata"]["metadata_source"] == "filesystem_only"


# ── 3. 缩水准入守卫 ───────────────────────────────────────────────────────


def test_shrinkage_guard_blocks_the_2026_09_09_accident_shape() -> None:
    """真实事故形状：25 篇 → 4 篇，缺失清单必须能被指名。"""
    previous = {f"external/public/doc{i}.md" for i in range(21)} | {f"doc{i}.md" for i in range(4)}
    candidate = {f"doc{i}.md" for i in range(4)}

    guard = evaluate_index_shrinkage(previous_keys=previous, candidate_keys=candidate)

    assert guard["blocked"] is True
    assert guard["previous_documents"] == 25 and guard["candidate_documents"] == 4
    assert guard["unexplained_missing_count"] == 21
    assert "external/public/doc0.md" in guard["unexplained_missing"]


def test_shrinkage_guard_excuses_documents_deleted_through_the_api() -> None:
    guard = evaluate_index_shrinkage(
        previous_keys={"a.md", "b.md", "upload/c.md"},
        candidate_keys={"a.md", "b.md"},
        excused_names=["c.md"],
    )
    assert guard["blocked"] is False
    assert guard["missing_count"] == 1 and guard["excused_count"] == 1


def test_shrinkage_guard_allows_growth_and_identical_rebuilds() -> None:
    assert evaluate_index_shrinkage(previous_keys={"a.md"}, candidate_keys={"a.md", "b.md"})["blocked"] is False
    assert evaluate_index_shrinkage(previous_keys={"a.md"}, candidate_keys={"a.md"})["blocked"] is False
    assert evaluate_index_shrinkage(previous_keys=set(), candidate_keys={"a.md"})["blocked"] is False


# ── 4. 一致性审计 ─────────────────────────────────────────────────────────


def _write_index(index_root: Path, version: str, chunks: list[dict]) -> None:
    (index_root / version).mkdir(parents=True, exist_ok=True)
    (index_root / version / "chunks.json").write_text(json.dumps(chunks, ensure_ascii=False), encoding="utf-8")
    (index_root / "CURRENT").write_text(version, encoding="utf-8")


def test_index_document_keys_reports_empty_when_index_missing(tmp_path: Path) -> None:
    keys, count, version = index_document_keys(tmp_path / "absent")
    assert keys == set() and count == 0 and version is None


def test_audit_flags_divergence_between_notes_and_active_index(tmp_path: Path) -> None:
    db = _make_notes_db(
        tmp_path / "product.sqlite3",
        [{"vault_path": "a.md"}, {"vault_path": "policies/b.md"}, {"vault_path": "c.md", "ai_access_level": "excluded"}],
    )
    index_root = tmp_path / "indexes"
    _write_index(index_root, "m3-x", [
        {"metadata": {"relative_path": "knowledge/a.md", "workspace": "knowledge", "acl_json": "{}"}},
    ])

    report = audit_index_consistency(index_root=index_root, db_path=db)

    assert report["consistent"] is False
    assert report["declared_documents"] == 2  # excluded 的那篇不计入
    assert report["indexed_documents"] == 1
    assert report["missing_from_index"] == ["policies/b.md"]
    assert report["chunks"] == 1
    assert report["chunks_with_workspace"] == 1 and report["chunks_with_acl"] == 1
    # 2026-09-10 拍板：语料只收 vault 根目录 → 子目录缺失属"已接受的口径"，
    # 不算需要处理的分叉（consistent 仍为 False，表示"确实有偏差"）。
    assert report["scope_consistent"] is True
    assert report["in_scope_missing_count"] == 0
    assert report["out_of_scope_subtrees"] == {"policies": 1}


def test_audit_is_consistent_when_index_covers_declared_documents(tmp_path: Path) -> None:
    db = _make_notes_db(tmp_path / "product.sqlite3", [{"vault_path": "a.md"}])
    index_root = tmp_path / "indexes"
    _write_index(index_root, "m3-y", [{"metadata": {"relative_path": "knowledge/a.md"}}])

    report = audit_index_consistency(index_root=index_root, db_path=db)

    assert report["consistent"] is True
    assert report["missing_from_index_count"] == 0 and report["undeclared_in_index_count"] == 0


# ── 5. KnowledgeService.rebuild 的接线（守卫 + 注入真的被调用）──────────────


def test_rebuild_refuses_to_shrink_and_leaves_current_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    index_root = tmp_path / "indexes"
    previous_version = "m3-previous"
    _write_index(index_root, previous_version, [
        {"metadata": {"relative_path": "knowledge/a.md"}},
        {"metadata": {"relative_path": "knowledge/b.md"}},
    ])
    monkeypatch.setattr(ks, "load_corpus", lambda dirs, **kwargs: [SimpleNamespace(metadata={"relative_path": "knowledge/a.md"})])

    def _must_not_build(*args, **kwargs):  # pragma: no cover - 只有守卫失效才会执行
        raise AssertionError("build_versioned_index should not run when the guard blocks")

    monkeypatch.setattr(ks, "build_versioned_index", _must_not_build)
    service = ks.KnowledgeService(tmp_path / "docs", tmp_path / "uploads", index_root)

    with pytest.raises(IndexShrinkageError) as excinfo:
        service.rebuild()

    assert "b.md" in str(excinfo.value)
    assert (index_root / "CURRENT").read_text(encoding="utf-8") == previous_version


def test_rebuild_force_bypasses_the_guard(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    index_root = tmp_path / "indexes"
    _write_index(index_root, "m3-previous", [{"metadata": {"relative_path": "knowledge/a.md"}},
                                            {"metadata": {"relative_path": "knowledge/b.md"}}])
    monkeypatch.setattr(ks, "load_corpus", lambda dirs, **kwargs: [SimpleNamespace(metadata={"relative_path": "knowledge/a.md"})])
    built: dict = {}

    def _fake_build(provider, chunks, root, version):
        built["chunks"] = chunks
        _write_index(root, version, [{"metadata": dict(chunks[0].metadata)}])
        return None, root / version

    monkeypatch.setattr(ks, "build_versioned_index", _fake_build)
    monkeypatch.setattr(ks, "BGEEmbeddingProvider", lambda: object())
    service = ks.KnowledgeService(tmp_path / "docs", tmp_path / "uploads", index_root)

    service.rebuild(force=True)

    assert built["chunks"], "force 放行后应当真正构建"
    assert (index_root / "CURRENT").read_text(encoding="utf-8").startswith("m3-")


def test_rebuild_injects_notes_metadata_before_building(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """守卫放行的正常路径下，构建前的 chunk 必须已带上 ACL / 命名空间字段。"""
    db = _make_notes_db(
        tmp_path / "product.sqlite3",
        [{"vault_path": "a.md", "workspace": "knowledge", "acl_json": '{"workspace": "knowledge"}'}],
    )
    index_root = tmp_path / "indexes"
    monkeypatch.setattr(ks, "load_corpus", lambda dirs, **kwargs: [SimpleNamespace(metadata={"relative_path": "knowledge/a.md"})])
    captured: dict = {}

    def _fake_build(provider, chunks, root, version):
        captured["metadata"] = chunks[0].metadata
        _write_index(root, version, [{"metadata": dict(chunks[0].metadata)}])
        return None, root / version

    monkeypatch.setattr(ks, "build_versioned_index", _fake_build)
    monkeypatch.setattr(ks, "BGEEmbeddingProvider", lambda: object())
    service = ks.KnowledgeService(tmp_path / "docs", tmp_path / "uploads", index_root, db_path=db)

    service.rebuild()

    assert captured["metadata"]["workspace"] == "knowledge"
    assert captured["metadata"]["acl_json"] == '{"workspace": "knowledge"}'
    assert captured["metadata"]["metadata_source"] == "notes"


# ── 6. 已声明语料范围：把"已接受的口径"与"真分叉"分开 ────────────────────────
# 2026-09-10 拍板：文件层面的差异（子目录文档没进索引、顶层 4 篇与 active 版本
# 内容冲突）不追，语料声明为"仅 vault 根目录"。于是审计必须能区分：
# 范围外缺失 = 已接受；范围内缺失 / 索引含 notes 不认识的文档 = 真问题。
# 否则告警每次启动都为真，就会变成没人看的噪音，真事故反而更容易藏。


def test_parse_included_subtrees_normalises_declarations() -> None:
    assert parse_included_subtrees("") == ()
    assert parse_included_subtrees(None) == ()
    assert parse_included_subtrees("policies") == ("policies",)
    assert parse_included_subtrees("policies, workflows ,") == ("policies", "workflows")
    assert parse_included_subtrees("knowledge/policies") == ("policies",)  # 前缀被归一掉
    assert parse_included_subtrees(["workflows", "policies"]) == ("policies", "workflows")


def test_document_subtree_and_scope_membership() -> None:
    assert document_subtree("a.md") == ""
    assert document_subtree("knowledge/policies/b.md") == "policies"

    # 根目录文件永远算在范围内（语料本来就声明收根目录）
    assert is_document_in_scope("a.md", ()) is True
    # 未声明子树 → 范围外
    assert is_document_in_scope("policies/b.md", ()) is False
    assert is_document_in_scope("external/public/c.md", ()) is False
    # 声明后即进入范围
    assert is_document_in_scope("policies/b.md", ("policies",)) is True
    assert is_document_in_scope("external/public/c.md", ("policies",)) is False


def test_classify_divergence_splits_accepted_from_real() -> None:
    result = classify_divergence(
        missing_from_index=["a.md", "policies/b.md", "external/public/c.md"],
        undeclared_in_index=[],
        included_subtrees=(),
    )

    assert result["in_scope_missing"] == ["a.md"]  # 根目录缺失才是真问题
    assert result["out_of_scope_subtrees"] == {"external": 1, "policies": 1}
    assert result["out_of_scope_missing_count"] == 2
    assert result["scope_consistent"] is False


def test_classify_divergence_treats_undeclared_documents_as_a_real_problem() -> None:
    """索引里有、notes 不认识的文档永远算问题（说明索引来自另一套事实源）。"""
    result = classify_divergence(
        missing_from_index=["policies/b.md"],
        undeclared_in_index=["ghost.md"],
        included_subtrees=(),
    )

    assert result["in_scope_missing_count"] == 0
    assert result["out_of_scope_missing_count"] == 1
    assert result["undeclared_count"] == 1
    assert result["scope_consistent"] is False


def test_audit_scope_consistent_can_be_true_while_divergence_exists(tmp_path: Path) -> None:
    """本机真实形状：notes 25 篇、索引 4 篇顶层 → 有偏差但无需处理。"""
    db = _make_notes_db(
        tmp_path / "product.sqlite3",
        [{"vault_path": "a.md"}, {"vault_path": "b.md"}, {"vault_path": "policies/c.md"}],
    )
    index_root = tmp_path / "indexes"
    _write_index(index_root, "m3-real", [
        {"metadata": {"relative_path": "knowledge/a.md"}},
        {"metadata": {"relative_path": "knowledge/b.md"}},
    ])

    report = audit_index_consistency(index_root=index_root, db_path=db)

    assert report["consistent"] is False           # 确实有偏差，不粉饰
    assert report["scope_consistent"] is True      # 但都在已声明范围外
    assert report["missing_from_index_count"] == 1
    assert report["out_of_scope_subtrees"] == {"policies": 1}
    assert report["in_scope_missing_count"] == 0


def test_audit_declaring_a_subtree_makes_its_absence_a_real_problem(tmp_path: Path) -> None:
    """一旦把子树纳入声明范围，同一份数据就从"已接受"变成"必须处理"。"""
    db = _make_notes_db(
        tmp_path / "product.sqlite3",
        [{"vault_path": "a.md"}, {"vault_path": "policies/c.md"}],
    )
    index_root = tmp_path / "indexes"
    _write_index(index_root, "m3-real", [{"metadata": {"relative_path": "knowledge/a.md"}}])

    default = audit_index_consistency(index_root=index_root, db_path=db)
    assert default["scope_consistent"] is True
    assert default["out_of_scope_subtrees"] == {"policies": 1}

    strict = audit_index_consistency(index_root=index_root, db_path=db, included_subtrees=("policies",))
    assert strict["scope_consistent"] is False
    assert strict["in_scope_missing"] == ["policies/c.md"]
    assert strict["out_of_scope_missing_count"] == 0
