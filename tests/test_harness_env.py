"""测试基础设施自身的契约：环境还原必须扛得住超长变量。

为什么值得为 conftest 写测试：``clean_env`` 在 **teardown** 里还原环境。它一旦
抛异常，报出来的是"一批测试集体失败/报错"，而失败原因与被测代码毫无关系
（本机实测：宿主注入的 ``ACC_PRODUCT_CONFIG_V3`` 长约 516KB，远超 Windows
环境块 32767 的上限，``os.environ.update(snapshot)`` 直接 ValueError）。
这样的假红会把一次真实的修复淹没掉，所以它自己也得有守卫。
"""

from __future__ import annotations

import os

from conftest import _ENVIRON_MAX_ENTRY, restore_environ


def test_restore_environ_skips_oversized_entries():
    """超长条目跳过（无法写回），其余条目照常还原，且不抛异常。"""
    snapshot = dict(os.environ)
    try:
        os.environ["MG_PROBE"] = "present"
        restore_environ({
            "MG_SMALL": "1",
            "MG_OVERSIZED": "x" * (_ENVIRON_MAX_ENTRY + 10),
        })

        assert os.environ["MG_SMALL"] == "1", "能写回的条目必须还原"
        assert "MG_OVERSIZED" not in os.environ, "超长条目无法写回，跳过而不是崩溃"
        assert "MG_PROBE" not in os.environ, "还原必须清掉测试期间新加的变量"
    finally:
        restore_environ(snapshot)


def test_restore_environ_replaces_previous_values():
    """还原是"替换"而不是"合并"：快照里没有的键不得存活。"""
    snapshot = dict(os.environ)
    try:
        os.environ["MG_STALE"] = "from-previous-test"
        restore_environ({"MG_FRESH": "2"})

        assert os.environ["MG_FRESH"] == "2"
        assert "MG_STALE" not in os.environ
    finally:
        restore_environ(snapshot)
