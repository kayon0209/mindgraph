from __future__ import annotations

from infrastructure.openai_compatible_provider import OpenAICompatibleProvider
from infrastructure.zhipu_compatible_client import ZHIPU_OPENAI_BASE_URL


class ZhipuChatProvider(OpenAICompatibleProvider):
    """Zhipu's documented OpenAI-compatible chat endpoint.

    Keep the historical provider name as part of the configuration contract,
    while using the maintained HTTP adapter shared by the other compatible
    providers instead of the SDK that constrains PyJWT to a vulnerable range.
    """

    provider_name = "zhipu"

    def __init__(self, api_key: str, model_name: str, verified: bool = False) -> None:
        super().__init__(
            self.provider_name,
            ZHIPU_OPENAI_BASE_URL,
            api_key,
            model_name,
            timeout=90.0,
            max_retries=2,
            verified=verified,
            configured_models=[model_name],
        )
