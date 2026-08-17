"""MindGraph 对外品牌契约回归测试。"""
from __future__ import annotations

from infrastructure.logging_config import get_logger


def test_application_loggers_use_mindgraph_namespace():
    assert get_logger("api").name == "mindgraph.api"
