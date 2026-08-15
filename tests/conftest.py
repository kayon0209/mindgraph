"""测试夹具和共享配置。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def clean_env():
    """每个测试运行前清理环境变量影响。"""
    old_environ = dict(os.environ)
    # 确保测试时不会读取真实 .env
    os.environ["ENVIRONMENT"] = "test"
    os.environ["AUTH_MODE"] = "off"
    os.environ["CHAT_PROVIDER"] = "deepseek"
    os.environ["OPENAI_COMPAT_API_KEY"] = "test-key"
    os.environ["OPENAI_COMPAT_MODEL"] = "deepseek-test"
    os.environ["OPENAI_COMPAT_BASE_URL"] = "https://test.example.com"
    os.environ["BGE_LOCAL_FILES_ONLY"] = "true"
    os.environ["RATE_LIMIT_ENABLED"] = "false"
    yield
    os.environ.clear()
    os.environ.update(old_environ)


@pytest.fixture
def sample_chunks():
    """示例 Chunk 列表。"""
    from retrieval.types import Chunk

    return [
        Chunk("policy.md::0", "差旅费报销时限为十个工作日", "policy.md", 0, "时限"),
        Chunk("policy.md::1", "普通员工飞机标准为经济舱", "policy.md", 1, "交通"),
        Chunk("materials.md::0", "电子发票须打印后粘贴", "materials.md", 0, "发票"),
    ]


@pytest.fixture
def temp_dir():
    """临时目录 fixture。"""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def mock_chat_provider():
    """模拟 ChatProvider。"""
    provider = MagicMock()
    provider.provider_name = "test"
    provider.model_name = "test-model"
    provider.available = True
    provider.complete.return_value = ("这是测试答案。", {"total_tokens": 10})
    provider.stream.return_value = iter([
        {"delta": "这是"},
        {"delta": "测试"},
        {"delta": "答案"},
        {"usage": {"total_tokens": 10, "input_tokens": 5, "output_tokens": 5}},
    ])
    return provider


@pytest.fixture
def sample_questions():
    """示例评测问题。"""
    return [
        {
            "case_id": 1,
            "question": "差旅费报销的时限是几天？",
            "category": "direct_rule",
            "expected_behavior": "answer",
            "reference_answer": "出差结束后10个工作日内办理报销。",
            "required_facts": ["10个工作日"],
        },
        {
            "case_id": 19,
            "question": "公司股票怎么购买？",
            "category": "out_of_scope",
            "expected_behavior": "refuse",
            "reference_answer": "抱歉，我只能回答公司报销相关问题。",
            "required_facts": [],
        },
    ]
