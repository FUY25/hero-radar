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


class LlmProviderTest(unittest.TestCase):
    def test_fake_provider_returns_json_objects_in_order_and_records_calls(self) -> None:
        from pipeline.decision.llm_provider import FakeLLMProvider

        provider = FakeLLMProvider([{"ok": True}, {"ok": False}])

        self.assertEqual(
            provider.complete_json(task="a", prompt_version="v1", input_payload={"n": 1})[
                "ok"
            ],
            True,
        )
        self.assertEqual(
            provider.complete_json(task="b", prompt_version="v1", input_payload={"n": 2})[
                "ok"
            ],
            False,
        )
        self.assertEqual([call["task"] for call in provider.calls], ["a", "b"])

    def test_deepseek_provider_builds_openai_payload_without_secret_in_repr(self) -> None:
        from pipeline.decision.llm_provider import DeepSeekProvider

        provider = DeepSeekProvider(
            api_key="secret-value",
            model="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
        )
        payload = provider.build_payload(
            system_prompt="Return JSON.",
            user_payload={"hello": "world"},
            temperature=0,
        )

        self.assertEqual(payload["model"], "deepseek-v4-flash")
        self.assertEqual(payload["response_format"]["type"], "json_object")
        self.assertEqual(payload["temperature"], 0)
        self.assertIn("json", payload["messages"][0]["content"].lower())
        self.assertIn('"hello": "world"', payload["messages"][1]["content"])
        self.assertNotIn("secret-value", repr(provider))

    def test_deepseek_defaults_are_v4_flash_and_pro_can_be_explicit(self) -> None:
        from pipeline.decision.llm_provider import DEFAULT_DEEPSEEK_MODEL, DeepSeekProvider

        default_provider = DeepSeekProvider(api_key="x")
        pro_provider = DeepSeekProvider(api_key="x", model="deepseek-v4-pro")

        self.assertEqual(DEFAULT_DEEPSEEK_MODEL, "deepseek-v4-flash")
        self.assertEqual(default_provider.model, "deepseek-v4-flash")
        self.assertEqual(pro_provider.model, "deepseek-v4-pro")
        self.assertNotEqual(default_provider.model, "deepseek-chat")

    def test_deepseek_retries_transient_read_timeout(self) -> None:
        from pipeline.decision.llm_provider import DeepSeekProvider

        calls = []

        def fake_urlopen(request, timeout):
            calls.append({"url": request.full_url, "timeout": timeout})
            if len(calls) == 1:
                raise TimeoutError("read timed out")
            return FakeHttpResponse(
                {"choices": [{"message": {"content": json.dumps({"ok": True})}}]}
            )

        provider = DeepSeekProvider(api_key="secret", timeout=1, max_retries=1)

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = provider.complete_json(
                task="smoke",
                prompt_version="v1",
                input_payload={"hello": "world"},
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(calls), 2)

    def test_deepseek_retries_empty_json_content(self) -> None:
        from pipeline.decision.llm_provider import DeepSeekProvider

        calls = []

        def fake_urlopen(request, timeout):
            calls.append({"url": request.full_url, "timeout": timeout})
            if len(calls) == 1:
                return FakeHttpResponse({"choices": [{"message": {"content": ""}}]})
            return FakeHttpResponse(
                {"choices": [{"message": {"content": json.dumps({"ok": True})}}]}
            )

        provider = DeepSeekProvider(api_key="secret", timeout=1, max_retries=1)

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = provider.complete_json(
                task="smoke",
                prompt_version="v1",
                input_payload={"hello": "world"},
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
