"""在干净的 HEAD 工作树上复核提交态：本地工作区绿 ≠ 提交可过。

为什么需要这个脚本（两次实测教训）：

1. **实现与消费方分属两个提交**——提交态 ``ImportError`` 让 14 个测试全红，而本地
   工作区是绿的（未提交的实现把它兜住了）。2026-09-12 修掉后又踩了一次同类问题。
2. **``data/`` 被 ``.gitignore`` 忽略**——干净工作树里没有 ``mindgraph_indexes`` /
   ``retrieval_indexes`` 两套索引根，于是 ``test_evaluation_v2_migration`` /
   ``test_index_root_registry`` / ``test_freeze_baseline`` 会以 "No index version
   under data/... is compatible" 报 **14 个假失败**，指向"代码坏了"。
   （缺数据时测试也已被改进为输出人话诊断，见 ``tests/index_data_hint.py``。）

手工三步（``worktree add`` → 复制两套索引根 → 跑全量 → 清理）容易漏，这里收敛成一条命令。

用法::

    .venv\\Scripts\\python.exe scripts/verify_committed_state.py
    .venv\\Scripts\\python.exe scripts/verify_committed_state.py --keep
    .venv\\Scripts\\python.exe scripts/verify_committed_state.py --ref origin/main
    .venv\\Scripts\\python.exe scripts/verify_committed_state.py --pytest-args "-q -x"

只覆盖后端（pytest）。前端 ``tsc -b`` / ``vitest`` 在干净工作树里需要先装 node_modules，
不在这里做——那是另一条链路。

本机 agent harness 的两个已知干扰会自动剥掉：``ACC_PRODUCT_CONFIG_V3``（约 500KB，
超 Windows 环境块上限，会让夹具 teardown 抛 ValueError、子进程探测假失败）与
``CODEBUDDY_SAFE_DELETE_ENABLED``（不置 0 时安全删除会拦 pytest 的临时目录清理，
让**退出码 1 变成假失败**）。
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKTREE = PROJECT_ROOT.parent / "_verify_head"

#: ``data/`` 被 gitignore，不复制过去提交态必报假失败；用**复制**而非软链，
#: 免得测试把结果写回主工作区。
RUNTIME_DATA = ("mindgraph_indexes", "retrieval_indexes")

_HARNESS_ENV_TO_DROP = ("ACC_PRODUCT_CONFIG_V3",)
_HARNESS_ENV_TO_SET = {"CODEBUDDY_SAFE_DELETE_ENABLED": "0"}


def _python_executable() -> Path:
    """优先用本仓库 venv 的解释器（上面这段 docstring 里的调用方式）。"""
    relative = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    candidate = PROJECT_ROOT / ".venv" / relative
    return candidate if candidate.exists() else Path(sys.executable)


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    for name in _HARNESS_ENV_TO_DROP:
        env.pop(name, None)
    env.update(_HARNESS_ENV_TO_SET)
    return env


def _run(cmd: list[object], cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    printable = " ".join(str(part) for part in cmd)
    print(f"[verify] $ {printable}")
    # 参数全部由本脚本自己拼（无外部输入拼接），且不需要 shell。
    return subprocess.run(
        [str(part) for part in cmd], cwd=str(cwd), env=env, check=False
    )


def _remove_worktree(path: Path) -> None:
    if not path.exists():
        return
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(path)],
        cwd=str(PROJECT_ROOT),
        check=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="在干净的 HEAD 工作树上跑全量测试（提交态复核）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--ref", default="HEAD", help="要复核的提交/引用（默认 HEAD）")
    parser.add_argument("--worktree", type=Path, default=DEFAULT_WORKTREE, help="工作树落地目录")
    parser.add_argument("--keep", action="store_true", help="跑完保留工作树，便于进去手工排查")
    parser.add_argument("--pytest-args", default="", help='透传给 pytest 的参数，如 "-x -k evaluation"')
    parser.add_argument(
        "--without-runtime-data",
        action="store_true",
        help="故意不复制运行期索引根（诊断用：看清缺数据时报什么，不是复核）",
    )
    args = parser.parse_args(argv)

    worktree = args.worktree.expanduser().resolve()

    _remove_worktree(worktree)
    if worktree.exists():
        print(
            f"[verify] 目录已存在且 git 无法清理：{worktree}\n"
            "         请手工确认后删除（或换 --worktree 指向别处）再重试",
            file=sys.stderr,
        )
        return 2

    added = _run(["git", "worktree", "add", "--detach", worktree, args.ref], PROJECT_ROOT)
    if added.returncode != 0:
        print("[verify] worktree 创建失败，终止", file=sys.stderr)
        return added.returncode

    copied: list[str] = []
    if args.without_runtime_data:
        print("[verify] --without-runtime-data：跳过复制（预期看到索引根缺失诊断）")
    else:
        for name in RUNTIME_DATA:
            source = PROJECT_ROOT / "data" / name
            if not source.is_dir():
                print(f"[verify] 警告：本仓库缺 data/{name}，跳过复制（提交态会因此报索引不兼容）")
                continue
            shutil.copytree(source, worktree / "data" / name, dirs_exist_ok=True)
            copied.append(name)
        print(f"[verify] 已复制运行期数据：{'、'.join(copied) if copied else '（无）'}")

    pytest_cmd: list[object] = [_python_executable(), "-m", "pytest"]
    pytest_cmd.extend(args.pytest_args.split())
    result = _run(pytest_cmd, worktree, env=_child_env())

    if args.keep:
        print(f"[verify] 工作树保留在：{worktree}")
    else:
        _remove_worktree(worktree)

    verdict = "通过" if result.returncode == 0 else "未通过"
    print(f"[verify] 提交态 {args.ref} 退出码 {result.returncode}（{verdict}）")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
