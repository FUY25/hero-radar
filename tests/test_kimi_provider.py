from __future__ import annotations

import json
import unittest
from unittest import mock


class FakeHttpResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class KimiProviderTest(unittest.TestCase):
    def test_kimi_provider_builds_openai_json_payload_without_secret_in_repr(self):
        from pipeline.decision.kimi_provider import KimiProvider

        provider = KimiProvider(
            api_key="secret-value",
            model="kimi-k2.5",
            base_url="https://api.moonshot.ai/v1",
        )
        payload = provider.build_payload(
            system_prompt="Score candidates as JSON.",
            user_payload={"candidate": "owner/repo"},
            temperature=0,
        )

        self.assertEqual(payload["model"], "kimi-k2.5")
        self.assertEqual(payload["response_format"]["type"], "json_object")
        self.assertEqual(payload["temperature"], 0)
        self.assertIn('"candidate": "owner/repo"', payload["messages"][1]["content"])
        self.assertNotIn("secret-value", repr(provider))

    def test_kimi_provider_reads_kimi_or_moonshot_env_key(self):
        from pipeline.decision.kimi_provider import KimiProvider

        with mock.patch.dict("os.environ", {"KIMI_API_KEY": "kimi-secret"}, clear=True):
            self.assertEqual(KimiProvider().api_key, "kimi-secret")
        with mock.patch.dict(
            "os.environ", {"MOONSHOT_API_KEY": "moon-secret"}, clear=True
        ):
            self.assertEqual(KimiProvider().api_key, "moon-secret")

    def test_kimi_provider_completes_json_and_retries_empty_content(self):
        from pipeline.decision.kimi_provider import KimiProvider

        calls = []

        def fake_urlopen(request, timeout):
            calls.append({"url": request.full_url, "timeout": timeout})
            if len(calls) == 1:
                return FakeHttpResponse({"choices": [{"message": {"content": ""}}]})
            return FakeHttpResponse(
                {"choices": [{"message": {"content": json.dumps({"ok": True})}}]}
            )

        provider = KimiProvider(api_key="secret", timeout=1, max_retries=1)
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = provider.complete_json(
                task="layer2_smoke",
                prompt_version="v1",
                input_payload={"hello": "world"},
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(calls), 2)

    def test_kimi_web_search_client_uses_builtin_tool_without_json_mode(self):
        from pipeline.decision.kimi_provider import KimiProvider, KimiWebSearchClient

        captured_payloads = []

        def fake_urlopen(request, timeout):
            payload = json.loads(request.data.decode("utf-8"))
            captured_payloads.append(payload)
            return FakeHttpResponse(
                {"choices": [{"message": {"content": "Search summary for owner/repo"}}]}
            )

        provider = KimiProvider(api_key="secret", timeout=1, max_retries=0)
        client = KimiWebSearchClient(provider=provider)

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = client.search(query="owner/repo agent workflow", max_results=3)

        self.assertEqual(result["content"], "Search summary for owner/repo")
        self.assertEqual(
            captured_payloads[0]["tools"],
            [{"type": "builtin_function", "function": {"name": "$web_search"}}],
        )
        self.assertNotIn("response_format", captured_payloads[0])
