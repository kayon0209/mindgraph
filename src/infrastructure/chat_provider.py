from __future__ import annotations

from typing import Any, Iterable

from zhipuai import ZhipuAI

from infrastructure.openai_compatible_provider import NormalizedProviderError

# F6：显式超时 + 有界重试（SDK 默认 max_retries=3 且无显式超时，曾出现长时间挂起）
_REQUEST_TIMEOUT = 90.0
_MAX_RETRIES = 2

# F3：把 SDK 异常归一为带 .code 的错误，chat_service 通过 getattr(exc, "code") 取用
_ERROR_CODE_MAP = {
    "400": "invalid_request",
    "401": "authentication_failed",
    "402": "quota_exhausted",
    "403": "authentication_failed",
    "404": "model_not_found",
    "429": "rate_limited",
}


def _normalize_error(exc: Exception) -> NormalizedProviderError:
    raw_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    code_str = str(raw_code) if raw_code is not None else ""
    if code_str in _ERROR_CODE_MAP:
        return NormalizedProviderError(_ERROR_CODE_MAP[code_str], str(exc))
    if code_str.isdigit() and int(code_str) >= 500:
        return NormalizedProviderError("provider_unavailable", str(exc))
    if isinstance(exc, TimeoutError) or "timeout" in str(exc).lower() or "timed out" in str(exc).lower():
        return NormalizedProviderError("timeout", f"Provider request timed out: {exc}")
    return NormalizedProviderError("provider_error", str(exc))


class ZhipuChatProvider:
    provider_name = "zhipu"
    def __init__(self, api_key: str, model_name: str, verified: bool = False) -> None:
        self.api_key = api_key
        self.model_name = model_name
        self.verified = verified
        self._client = ZhipuAI(api_key=api_key, timeout=_REQUEST_TIMEOUT, max_retries=_MAX_RETRIES) if api_key else None

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
            raise NormalizedProviderError("provider_not_configured", "Zhipu provider is not configured")
        try:
            response = self._client.chat.completions.create(model=self.model_name, messages=messages, temperature=0.2)
        except Exception as exc:
            raise _normalize_error(exc) from exc
        if not response.choices or not response.choices[0].message:
            raise NormalizedProviderError("provider_error", "Empty response from model")
        return (response.choices[0].message.content or "").strip(), self._usage(getattr(response, "usage", None))

    def stream(self, messages: list[dict[str, str]]) -> Iterable[dict[str, Any]]:
        if not self._client:
            raise NormalizedProviderError("provider_not_configured", "Zhipu provider is not configured")
        try:
            response = self._client.chat.completions.create(model=self.model_name, messages=messages, temperature=0.2, stream=True)
        except Exception as exc:
            raise _normalize_error(exc) from exc
        try:
            iterator = iter(response)
        except TypeError as exc:
            raise NormalizedProviderError("provider_error", str(exc)) from exc
        while True:
            try:
                chunk = next(iterator)
            except StopIteration:
                break
            except Exception as exc:
                raise _normalize_error(exc) from exc
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
