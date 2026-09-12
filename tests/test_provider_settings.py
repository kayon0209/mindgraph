from __future__ import annotations

from infrastructure.settings import Settings


def test_legacy_deepseek_labels_are_remapped_when_endpoint_is_gitee() -> None:
    settings = Settings(
        _env_file=None,
        CHAT_PROVIDER="deepseek",
        OPENAI_COMPAT_PROVIDER_NAME="deepseek",
        OPENAI_COMPAT_BASE_URL="https://ai.gitee.com/v1",
        OPENAI_COMPAT_MODEL="qwen3.8-flash",
    )

    assert settings.CHAT_PROVIDER == "gitee"
    assert settings.OPENAI_COMPAT_PROVIDER_NAME == "gitee"


def test_partial_legacy_slot_name_is_aligned_with_gitee_default() -> None:
    settings = Settings(
        _env_file=None,
        CHAT_PROVIDER="gitee",
        OPENAI_COMPAT_PROVIDER_NAME="deepseek",
        OPENAI_COMPAT_BASE_URL="https://ai.gitee.com/v1",
    )

    assert settings.CHAT_PROVIDER == "gitee"
    assert settings.OPENAI_COMPAT_PROVIDER_NAME == "gitee"
