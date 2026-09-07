"""Small Zhipu OpenAI-compatible client for retained legacy call sites.

The current API provider uses the same documented HTTP interface directly.
This adapter deliberately exposes only the non-streaming chat and embedding
surface still needed by the archived RAG baseline and its evaluator, avoiding
the vulnerable third-party SDK dependency.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx


ZHIPU_OPENAI_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
_TIMEOUT_SECONDS = 90.0


class ZhipuCompatibleClient:
    def __init__(self, api_key: str, *, timeout: float = _TIMEOUT_SECONDS) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.chat = SimpleNamespace(completions=_ChatCompletions(self))
        self.embeddings = _Embeddings(self)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = httpx.post(
            f"{ZHIPU_OPENAI_BASE_URL}/{path.lstrip('/')}",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Malformed Zhipu OpenAI-compatible response")
        return data


class _ChatCompletions:
    def __init__(self, client: ZhipuCompatibleClient) -> None:
        self._client = client

    def create(self, *, model: str, messages: list[dict[str, str]], temperature: float = 0.2) -> SimpleNamespace:
        data = self._client._post(
            "chat/completions", {"model": model, "messages": messages, "temperature": temperature}
        )
        choices = data.get("choices")
        if not isinstance(choices, list):
            raise ValueError("Malformed Zhipu chat response: choices missing")
        normalized_choices = []
        for choice in choices:
            message = choice.get("message") if isinstance(choice, dict) else None
            if not isinstance(message, dict):
                continue
            normalized_choices.append(SimpleNamespace(message=SimpleNamespace(content=message.get("content"))))
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return SimpleNamespace(
            choices=normalized_choices,
            usage=SimpleNamespace(
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
            ),
        )


class _Embeddings:
    def __init__(self, client: ZhipuCompatibleClient) -> None:
        self._client = client

    def create(self, *, input: list[str], model: str) -> SimpleNamespace:
        data = self._client._post("embeddings", {"input": input, "model": model})
        values = data.get("data")
        if not isinstance(values, list):
            raise ValueError("Malformed Zhipu embedding response: data missing")
        normalized = []
        for value in values:
            if not isinstance(value, dict) or not isinstance(value.get("embedding"), list):
                raise ValueError("Malformed Zhipu embedding response item")
            normalized.append(SimpleNamespace(index=value.get("index"), embedding=value["embedding"]))
        return SimpleNamespace(data=normalized)
