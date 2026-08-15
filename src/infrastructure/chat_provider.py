from __future__ import annotations

from typing import Any, Iterable

from zhipuai import ZhipuAI


class ZhipuChatProvider:
    provider_name = "zhipu"
    def __init__(self, api_key: str, model_name: str, verified: bool = False) -> None:
        self.api_key = api_key
        self.model_name = model_name
        self.verified = verified
        self._client = ZhipuAI(api_key=api_key) if api_key else None

    @property
    def available(self) -> bool:
        return self._client is not None

    @staticmethod
    def _usage(usage: Any) -> dict[str, Any]:
        if usage is None:
            return {"usage_source": "unavailable"}
        return {
            "input_tokens": getattr(usage, "prompt_tokens", None),
            "output_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
            "estimated_cost": None,
            "currency": None,
            "usage_source": "provider_reported",
        }

    def complete(self, messages: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
        if not self._client:
            raise RuntimeError("Zhipu provider is not configured")
        try:
            response = self._client.chat.completions.create(model=self.model_name, messages=messages, temperature=0.2)
        except Exception as exc:
            code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
            if code and str(code) in ("401", "403", "authentication_error"):
                raise RuntimeError("authentication_failed: Invalid API key")
            if code and str(code) == "429":
                raise RuntimeError("rate_limited: Too many requests")
            raise RuntimeError(f"provider_error: {exc}")
        if not response.choices or not response.choices[0].message:
            raise RuntimeError("provider_error: Empty response from model")
        return (response.choices[0].message.content or "").strip(), self._usage(getattr(response, "usage", None))

    def stream(self, messages: list[dict[str, str]]) -> Iterable[dict[str, Any]]:
        if not self._client:
            raise RuntimeError("Zhipu provider is not configured")
        try:
            response = self._client.chat.completions.create(model=self.model_name, messages=messages, temperature=0.2, stream=True)
        except Exception as exc:
            raise RuntimeError(f"provider_error: {exc}")
        for chunk in response:
            try:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    yield {"delta": delta.content}
                usage = getattr(chunk, "usage", None)
                if usage:
                    yield {"usage": self._usage(usage)}
            except (AttributeError, IndexError):
                continue

    def health_check(self):
        from datetime import datetime, timezone
        status = "verified" if self.available and self.verified else "configured_unverified" if self.available else "not_configured"
        return {**self.capability(), "health_status": status, "last_health_check": datetime.now(timezone.utc)}

    def capability(self):
        return {"provider": self.provider_name, "model": self.model_name, "configured": self.available, "verified": self.verified,
            "streaming_support": True, "usage_support": True, "health_status": "verified" if self.available and self.verified else "not_checked" if self.available else "not_configured",
            "last_health_check": None, "pricing_metadata_available": False}
