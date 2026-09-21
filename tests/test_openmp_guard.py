"""``config`` 不应替环境决定 OpenMP 线程数与权重加载并行度。

## 为什么这个测试现在断言「不设置」

2026-09-20 曾在 ``config.py`` 里设过 ``OMP_NUM_THREADS=1`` +
``HF_DEACTIVATE_ASYNC_LOAD=1``，依据是"先载入 FAISS 索引与 BGE、再加载
CrossEncoder 精排器会确定性段错误"，当时判断为 OpenMP 运行时刻冲突。

真正的根因是**环境提交内存不足**：页面文件只有 12.5GB（系统盘自动管理）、
C 盘仅剩 3.6GB，加载 1.1GB 权重时贴着上限。症状是 exit 139 / access violation
（无 Python traceback），最直白的一次是 ``OSError 1455「页面文件太小」`` 抛在
safetensors 的 ``safe_open`` 处。在页面文件迁到 D 盘（C 2048MB + D 16384MB，
共 18GB）后复测，所有组合都稳定：

    OMP=12 + 关异步 → 4/4     OMP=4 + 关异步 → 4/4     OMP=1 + 关异步 → 4/4
    OMP=12 + 不关异步 → 5/5   OMP=1 + 不关异步 → 5/5

而 ``OMP=1`` 会让 BGE 编码退化成单线程（查询延迟 ~38ms → ~100ms 量级）。
**用真实性能换一个并不存在的故障不划算**，所以移除，并由本测试守住"不要再加回来"。

环境真出问题时（页面文件过小），应按 ``config.py`` 注释里记录的症状去查页面文件
与磁盘空间，而不是去调线程数。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"

# 用 "-" 占位而不是让 print 输出 None：``os.environ.get`` 返回的是 None，
# 打成字符串后是 "None"，与"空值"难以区分，曾让断言写成 ('', '') 却永远不匹配。
_PROBE = (
    "import config, os; "
    "print((os.environ.get('OMP_NUM_THREADS') or '-') + '|' + (os.environ.get('HF_DEACTIVATE_ASYNC_LOAD') or '-'))"
)

_PLACEHOLDER = "-"
_GUARDED = ("OMP_NUM_THREADS", "HF_DEACTIVATE_ASYNC_LOAD")


def _run_probe(**overrides: str) -> tuple[str, str]:
    """在干净子进程里 import config，回读两个变量（未设置时为占位符 "-"）。"""
    env = {key: value for key, value in os.environ.items() if key not in _GUARDED}
    # harness 可能注入超大变量（超 Windows 环境块上限），剔除后子进程才起得来。
    env.pop("ACC_PRODUCT_CONFIG_V3", None)
    env.update(overrides)
    env["PYTHONPATH"] = str(SRC)
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    omp, _, async_load = completed.stdout.strip().partition("|")
    return (omp, async_load)


def test_config_does_not_force_thread_or_load_caps() -> None:
    assert _run_probe() == (_PLACEHOLDER, _PLACEHOLDER)


def test_user_provided_values_are_preserved() -> None:
    """config 只读环境，不覆盖调用方显式配置。"""
    assert _run_probe(OMP_NUM_THREADS="4", HF_DEACTIVATE_ASYNC_LOAD="0") == ("4", "0")
