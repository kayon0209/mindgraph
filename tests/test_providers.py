import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from infrastructure.anthropic_provider import AnthropicProvider
from infrastructure.chat_provider import ZhipuChatProvider
from infrastructure.openai_compatible_provider import NormalizedProviderError, OpenAICompatibleProvider
from infrastructure.provider_registry import ProviderRegistry
from infrastructure.zhipu_compatible_client import ZhipuCompatibleClient


class FakeStreamContext:
    def __init__(self, response): self.response = response
    def __enter__(self): return self.response
    def __exit__(self, *args): return False


class ProviderTests(unittest.TestCase):
    def test_zhipu_compatible_client_uses_documented_openai_endpoints(self):
        client = ZhipuCompatibleClient("key")
        embedding_response = Mock(status_code=200)
        embedding_response.json.return_value = {
            "data": [
                {"index": 1, "embedding": [0.2]},
                {"index": 0, "embedding": [0.1]},
            ]
        }
        with patch("httpx.post", return_value=embedding_response) as posted:
            result = client.embeddings.create(input=["first", "second"], model="embedding-3")
        self.assertEqual(
            posted.call_args.args[0],
            "https://open.bigmodel.cn/api/paas/v4/embeddings",
        )
        self.assertEqual(posted.call_args.kwargs["headers"]["Authorization"], "Bearer key")
        self.assertEqual([item.index for item in result.data], [1, 0])

        chat_response = Mock(status_code=200)
        chat_response.json.return_value = {
            "choices": [{"message": {"content": "answer"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        }
        with patch("httpx.post", return_value=chat_response) as posted:
            completion = client.chat.completions.create(
                model="glm-4.7", messages=[{"role": "user", "content": "question"}], temperature=0.2
            )
        self.assertEqual(
            posted.call_args.args[0],
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        )
        self.assertEqual(completion.choices[0].message.content, "answer")
        self.assertEqual(completion.usage.total_tokens, 3)

    def test_zhipu_chat_provider_uses_openai_compatible_transport(self):
        provider = ZhipuChatProvider("key", "glm-4.7")
        response = Mock(status_code=200)
        response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        with patch("httpx.post", return_value=response) as posted:
            text, _usage = provider.complete([{"role": "user", "content": "x"}])
        self.assertEqual(text, "ok")
        self.assertEqual(provider.provider_name, "zhipu")
        self.assertEqual(
            posted.call_args.args[0],
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        )
    def test_openai_compatible_complete_usage_and_capability(self):
        provider = OpenAICompatibleProvider("deepseek", "https://example.test", "key", "model")
        response = Mock(status_code=200); response.json.return_value = {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}}
        with patch("httpx.post", return_value=response): text, usage = provider.complete([{"role": "user", "content": "x"}])
        self.assertEqual(text, "ok"); self.assertEqual(usage["total_tokens"], 3); self.assertFalse(provider.capability()["pricing_metadata_available"])

    def test_openai_stream_and_malformed_response(self):
        provider = OpenAICompatibleProvider("deepseek", "https://example.test", "key", "model")
        response = Mock(status_code=200); response.iter_lines.return_value = ['data: {"choices":[{"delta":{"content":"a"}}]}', 'data: {"usage":{"total_tokens":2}}', "data: [DONE]"]
        client = Mock()
        with patch.object(
            provider,
            "_post",
            return_value=(client, FakeStreamContext(response)),
        ):
            items = list(provider.stream([{"role": "user", "content": "x"}]))
        self.assertEqual(items[0]["delta"], "a"); self.assertEqual(items[1]["usage"]["total_tokens"], 2)
        client.close.assert_called_once_with()
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

    def test_providers_never_send_function_calling_params(self):
        """ADR-003 红线（P0-1）：provider 保持 text-in/text-out——adapter 发出的
        HTTP 载荷不得携带 tools / tool_choice，消息内不得出现 tool_calls /
        tool_call_id / role=tool。出现即失败，杜绝 function calling 面。"""
        forbidden = ("tools", "tool_choice", "tool_calls", "tool_call_id")

        def assert_clean(payload, origin):
            for key in forbidden:
                self.assertNotIn(key, payload, f"{origin} 载荷携带 {key}：{payload}")
            for message in payload.get("messages", []) or ([] if "messages" not in payload else payload["messages"]):
                self.assertNotIn("tool_calls", message, f"{origin} message 携带 tool_calls：{message}")
                self.assertNotIn("tool_call_id", message, f"{origin} message 携带 tool_call_id：{message}")
                self.assertNotEqual(message.get("role"), "tool", f"{origin} message 出现 tool 角色：{message}")

        # OpenAI 兼容 adapter：complete 与 stream 的载荷
        openai_provider = OpenAICompatibleProvider("deepseek", "https://example.test", "key", "model")
        ok_response = Mock(status_code=200); ok_response.json.return_value = {"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 3}}
        with patch("httpx.post", return_value=ok_response) as posted:
            openai_provider.complete([{"role": "user", "content": "x"}])
        assert_clean(posted.call_args.kwargs["json"], "openai_complete")

        stream_response = Mock(status_code=200); stream_response.iter_lines.return_value = ['data: {"choices":[{"delta":{"content":"a"}}]}', "data: [DONE]"]
        with patch.object(openai_provider, "_post", return_value=(Mock(), FakeStreamContext(stream_response))) as posted_payload:
            list(openai_provider.stream([{"role": "user", "content": "x"}]))
        assert_clean(posted_payload.call_args.args[0], "openai_stream")

        # Anthropic adapter：complete 的载荷
        anthropic = AnthropicProvider("key", "claude-test")
        anthropic_response = Mock(status_code=200); anthropic_response.json.return_value = {"content": [{"type": "text", "text": "ok"}], "usage": {"input_tokens": 2, "output_tokens": 1}}
        with patch("httpx.post", return_value=anthropic_response) as posted:
            anthropic.complete([{"role": "user", "content": "x"}])
        assert_clean(posted.call_args.kwargs["json"], "anthropic_complete")


@unittest.skipUnless(os.getenv("RUN_DEEPSEEK_INTEGRATION") == "true", "set RUN_DEEPSEEK_INTEGRATION=true")
class DeepSeekIntegrationTests(unittest.TestCase):
    def test_real_complete_and_stream(self):
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
        provider = OpenAICompatibleProvider(os.getenv("OPENAI_COMPAT_PROVIDER_NAME", "deepseek"), os.getenv("OPENAI_COMPAT_BASE_URL", ""), os.getenv("OPENAI_COMPAT_API_KEY", ""), os.getenv("OPENAI_COMPAT_MODEL", ""), 60, 0)
        text, usage = provider.complete([{"role": "user", "content": "只回答：测试"}]); self.assertTrue(text); self.assertEqual(usage["usage_source"], "provider_reported")
        items = list(provider.stream([{"role": "user", "content": "只回答：测试"}])); self.assertTrue(any(item.get("delta") for item in items))


if __name__ == "__main__": unittest.main()
