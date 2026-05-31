from __future__ import annotations

import unittest


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


if __name__ == "__main__":
    unittest.main()
