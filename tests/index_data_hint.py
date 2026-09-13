"""缺运行期索引根时的"人话"诊断，供依赖 ``data/`` 的测试拼进断言消息。

背景：``data/`` 被 ``.gitignore`` 忽略，``git worktree add`` 出来的提交态工作树、
以及没有 provision 运行期数据的 CI 都不会检出两套索引根。此时断言会以

    AssertionError: data/mindgraph_indexes 没有 CURRENT
    ValueError: No index version under data/mindgraph_indexes is compatible with dataset ...

变红——两条都**指向代码**，真实原因却是"运行期数据没复制过去"。2026-09-12 排查提交态
时因此被带偏了 4 次空跑，故把这句话做成可复用的诊断。

纯函数（``tests/`` 在 pytest 的 pythonpath 上，直接 ``from index_data_hint import ...``）。
**不要在这里放 fixture**：非 conftest 模块定义的 fixture 不进 pytest 搜索路径——这个坑
踩过（见 ``index_build_fixture.py`` 顶部说明）。
"""

from __future__ import annotations

from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: 登记在 ``application/index_metadata.INDEX_ROOT_REGISTRY`` 的两套语料索引根。
RUNTIME_INDEX_ROOTS = ("mindgraph_indexes", "retrieval_indexes")


def missing_runtime_index_roots(project_root: Path | None = None) -> list[str]:
    """返回缺 ``CURRENT`` 的索引根名；全都在盘上时返回空列表。

    ``project_root`` 只为可测性存在（默认本仓库根），测试可传 ``tmp_path`` 造两种情形。
    """
    root = Path(project_root) if project_root is not None else _PROJECT_ROOT
    return [
        name
        for name in RUNTIME_INDEX_ROOTS
        if not (root / "data" / name / "CURRENT").is_file()
    ]


def runtime_data_hint(project_root: Path | None = None) -> str:
    """缺索引根时返回一句可拼进断言消息的诊断；不缺时返回空串。

    返回空串（而不是 None）是刻意的：调用方写成 ``assert x, "msg" + runtime_data_hint()``
    即可，两边都不需要分支。
    """
    missing = missing_runtime_index_roots(project_root)
    if not missing:
        return ""
    listed = "、".join(f"data/{name}" for name in missing)
    return (
        f"（运行期数据缺失：{listed} —— data/ 被 .gitignore 忽略，干净 worktree 与未 "
        "provision 的 CI 都不会检出它。先复制两套索引根（见 AGENTS.md「提交态复核」；"
        "用复制而非软链，避免测试写回主工作区），**这不是代码缺陷**）"
    )
