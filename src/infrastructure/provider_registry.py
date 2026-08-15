from __future__ import annotations

from domain.errors import ProviderUnavailableError


class ProviderRegistry:
    def __init__(self, providers, default_provider: str) -> None:
        self.providers = {provider.provider_name: provider for provider in providers}
        self.default_provider = default_provider

    def get(self, provider_name: str | None = None, model_name: str | None = None):
        name = provider_name or self.default_provider
        provider = self.providers.get(name)
        if provider is None:
            raise ProviderUnavailableError(f"Provider is not implemented: {name}")
        if model_name and model_name != provider.model_name:
            if not hasattr(provider, "with_model"):
                raise ProviderUnavailableError(f"Model is not enabled for provider: {name}")
            return provider.with_model(model_name)
        return provider

    def capabilities(self):
        output = []
        for provider in self.providers.values():
            models = getattr(provider, "configured_models", [provider.model_name])
            for model in models:
                selected = provider.with_model(model) if model != provider.model_name and hasattr(provider, "with_model") else provider
                output.append(selected.capability())
        return output
