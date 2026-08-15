from __future__ import annotations

from typing import Any, Iterable, Protocol


class ChatProvider(Protocol):
    model_name: str
    provider_name: str

    @property
    def available(self) -> bool: ...

    def complete(self, messages: list[dict[str, str]]) -> tuple[str, dict[str, Any]]: ...

    def stream(self, messages: list[dict[str, str]]) -> Iterable[dict[str, Any]]: ...

    def health_check(self) -> dict[str, Any]: ...

    def capability(self) -> dict[str, Any]: ...
