from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

from .openai_compatible_provider import NormalizedProviderError, normalize_http_error


class AnthropicProvider:
    provider_name = "anthropic"

    def __init__(self, api_key: str, model_name: str, timeout: float = 60.0) -> None:
        self.api_key, self.model_name, self.timeout = api_key, model_name, timeout
        self._last_health = None
        self._health_status = "not_checked" if api_key else "not_configured"

    @property
    def available(self): return bool(self.api_key and self.model_name)

    @staticmethod
    def _headers(api_key): return {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}

    @staticmethod
    def _payload(messages, model, stream=False):
        system = "\n".join(item.get("content", "") for item in messages if item.get("role") == "system")
        conversation = [item for item in messages if item.get("role") != "system"]
        return {"model": model, "system": system, "messages": conversation, "max_tokens": 1024, "stream": stream}

    def complete(self, messages):
        if not self.available: raise NormalizedProviderError("provider_not_configured", "Anthropic is not configured")
        try:
            response = httpx.post("https://api.anthropic.com/v1/messages", headers=self._headers(self.api_key), json=self._payload(messages, self.model_name), timeout=self.timeout)
            if response.status_code >= 400: raise NormalizedProviderError(normalize_http_error(response.status_code), f"Provider returned HTTP {response.status_code}")
            data = response.json(); usage = data.get("usage") or {}
            return "".join(block.get("text", "") for block in data.get("content", [])), {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"), "total_tokens": None, "estimated_cost": None, "currency": None, "usage_source": "provider_reported"}
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            raise NormalizedProviderError("provider_unavailable", f"Anthropic connection failed: {exc}") from exc
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise NormalizedProviderError("unknown_provider_error", f"Malformed Anthropic response: {exc}") from exc

    def stream(self, messages) -> Iterable[dict[str, Any]]:
        if not self.available: raise NormalizedProviderError("provider_not_configured", "Anthropic is not configured")
        with httpx.stream("POST", "https://api.anthropic.com/v1/messages", headers=self._headers(self.api_key), json=self._payload(messages, self.model_name, True), timeout=self.timeout) as response:
            if response.status_code >= 400: raise NormalizedProviderError(normalize_http_error(response.status_code), f"Provider returned HTTP {response.status_code}")
            for line in response.iter_lines():
                if line.startswith("data:"):
                    data = json.loads(line[5:].strip())
                    if data.get("type") == "content_block_delta":
                        text = data.get("delta", {}).get("text")
                        if text: yield {"delta": text}
                    if data.get("type") == "message_delta" and data.get("usage"):
                        yield {"usage": {"input_tokens": None, "output_tokens": data["usage"].get("output_tokens"), "total_tokens": None, "estimated_cost": None, "currency": None, "usage_source": "provider_reported"}}

    def health_check(self):
        self._last_health = datetime.now(timezone.utc); self._health_status = "not_configured" if not self.available else "implemented_unverified"; return self.capability()

    def capability(self):
        return {"provider": self.provider_name, "model": self.model_name, "configured": self.available, "verified": False,
            "streaming_support": True, "usage_support": True, "health_status": self._health_status,
            "last_health_check": self._last_health, "pricing_metadata_available": False}
