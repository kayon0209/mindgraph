import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from infrastructure.anthropic_provider import AnthropicProvider
from infrastructure.openai_compatible_provider import NormalizedProviderError, OpenAICompatibleProvider
from infrastructure.provider_registry import ProviderRegistry


class FakeStreamContext:
    def __init__(self, response): self.response = response
    def __enter__(self): return self.response
    def __exit__(self, *args): return False


class ProviderTests(unittest.TestCase):
    def test_openai_compatible_complete_usage_and_capability(self):
        provider = OpenAICompatibleProvider("deepseek", "https://example.test", "key", "model")
        response = Mock(status_code=200); response.json.return_value = {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}}
        with patch("httpx.post", return_value=response): text, usage = provider.complete([{"role": "user", "content": "x"}])
        self.assertEqual(text, "ok"); self.assertEqual(usage["total_tokens"], 3); self.assertFalse(provider.capability()["pricing_metadata_available"])

    def test_openai_stream_and_malformed_response(self):
        provider = OpenAICompatibleProvider("deepseek", "https://example.test", "key", "model")
        response = Mock(status_code=200); response.iter_lines.return_value = ['data: {"choices":[{"delta":{"content":"a"}}]}', 'data: {"usage":{"total_tokens":2}}', "data: [DONE]"]
        with patch.object(provider, "_post", return_value=FakeStreamContext(response)): items = list(provider.stream([{"role": "user", "content": "x"}]))
        self.assertEqual(items[0]["delta"], "a"); self.assertEqual(items[1]["usage"]["total_tokens"], 2)
        bad = Mock(status_code=200); bad.json.return_value = {}
        with patch("httpx.post", return_value=bad):
            with self.assertRaisesRegex(NormalizedProviderError, "Malformed"): provider.complete([])

    def test_registry_switches_only_allowlisted_models(self):
        provider = OpenAICompatibleProvider(
            "deepseek", "https://example.test", "key", "flash", configured_models=["flash", "pro"]
        )
        registry = ProviderRegistry([provider], "deepseek")
        self.assertEqual(registry.get("deepseek", "pro").model_name, "pro")
        self.assertEqual([item["model"] for item in registry.capabilities()], ["flash", "pro"])
        with self.assertRaises(NormalizedProviderError) as caught:
            registry.get("deepseek", "unknown")
        self.assertEqual(caught.exception.code, "model_not_found")

    def test_error_normalization_missing_key_timeout_rate_limit(self):
        missing = OpenAICompatibleProvider("x", "", "", "m")
        with self.assertRaises(NormalizedProviderError) as caught: missing.complete([])
        self.assertEqual(caught.exception.code, "provider_not_configured")
        provider = OpenAICompatibleProvider("x", "https://x", "k", "m", max_retries=0)
        with patch("httpx.post", side_effect=httpx.ReadTimeout("timeout")):
            with self.assertRaises(NormalizedProviderError) as caught: provider.complete([])
        self.assertEqual(caught.exception.code, "timeout")
        limited = Mock(status_code=429)
        with patch("httpx.post", return_value=limited):
            with self.assertRaises(NormalizedProviderError) as caught: provider.complete([])
        self.assertEqual(caught.exception.code, "rate_limited")

    def test_anthropic_mock_and_registry(self):
        anthropic = AnthropicProvider("key", "claude-test")
        response = Mock(status_code=200); response.json.return_value = {"content": [{"type": "text", "text": "ok"}], "usage": {"input_tokens": 2, "output_tokens": 1}}
        with patch("httpx.post", return_value=response): self.assertEqual(anthropic.complete([{"role": "user", "content": "x"}])[0], "ok")
        registry = ProviderRegistry([anthropic], "anthropic")
        self.assertEqual(registry.get().provider_name, "anthropic"); self.assertFalse(registry.capabilities()[0]["verified"])


@unittest.skipUnless(os.getenv("RUN_DEEPSEEK_INTEGRATION") == "true", "set RUN_DEEPSEEK_INTEGRATION=true")
class DeepSeekIntegrationTests(unittest.TestCase):
    def test_real_complete_and_stream(self):
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
        provider = OpenAICompatibleProvider(os.getenv("OPENAI_COMPAT_PROVIDER_NAME", "deepseek"), os.getenv("OPENAI_COMPAT_BASE_URL", ""), os.getenv("OPENAI_COMPAT_API_KEY", ""), os.getenv("OPENAI_COMPAT_MODEL", ""), 60, 0)
        text, usage = provider.complete([{"role": "user", "content": "只回答：测试"}]); self.assertTrue(text); self.assertEqual(usage["usage_source"], "provider_reported")
        items = list(provider.stream([{"role": "user", "content": "只回答：测试"}])); self.assertTrue(any(item.get("delta") for item in items))


if __name__ == "__main__": unittest.main()
