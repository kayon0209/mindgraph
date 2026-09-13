"""测试夹具和共享配置。"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
from unittest.mock import MagicMock

import pytest


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_BUSINESS_DATABASE = (_PROJECT_ROOT / "data" / "product" / "product.sqlite3").resolve()

# Windows 进程环境块中单个变量（key=value）的长度上限，CPython 的 os.putenv
# 超限即抛 ValueError。
_ENVIRON_MAX_ENTRY = 32767


def restore_environ(snapshot: dict[str, str]) -> None:
    """把 ``os.environ`` 还原到快照；超出环境块上限的变量只能跳过。

    为什么不能直接 ``os.environ.update(snapshot)``：只要快照里存在一个超长变量
    （CI / 工具链注入的大块 JSON 配置很常见，本机会看到约 500KB 的那种），
    Windows 会在写回时抛
    ``ValueError: the environment variable is longer than 32767 characters``。
    它发生在 **teardown**，于是表现为「一批本该通过的测试集体失败/报错」，而且
    异常之后剩下的变量都没还原，后续测试继续被污染 —— 排查成本极高，指向的
    却是一个和被测代码无关的宿主环境问题。

    超长变量在本项目里没有消费方（``grep ACC_PRODUCT_CONFIG`` 零命中），
    跳过它远好过让整轮测试变成假失败。
    """
    os.environ.clear()
    for key, value in snapshot.items():
        if len(key) + len(value) + 1 > _ENVIRON_MAX_ENTRY:
            continue  # 超长条目无法写回（宿主注入的约 500KB 配置块即此类）
        os.environ[key] = value


@pytest.fixture(scope="session", autouse=True)
def block_business_database_in_tests():
    """Fail before any test can construct a handle for the business database.

    TestClient(app) starts the real lifespan unless a test injects a temporary
    container.  A session-scoped guard runs before module-scoped fixtures, so
    an accidental default ``ServiceContainer`` cannot silently initialise the
    repository's business SQLite file.
    """
    from infrastructure.database import ProductDatabase

    original_init = ProductDatabase.__init__
    monkeypatch = pytest.MonkeyPatch()

    def guarded_init(self, path, *args, **kwargs):
        candidate = Path(path).resolve()
        if candidate == _BUSINESS_DATABASE:
            raise AssertionError(
                "tests must not construct the business database; inject a temporary database instead"
            )
        original_init(self, path, *args, **kwargs)

    monkeypatch.setattr(ProductDatabase, "__init__", guarded_init)
    try:
        yield
    finally:
        monkeypatch.undo()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """每个测试运行前清理环境变量影响。"""
    old_environ = dict(os.environ)
    # 确保测试时不会读取真实 .env
    os.environ["ENVIRONMENT"] = "development"
    os.environ["AUTH_MODE"] = "off"
    # off 模式默认只读（写权限需显式开关）；测试需要覆盖写端点的既有行为，
    # 因此显式打开，与本地 .env 的配置保持一致。
    os.environ["AUTH_OFF_ALLOW_WRITES"] = "true"
    os.environ["CHAT_PROVIDER"] = "deepseek"
    os.environ["OPENAI_COMPAT_API_KEY"] = "test-key"
    os.environ["OPENAI_COMPAT_MODEL"] = "deepseek-test"
    os.environ["OPENAI_COMPAT_BASE_URL"] = "https://test.example.com"
    os.environ["BGE_LOCAL_FILES_ONLY"] = "true"
    os.environ["RATE_LIMIT_ENABLED"] = "false"
    # Agentic flags 一律以进程内默认值（False）参与测试：.env 的灰度开启
    # 不得影响"off 态行为"断言——显式覆盖优先于 .env 文件（pydantic-settings
    # 优先级：环境变量 > .env）。需要 on 态的测试自行 setenv + cache_clear。
    for flag in ("ASSIST_ENABLED", "ASSIST_MCP_ENABLED", "AGENT_ASSIST_ENABLED",
                 "AGENT_TASKS_ENABLED", "TASK_WORKER_ENABLED", "AGENT_WRITE_TOOLS_ENABLED",
                 "CONVERSATION_PERSISTENCE_ENABLED"):
        os.environ[flag] = "false"
    # api.auth reads AUTH_MODE at import time; keep the module-level value in
    # sync with the isolated test environment. Tests for enterprise modes can
    # override it explicitly after this autouse fixture runs.
    import api.auth as auth

    monkeypatch.setattr(auth, "AUTH_MODE", "off")

    from infrastructure.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    # 经 restore_environ 而不是 os.environ.update：超长变量无法写回，直接 update
    # 会在 teardown 抛 ValueError，把整轮测试变成假失败（见函数 docstring）。
    restore_environ(old_environ)


@pytest.fixture
def stub_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 m4 ``build()`` 用的 BGE provider 换成确定性替身。

    定义在 conftest 而不是 ``index_build_fixture.py``：非 conftest 模块里的
    fixture 不会被其他测试文件发现（只有 conftest 与显式 ``pytest_plugins``
    才进入 fixture 搜索路径）。替身类本身留在 ``index_build_fixture.py``，
    与文档来源替身同处一文件。
    """
    from index_build_fixture import StubEmbedding

    monkeypatch.setattr("application.index_lifecycle_service.BGEEmbeddingProvider", StubEmbedding)


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
