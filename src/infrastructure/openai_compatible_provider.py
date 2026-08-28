from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx


class NormalizedProviderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def normalize_http_error(status_code: int) -> str:
    return {
        400: "invalid_request", 401: "authentication_failed", 402: "quota_exhausted",
        403: "authentication_failed", 404: "model_not_found", 429: "rate_limited",
    }.get(status_code, "provider_unavailable" if status_code >= 500 else "unknown_provider_error")


class OpenAICompatibleProvider:
    def __init__(self, provider_name: str, base_url: str, api_key: str, model_name: str, timeout: float = 60.0, max_retries: int = 1, verified: bool = False, configured_models: list[str] | None = None) -> None:
        self.provider_name, self.base_url, self.api_key, self.model_name = provider_name, base_url.rstrip("/"), api_key, model_name
        self.timeout, self.max_retries, self.verified = timeout, max_retries, verified
        self._last_health: datetime | None = None
        self._health_status = "not_checked" if api_key else "not_configured"
        self.configured_models = list(dict.fromkeys(configured_models or [model_name]))
        self.verified_models = {model_name} if verified else set()

    def with_model(self, model_name: str):
        if model_name not in self.configured_models:
            raise NormalizedProviderError("model_not_found", f"Model is not enabled for {self.provider_name}")
        clone = OpenAICompatibleProvider(
            self.provider_name, self.base_url, self.api_key, model_name, self.timeout,
            self.max_retries, model_name in self.verified_models, self.configured_models,
        )
        clone.verified_models = self.verified_models
        return clone

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.base_url and self.model_name)

    @staticmethod
    def _usage(data: dict[str, Any] | None) -> dict[str, Any]:
        if not data:
            return {"usage_source": "unavailable"}
        return {"input_tokens": data.get("prompt_tokens"), "output_tokens": data.get("completion_tokens"),
            "total_tokens": data.get("total_tokens"), "estimated_cost": None, "currency": None,
            "usage_source": "provider_reported"}

    def _post(self, payload: dict[str, Any], stream: bool = False):
        if not self.available:
            raise NormalizedProviderError("provider_not_configured", f"{self.provider_name} is not configured")
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        for attempt in range(self.max_retries + 1):
            try:
                if stream:
                    client = httpx.Client(timeout=self.timeout)
                    return client, client.stream("POST", f"{self.base_url}/chat/completions", headers=headers, json=payload)
                else:
                    return httpx.post(f"{self.base_url}/chat/completions", headers=headers, json=payload, timeout=self.timeout)
            except httpx.TimeoutException as exc:
                if attempt == self.max_retries:
                    raise NormalizedProviderError("timeout", "Provider request timed out") from exc
            except httpx.HTTPError as exc:
                if attempt == self.max_retries:
                    raise NormalizedProviderError("provider_unavailable", "Provider connection failed") from exc

    def complete(self, messages: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
        response = self._post({"model": self.model_name, "messages": messages, "temperature": 0.2, "stream": False})
        if response.status_code >= 400:
            raise NormalizedProviderError(normalize_http_error(response.status_code), f"Provider returned HTTP {response.status_code}")
        try:
            data = response.json()
            self.verified_models.add(self.model_name)
            return data["choices"][0]["message"]["content"].strip(), self._usage(data.get("usage"))
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise NormalizedProviderError("unknown_provider_error", "Malformed provider response") from exc

    def stream(self, messages: list[dict[str, str]]) -> Iterable[dict[str, Any]]:
        client, context = self._post({"model": self.model_name, "messages": messages, "temperature": 0.2, "stream": True, "stream_options": {"include_usage": True}}, stream=True)
        try:
            with context as response:
                if response.status_code >= 400:
                    raise NormalizedProviderError(normalize_http_error(response.status_code), f"Provider returned HTTP {response.status_code}")
                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    choices = data.get("choices") or []
                    if choices and isinstance(choices[0], dict):
                        delta_content = choices[0].get("delta", {}).get("content")
                        if delta_content:
                            self.verified_models.add(self.model_name)
                            yield {"delta": delta_content}
                    if data.get("usage"):
                        yield {"usage": self._usage(data["usage"])}
        finally:
            client.close()

    def health_check(self) -> dict[str, Any]:
        self._last_health = datetime.now(timezone.utc)
        if not self.available:
            self._health_status = "not_configured"
        else:
            try:
                response = httpx.get(f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.api_key}"}, timeout=min(self.timeout, 15.0))
                self._health_status = "available" if response.status_code < 400 else normalize_http_error(response.status_code)
                self.verified = self.verified or response.status_code < 400
            except httpx.HTTPError:
                self._health_status = "health_check_failed"
        return self.capability()

    def capability(self) -> dict[str, Any]:
        return {"provider": self.provider_name, "model": self.model_name, "configured": self.available,
            "verified": self.model_name in self.verified_models, "streaming_support": True, "usage_support": True,
            "health_status": self._health_status, "last_health_check": self._last_health,
            "pricing_metadata_available": False}
