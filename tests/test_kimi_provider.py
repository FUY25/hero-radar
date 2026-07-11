from __future__ import annotations

import json
import tempfile
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
    def test_complete_json_applies_configured_max_output_tokens(self):
        from pipeline.decision.kimi_provider import KimiProvider

        provider = KimiProvider(
            api_key="secret",
            model="kimi-k2.5",
            max_output_tokens=1000,
        )

        payload = provider.build_payload(
            system_prompt="Return JSON.",
            user_payload={"candidate": "repo"},
        )

        self.assertEqual(provider.max_output_tokens, 1000)
        self.assertEqual(provider.actual_temperature, 1)
        self.assertEqual(provider.response_format, {"type": "json_object"})
        self.assertEqual(payload["max_tokens"], 1000)

    def test_kimi_provider_builds_json_payload_with_k2_accepted_temperature(self):
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
        self.assertEqual(payload["temperature"], 1)
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

    def test_kimi_provider_exposes_response_usage_for_eval_telemetry(self):
        from pipeline.decision.kimi_provider import KimiProvider

        provider = KimiProvider(api_key="secret", timeout=1, max_retries=0)
        response = FakeHttpResponse(
            {
                "choices": [{"message": {"content": json.dumps({"ok": True})}}],
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 30,
                    "total_tokens": 150,
                },
            }
        )

        with mock.patch("urllib.request.urlopen", return_value=response):
            provider.complete_json(
                task="layer2_eval",
                prompt_version="layer2-scoring-investigator-v2",
                input_payload={"candidate": "repo"},
            )

        self.assertEqual(
            provider.last_usage,
            {
                "prompt_tokens": 120,
                "completion_tokens": 30,
                "total_tokens": 150,
            },
        )

    def test_eval_provider_estimates_cost_only_from_explicit_configured_rates(self):
        from pipeline.decision.layer2_eval_provider import RateLimitedKimiEvalProvider
        from pipeline.decision.rate_limit import StartRateLimiter

        class InnerProvider:
            model = "kimi-k2.5"
            base_url = "https://api.moonshot.ai/v1"
            api_key = "configured"
            timeout = 90
            max_retries = 2
            max_output_tokens = 3000
            actual_temperature = 1
            response_format = {"type": "json_object"}
            last_usage = None
            last_cost = None

            def complete_json(self, **_call):
                self.last_usage = {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                    "prompt_tokens_details": {"cached_tokens": 20},
                }
                self.last_cost = None
                return {"ok": True}

        provider = RateLimitedKimiEvalProvider(
            limiter=StartRateLimiter(0),
            api_key="configured",
            input_cost_per_million=1.0,
            cached_input_cost_per_million=0.1,
            output_cost_per_million=2.0,
        )
        provider._provider = InnerProvider()

        provider.complete_json(
            task="layer2_eval",
            prompt_version="layer2-scoring-investigator-v2",
            input_payload={"candidate": "repo"},
        )

        self.assertEqual(provider.last_cost["amount"], 0.000182)
        self.assertEqual(provider.last_cost["source"], "configured_rate_estimate")

    def test_eval_provider_rate_limits_and_accumulates_retry_attempt_usage(self):
        from pipeline.decision.layer2_eval_provider import RateLimitedKimiEvalProvider
        from pipeline.decision.rate_limit import StartRateLimiter

        class RetryingInner:
            model = "kimi-k2.5"
            base_url = "https://api.moonshot.ai/v1"
            api_key = "configured"
            timeout = 90
            max_retries = 0
            max_output_tokens = 3000
            actual_temperature = 1
            response_format = {"type": "json_object"}
            last_usage = None
            last_cost = None

            def __init__(self):
                self.calls = 0

            def complete_json(self, **_call):
                self.calls += 1
                self.last_usage = {
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "total_tokens": 110,
                }
                self.last_cost = None
                if self.calls == 1:
                    raise RuntimeError("first attempt failed after response")
                return {"ok": True}

        waits = []
        provider = RateLimitedKimiEvalProvider(
            limiter=StartRateLimiter(1, clock=lambda: 0, sleeper=waits.append),
            api_key="configured",
            max_retries=1,
            retry_backoff_seconds=0,
            input_cost_per_million=1.0,
            output_cost_per_million=2.0,
        )
        provider._provider = RetryingInner()

        result = provider.complete_json(
            task="layer2_eval",
            prompt_version="layer2-scoring-investigator-v2",
            input_payload={"candidate": "repo"},
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(provider.last_usage["total_tokens"], 220)
        self.assertEqual(provider.last_cost["amount"], 0.00024)
        self.assertEqual([row["status"] for row in provider.last_attempts], ["error", "ok"])
        self.assertEqual(waits, [1.0])

    def test_eval_provider_does_not_retry_deterministic_empty_content(self):
        from pipeline.decision.kimi_provider import KimiEmptyContentError
        from pipeline.decision.layer2_eval_provider import RateLimitedKimiEvalProvider
        from pipeline.decision.rate_limit import StartRateLimiter

        class EmptyInner:
            model = "kimi-k2.5"
            base_url = "https://api.moonshot.cn/v1"
            api_key = "configured"
            timeout = 90
            max_retries = 0
            max_output_tokens = 3000
            actual_temperature = 1
            response_format = {"type": "json_object"}
            last_usage = None
            last_cost = None
            last_response_diagnostics = None

            def __init__(self):
                self.calls = 0

            def complete_json(self, **_call):
                self.calls += 1
                self.last_usage = {
                    "prompt_tokens": 100,
                    "completion_tokens": 3000,
                    "total_tokens": 3100,
                }
                self.last_response_diagnostics = {
                    "finish_reason": "length",
                    "content_chars": 0,
                    "reasoning_chars": 12000,
                }
                raise KimiEmptyContentError("empty after output limit")

        provider = RateLimitedKimiEvalProvider(
            limiter=StartRateLimiter(0),
            api_key="configured",
            max_retries=2,
            retry_backoff_seconds=0,
            input_cost_per_million=4.0,
            output_cost_per_million=21.0,
            cost_currency="CNY",
        )
        inner = EmptyInner()
        provider._provider = inner

        with self.assertRaises(KimiEmptyContentError):
            provider.complete_json(
                task="layer2_eval",
                prompt_version="layer2-scoring-investigator-v2",
                input_payload={"candidate": "repo"},
            )

        self.assertEqual(inner.calls, 1)
        self.assertEqual(len(provider.last_attempts), 1)
        self.assertEqual(
            provider.last_attempts[0]["response_diagnostics"]["finish_reason"],
            "length",
        )

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

    def test_kimi_provider_reads_local_secrets_when_env_absent(self):
        from pathlib import Path

        from pipeline.decision import kimi_provider
        from pipeline.decision.kimi_provider import KimiProvider

        with tempfile.TemporaryDirectory() as tmpdir:
            secrets_path = Path(tmpdir) / "secrets.local.json"
            secrets_path.write_text(
                json.dumps(
                    {
                        "kimi": {
                            "api_key": "local-kimi-secret",
                            "base_url": "https://api.moonshot.cn/v1",
                        }
                    }
                )
            )
            with mock.patch.object(kimi_provider, "LOCAL_SECRETS_PATH", secrets_path):
                with mock.patch.dict("os.environ", {}, clear=True):
                    provider = KimiProvider()

        self.assertEqual(provider.api_key, "local-kimi-secret")
        self.assertEqual(provider.base_url, "https://api.moonshot.cn/v1")
        self.assertNotIn("local-kimi-secret", repr(provider))

    def test_kimi_provider_env_overrides_local_secrets(self):
        from pathlib import Path

        from pipeline.decision import kimi_provider
        from pipeline.decision.kimi_provider import KimiProvider

        with tempfile.TemporaryDirectory() as tmpdir:
            secrets_path = Path(tmpdir) / "secrets.local.json"
            secrets_path.write_text(
                json.dumps(
                    {
                        "kimi": {
                            "api_key": "local-kimi-secret",
                            "base_url": "https://api.moonshot.cn/v1",
                        }
                    }
                )
            )
            with mock.patch.object(kimi_provider, "LOCAL_SECRETS_PATH", secrets_path):
                with mock.patch.dict(
                    "os.environ",
                    {
                        "KIMI_API_KEY": "env-kimi-secret",
                        "KIMI_BASE_URL": "https://api.moonshot.ai/v1",
                    },
                    clear=True,
                ):
                    provider = KimiProvider()

        self.assertEqual(provider.api_key, "env-kimi-secret")
        self.assertEqual(provider.base_url, "https://api.moonshot.ai/v1")

    def test_kimi_provider_handshake_returns_sanitized_model_count(self):
        from pipeline.decision.kimi_provider import KimiProvider

        def fake_urlopen(request, timeout):
            self.assertEqual(request.full_url, "https://api.moonshot.cn/v1/models")
            self.assertIn("Bearer secret", request.headers["Authorization"])
            return FakeHttpResponse({"data": [{"id": "kimi-k2.5"}, {"id": "kimi-k2.6"}]})

        provider = KimiProvider(
            api_key="secret",
            base_url="https://api.moonshot.cn/v1",
            timeout=1,
        )
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = provider.handshake()

        self.assertEqual(
            result,
            {
                "ok": True,
                "base_url_host": "api.moonshot.cn",
                "key_configured": True,
                "models_count": 2,
            },
        )
        self.assertNotIn("secret", json.dumps(result))
