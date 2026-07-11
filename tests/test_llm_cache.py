from __future__ import annotations

import sqlite3
import unittest
from dataclasses import replace

from pipeline.decision.schema import init_decision_db


class LlmCacheTest(unittest.TestCase):
    def test_cache_key_is_stable_and_prompt_version_scoped(self) -> None:
        from pipeline.decision.llm_cache import cache_key_for

        key_a = cache_key_for(
            provider="deepseek",
            model="deepseek-v4-flash",
            prompt_version="x-v1",
            task="x_stage1",
            input_payload={"b": 2, "a": 1},
        )
        key_b = cache_key_for(
            provider="deepseek",
            model="deepseek-v4-flash",
            prompt_version="x-v1",
            task="x_stage1",
            input_payload={"a": 1, "b": 2},
        )
        key_c = cache_key_for(
            provider="deepseek",
            model="deepseek-v4-flash",
            prompt_version="x-v2",
            task="x_stage1",
            input_payload={"a": 1, "b": 2},
        )

        self.assertEqual(key_a, key_b)
        self.assertNotEqual(key_a, key_c)

    def test_get_or_store_llm_cache_round_trips_json(self) -> None:
        from pipeline.decision.llm_cache import get_cached_response, store_cached_response

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)

        key = store_cached_response(
            conn,
            provider="deepseek",
            model="deepseek-v4-flash",
            prompt_version="hn-v1",
            task="hn_classifier",
            input_payload={"item_id": 1},
            request_payload={"messages": []},
            response_payload={"projectness": "project"},
            status="ok",
        )

        cached = get_cached_response(conn, key)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["response_json"]["projectness"], "project")
        self.assertEqual(cached["request_json"]["messages"], [])
        self.assertEqual(cached["prompt_version"], "hn-v1")
        self.assertEqual(cached["status"], "ok")

    def test_store_updates_status_and_error_for_same_key(self) -> None:
        from pipeline.decision.llm_cache import get_cached_response, store_cached_response

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)

        key = store_cached_response(
            conn,
            provider="deepseek",
            model="deepseek-v4-flash",
            prompt_version="x-stage1-v1",
            task="x_stage1",
            input_payload={"batch": [1]},
            request_payload={"messages": ["old"]},
            response_payload={"triage": []},
            status="ok",
        )
        same_key = store_cached_response(
            conn,
            provider="deepseek",
            model="deepseek-v4-flash",
            prompt_version="x-stage1-v1",
            task="x_stage1",
            input_payload={"batch": [1]},
            request_payload={"messages": ["old"]},
            response_payload={"error": "bad json"},
            status="error",
            error="bad json",
        )

        self.assertEqual(key, same_key)
        cached = get_cached_response(conn, key)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["status"], "error")
        self.assertEqual(cached["error"], "bad json")
        self.assertEqual(cached["response_json"]["error"], "bad json")

    def test_complete_request_fingerprint_covers_every_model_contract_dimension(self) -> None:
        from pipeline.decision.request_contract import LLMRequestContract

        base = LLMRequestContract.create(
            provider="kimi",
            model="kimi-k2.5",
            task="layer2_scoring_investigator_turn",
            system_prompt="Score the candidate.",
            active_tools=[
                {
                    "name": "fetch_github_file",
                    "description": "Read one allowlisted repository file.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                }
            ],
            output_schema={"type": "object", "required": ["action"]},
            context_policy_version="scoring-context-v1",
            input_payload={"candidate": {"group_id": "github:owner/repo"}},
            actual_temperature=1,
            max_output_tokens=1800,
            response_format={"type": "json_object"},
            provider_options={"thinking": {"type": "disabled"}},
            prompt_version="scoring-v1",
            output_schema_version="score-v1",
            tool_registry_version="tools-v1",
            active_tool_versions=("fetch_github_file@1",),
        )

        self.assertEqual(base.fingerprint(), replace(base).fingerprint())
        variants = [
            replace(base, system_prompt="Changed system policy."),
            replace(
                base,
                active_tools=base.active_tools
                + (
                    {
                        "name": "web_search",
                        "description": "Search the web.",
                        "input_schema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                            "additionalProperties": False,
                        },
                    },
                ),
            ),
            replace(base, output_schema={"type": "object", "required": ["score"]}),
            replace(base, context_policy_version="scoring-context-v2"),
            replace(base, input_payload={"candidate": {"group_id": "name:other"}}),
            replace(base, actual_temperature=0),
            replace(base, max_output_tokens=1600),
            replace(base, response_format={"type": "json_schema"}),
            replace(base, provider_options={"thinking": {"type": "enabled"}}),
            replace(base, active_tool_versions=("fetch_github_file@2",)),
            replace(base, model="kimi-k2.6"),
            replace(base, task="layer2_brief_writer"),
        ]
        self.assertEqual(
            len({variant.fingerprint() for variant in variants}), len(variants)
        )
        self.assertTrue(
            all(variant.fingerprint() != base.fingerprint() for variant in variants)
        )

        fingerprint_payload = base.fingerprint_payload()
        self.assertEqual(
            set(fingerprint_payload),
            {
                "provider",
                "model",
                "task",
                "system_prompt_hash",
                "tool_schema_hash",
                "output_schema_hash",
                "context_policy_version",
                "input_payload",
                "actual_temperature",
                "max_output_tokens",
                "response_format",
                "provider_options",
            },
        )

    def test_complete_request_contract_omits_secrets_from_key_and_stored_request(self) -> None:
        from pipeline.decision.request_contract import LLMRequestContract

        def contract(secret: str) -> LLMRequestContract:
            return LLMRequestContract.create(
                provider="kimi",
                model="kimi-k2.5",
                task="layer2_scoring_investigator_turn",
                system_prompt=f"Bearer {secret} must never be retained",
                active_tools=[],
                output_schema={"type": "object"},
                context_policy_version="v1",
                input_payload={
                    "candidate": {"name": "safe"},
                    "api_key": secret,
                    "headers": {"Authorization": f"Bearer {secret}"},
                },
                actual_temperature=1,
                max_output_tokens=1800,
                response_format={"type": "json_object"},
            )

        first = contract("sk-one-secret")
        second = contract("sk-two-secret")

        self.assertEqual(first.fingerprint(), second.fingerprint())
        stored = first.stored_request()
        serialized = str(stored)
        self.assertNotIn("sk-one-secret", serialized)
        self.assertNotIn("Authorization", serialized)
        self.assertNotIn("api_key", serialized)

    def test_complete_request_contract_can_key_and_store_cache_rows(self) -> None:
        from pipeline.decision.llm_cache import (
            cache_key_for,
            get_cached_response,
            store_cached_response,
        )
        from pipeline.decision.request_contract import LLMRequestContract

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)
        contract = LLMRequestContract.create(
            provider="kimi",
            model="kimi-k2.5",
            task="layer2_scoring_investigator_turn",
            system_prompt="Score it.",
            active_tools=[],
            output_schema={"type": "object"},
            context_policy_version="v1",
            input_payload={"candidate": {"name": "safe"}},
            actual_temperature=1,
            max_output_tokens=1800,
            response_format={"type": "json_object"},
        )

        key = store_cached_response(
            conn,
            provider=contract.provider,
            model=contract.model,
            prompt_version="diagnostic-v1",
            task=contract.task,
            input_payload=contract.input_payload,
            request_payload=contract.stored_request(),
            response_payload={"action": "final"},
            status="ok",
            request_contract=contract,
        )

        self.assertEqual(key, cache_key_for(request_contract=contract))
        cached = get_cached_response(conn, key)
        self.assertEqual(cached["request_json"]["system_prompt_hash"], contract.system_prompt_hash)
        self.assertEqual(cached["request_json"]["max_output_tokens"], 1800)

        with self.assertRaisesRegex(ValueError, "provider"):
            store_cached_response(
                conn,
                provider="different-provider",
                model=contract.model,
                prompt_version="diagnostic-v1",
                task=contract.task,
                input_payload=contract.input_payload,
                request_payload={},
                response_payload={},
                status="ok",
                request_contract=contract,
            )

    def test_provider_contract_uses_actual_payload_sampling_and_output_policy(self) -> None:
        from pipeline.decision.request_contract import LLMRequestContract

        class Provider:
            provider_name = "kimi"
            model = "kimi-k2.5"

            def build_payload(self, **_kwargs):
                return {
                    "temperature": 1,
                    "max_tokens": 1800,
                    "response_format": {"type": "json_object"},
                }

        contract = LLMRequestContract.for_provider(
            Provider(),
            task="layer2_scoring_investigator_turn",
            system_prompt="Score it.",
            active_tools=[],
            output_schema={},
            context_policy_version="v1",
            input_payload={},
        )

        self.assertEqual(contract.actual_temperature, 1)
        self.assertEqual(contract.max_output_tokens, 1800)
        self.assertEqual(contract.response_format["type"], "json_object")


if __name__ == "__main__":
    unittest.main()
