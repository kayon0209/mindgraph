"""运行期数据诊断的守卫：缺索引根时，别把原因说成"代码坏了"。

背景是 2026-09-12 排查提交态时被带偏 4 次：干净 worktree 里 ``data/`` 被 gitignore
不会检出，于是 14 个测试以"索引版本不兼容""没有 CURRENT""两个根版本收敛了"变红——
每一条都指向代码，真实原因只是运行期数据没复制过去。``index_data_hint`` 提供那句话。

这里只测纯函数本身（可注入 ``project_root``）；"测试有没有真的拼上它"由
``test_index_root_registry`` / ``test_freeze_baseline`` 的断言消息覆盖。
"""

from __future__ import annotations

from pathlib import Path

from index_data_hint import (
    RUNTIME_INDEX_ROOTS,
    missing_runtime_index_roots,
    runtime_data_hint,
)


def _provision(root: Path, *names: str) -> None:
    """在 ``root`` 下造出像样的索引根（有 CURRENT 才算可用）。"""
    for name in names:
        directory = root / "data" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "CURRENT").write_text("idx-20260901\n", encoding="utf-8")


def test_hint_names_missing_roots_and_says_it_is_not_a_code_defect(tmp_path: Path) -> None:
    assert missing_runtime_index_roots(tmp_path) == list(RUNTIME_INDEX_ROOTS)

    hint = runtime_data_hint(tmp_path)
    for name in RUNTIME_INDEX_ROOTS:
        assert f"data/{name}" in hint
    assert ".gitignore" in hint
    assert "不是代码缺陷" in hint
    assert "AGENTS.md" in hint


def test_hint_is_empty_when_both_roots_are_on_disk(tmp_path: Path) -> None:
    _provision(tmp_path, *RUNTIME_INDEX_ROOTS)

    assert missing_runtime_index_roots(tmp_path) == []
    assert runtime_data_hint(tmp_path) == ""


def test_hint_points_only_at_the_root_that_is_actually_missing(tmp_path: Path) -> None:
    _provision(tmp_path, "mindgraph_indexes")

    assert missing_runtime_index_roots(tmp_path) == ["retrieval_indexes"]
    hint = runtime_data_hint(tmp_path)
    assert "data/retrieval_indexes" in hint
    assert "data/mindgraph_indexes" not in hint


def test_root_without_current_counts_as_missing(tmp_path: Path) -> None:
    """只有目录不够：没有 CURRENT 的根同样不可用（索引根是版本目录的父级）。"""
    (tmp_path / "data" / "retrieval_indexes").mkdir(parents=True)
    (tmp_path / "data" / "mindgraph_indexes").mkdir(parents=True)

    assert missing_runtime_index_roots(tmp_path) == list(RUNTIME_INDEX_ROOTS)
