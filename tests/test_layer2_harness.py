from __future__ import annotations

import json
import sqlite3
import unittest

from pipeline.decision.schema import init_decision_db


class Layer2HarnessTest(unittest.TestCase):
    def test_record_stage_event_sanitizes_secret_and_summarizes_counts(self):
        from pipeline.decision.layer2_harness import record_stage_event, stage_summary

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)

        record_stage_event(
            conn,
            feed_run_id="l2-run",
            group_id="group:repo",
            stage="scoring",
            status="scoring_error",
            error=RuntimeError("Bearer secret-token failed"),
            metadata={"attempt": 1},
        )
        record_stage_event(
            conn,
            feed_run_id="l2-run",
            group_id="group:ok",
            stage="scoring",
            status="scoring_ok",
        )

        row = conn.execute(
            "select error from l2_stage_events where group_id = ?",
            ("group:repo",),
        ).fetchone()
        summary = stage_summary(conn, "l2-run")

        self.assertNotIn("secret-token", row[0])
        self.assertEqual(summary["stage_counts"]["scoring_error"], 1)
        self.assertEqual(summary["stage_counts"]["scoring_ok"], 1)
        self.assertEqual(summary["error_counts"]["scoring"], 1)

    def test_final_run_status_distinguishes_ok_with_errors(self):
        from pipeline.decision.layer2_harness import final_run_status

        self.assertEqual(
            final_run_status({"error_total": 0, "success_total": 2}), "ok"
        )
        self.assertEqual(
            final_run_status({"error_total": 1, "success_total": 2}),
            "ok_with_errors",
        )
        self.assertEqual(
            final_run_status({"error_total": 1, "success_total": 0}), "error"
        )

    def test_telemetry_provider_records_llm_call_started_and_ok(self):
        from pipeline.decision.layer2_harness import (
            TelemetryLLMProvider,
            stage_summary,
        )

        class Provider:
            provider_name = "fake"
            model = "fake-json"
            timeout = 90

            def complete_json(self, **kwargs):
                return {"ok": True, "score": 88}

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        provider = Provider()
        wrapped = TelemetryLLMProvider(
            provider,
            conn=conn,
            feed_run_id="l2-run",
            group_id="group:repo",
            stage="scoring",
            timeout_seconds=12,
        )

        result = wrapped.complete_json(
            task="layer2_scoring",
            prompt_version="v1",
            input_payload={},
        )

        rows = conn.execute(
            "select status, metadata_json from l2_stage_events order by id"
        ).fetchall()
        self.assertEqual(result, {"ok": True, "score": 88})
        self.assertEqual([row[0] for row in rows], ["llm_call_started", "llm_call_ok"])
        self.assertIn('"timeout_seconds":12', rows[0][1].replace(" ", ""))
        self.assertEqual(provider.timeout, 90)
        self.assertEqual(stage_summary(conn, "l2-run")["error_total"], 0)

    def test_telemetry_provider_persists_logical_model_call_contract_and_usage(self):
        from pipeline.decision.layer2_harness import (
            ModelCallTelemetryContext,
            TelemetryLLMProvider,
        )
        from pipeline.decision.request_contract import LLMRequestContract

        class Provider:
            provider_name = "fake"
            model = "fake-json"
            actual_temperature = 0.25
            max_output_tokens = 900
            response_format = {"type": "json_object"}

            def complete_json(self, **kwargs):
                self.last_usage = {
                    "prompt_tokens": 321,
                    "completion_tokens": 45,
                    "prompt_tokens_details": {"cached_tokens": 120},
                    "total_tokens": 366,
                }
                return {"ok": True}

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        provider = Provider()
        wrapped = TelemetryLLMProvider(
            provider,
            conn=conn,
            feed_run_id="l2-run",
            group_id="group:repo",
            stage="scoring",
        )
        contract = LLMRequestContract.for_provider(
            provider,
            task="layer2_scoring_investigator_turn",
            system_prompt="Score the candidate.",
            active_tools=[{"name": "read_evidence_rows"}],
            output_schema={"type": "object"},
            context_policy_version="context-v2",
            input_payload={"candidate": {"group_id": "group:repo"}},
            prompt_version="scorer-v2",
            output_schema_version="turn-v2",
            tool_registry_version="registry-v2",
        )

        result = wrapped.complete_json(
            task=contract.task,
            prompt_version=contract.prompt_version,
            input_payload=contract.input_payload,
            system_prompt=contract.system_prompt,
            request_contract=contract,
            call_context=ModelCallTelemetryContext(
                component="scoring_agent",
                turn_index=2,
                attempt=1,
                estimated_tokens={"system_prompt": 80, "total": 640},
                context_manifest={
                    "included_evidence_ids": ["evidence:7"],
                    "api_key": "secret-token",
                    "diagnostic": "Bearer secret-token was configured",
                },
            ),
        )

        row = conn.execute(
            """
            select component, turn_index, attempt, request_fingerprint, cache_key,
                   prompt_version, output_schema_version, tool_registry_version,
                   context_policy_version, status, prompt_tokens, completion_tokens,
                   cached_input_tokens, total_tokens, temperature, max_output_tokens,
                   estimated_tokens_json, context_manifest_json
              from l2_model_calls
            """
        ).fetchone()
        self.assertEqual(result, {"ok": True})
        self.assertEqual(
            row[:16],
            (
                "scoring_agent",
                2,
                1,
                contract.fingerprint(),
                contract.fingerprint(),
                "scorer-v2",
                "turn-v2",
                "registry-v2",
                "context-v2",
                "ok",
                321,
                45,
                120,
                366,
                0.25,
                900,
            ),
        )
        self.assertEqual(
            json.loads(row[16]), {"system_prompt": 80, "total": 640}
        )
        self.assertEqual(
            json.loads(row[17]),
            {
                "diagnostic": "[redacted] was configured",
                "included_evidence_ids": ["evidence:7"],
            },
        )

    def test_telemetry_provider_records_llm_call_error_without_secret(self):
        from pipeline.decision.layer2_harness import (
            TelemetryLLMProvider,
            stage_summary,
        )

        class Provider:
            provider_name = "fake"
            model = "fake-json"

            def complete_json(self, **kwargs):
                raise RuntimeError("Bearer secret-token timed out")

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        wrapped = TelemetryLLMProvider(
            Provider(),
            conn=conn,
            feed_run_id="l2-run",
            group_id="group:repo",
            stage="deepdive",
        )

        with self.assertRaises(RuntimeError):
            wrapped.complete_json(
                task="layer2_deepdive_plan",
                prompt_version="v1",
                input_payload={},
            )

        rows = conn.execute(
            "select status, error from l2_stage_events order by id"
        ).fetchall()
        model_call = conn.execute(
            """
            select status, request_fingerprint, cache_key, error_type, error
              from l2_model_calls
            """
        ).fetchone()
        self.assertEqual([row[0] for row in rows], ["llm_call_started", "llm_call_error"])
        self.assertNotIn("secret-token", rows[1][1])
        self.assertEqual(model_call[0], "error")
        self.assertTrue(model_call[1])
        self.assertEqual(model_call[1], model_call[2])
        self.assertEqual(model_call[3], "RuntimeError")
        self.assertNotIn("secret-token", model_call[4])
        self.assertEqual(stage_summary(conn, "l2-run")["error_total"], 0)

    def test_cached_telemetry_provider_reuses_json_response_and_records_hit(self):
        from pipeline.decision.layer2_harness import CachedTelemetryLLMProvider

        class Provider:
            provider_name = "fake"
            model = "fake-json"

            def __init__(self):
                self.calls = 0

            def complete_json(self, **kwargs):
                self.calls += 1
                return {"ok": True, "score": 88}

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        first_provider = Provider()
        first = CachedTelemetryLLMProvider(
            first_provider,
            conn=conn,
            feed_run_id="l2-run",
            group_id="group:repo",
            stage="scoring",
        )
        second_provider = Provider()
        second = CachedTelemetryLLMProvider(
            second_provider,
            conn=conn,
            feed_run_id="l2-run-2",
            group_id="group:repo",
            stage="scoring",
        )

        payload = {"group_id": "group:repo", "evidence_hash": "hash"}
        self.assertEqual(
            first.complete_json(
                task="layer2_scoring_investigator_turn",
                prompt_version="v1",
                input_payload=payload,
            ),
            {"ok": True, "score": 88},
        )
        self.assertEqual(
            second.complete_json(
                task="layer2_scoring_investigator_turn",
                prompt_version="v1",
                input_payload=payload,
            ),
            {"ok": True, "score": 88},
        )

        statuses = [
            row[0]
            for row in conn.execute(
                "select status from l2_stage_events order by id"
            ).fetchall()
        ]
        self.assertEqual(first_provider.calls, 1)
        self.assertEqual(second_provider.calls, 0)
        self.assertIn("llm_cache_miss", statuses)
        self.assertIn("llm_cache_hit", statuses)
        self.assertEqual(conn.execute("select count(*) from llm_cache").fetchone()[0], 1)

    def test_cached_telemetry_persists_miss_outcome_and_hit_as_logical_calls(self):
        from pipeline.decision.layer2_harness import CachedTelemetryLLMProvider
        from pipeline.decision.request_contract import LLMRequestContract

        class Provider:
            provider_name = "fake"
            model = "fake-json"
            actual_temperature = 0
            max_output_tokens = 700
            response_format = {"type": "json_object"}

            def __init__(self):
                self.calls = 0

            def complete_json(self, **kwargs):
                self.calls += 1
                self.last_usage = {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                }
                return {"score": 91}

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        first_provider = Provider()
        second_provider = Provider()
        first = CachedTelemetryLLMProvider(
            first_provider,
            conn=conn,
            feed_run_id="l2-run-1",
            group_id="group:repo",
            stage="scoring",
        )
        second = CachedTelemetryLLMProvider(
            second_provider,
            conn=conn,
            feed_run_id="l2-run-2",
            group_id="group:repo",
            stage="scoring",
        )
        contract = LLMRequestContract.for_provider(
            first_provider,
            task="layer2_scoring_investigator_turn",
            system_prompt="Score.",
            active_tools=[],
            output_schema={"type": "object"},
            context_policy_version="context-v2",
            input_payload={"candidate": "group:repo"},
            prompt_version="scorer-v2",
        )
        call_context = {
            "component": "scoring_agent",
            "turn_index": 1,
            "attempt": 1,
            "estimated_tokens": {"total": 300},
            "context_manifest": {"policy": "bounded"},
        }

        first.complete_json(
            task=contract.task,
            prompt_version=contract.prompt_version,
            input_payload=contract.input_payload,
            system_prompt=contract.system_prompt,
            request_contract=contract,
            call_context=call_context,
        )
        second.complete_json(
            task=contract.task,
            prompt_version=contract.prompt_version,
            input_payload=contract.input_payload,
            system_prompt=contract.system_prompt,
            request_contract=contract,
            call_context=call_context,
        )

        rows = conn.execute(
            """
            select feed_run_id, status, request_fingerprint, cache_key,
                   prompt_tokens, completion_tokens, total_tokens
              from l2_model_calls order by id
            """
        ).fetchall()
        self.assertEqual(first_provider.calls, 1)
        self.assertEqual(second_provider.calls, 0)
        self.assertEqual(
            rows,
            [
                (
                    "l2-run-1",
                    "ok",
                    contract.fingerprint(),
                    contract.fingerprint(),
                    100,
                    20,
                    120,
                ),
                (
                    "l2-run-2",
                    "cache_hit",
                    contract.fingerprint(),
                    contract.fingerprint(),
                    None,
                    None,
                    None,
                ),
            ],
        )

    def test_cached_telemetry_key_changes_with_prompt_evidence_and_context_hashes(self):
        from pipeline.decision.layer2_harness import CachedTelemetryLLMProvider

        class Provider:
            provider_name = "fake"
            model = "fake-json"

            def __init__(self):
                self.calls = 0

            def complete_json(self, **kwargs):
                self.calls += 1
                return {"call": self.calls}

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        provider = Provider()
        wrapped = CachedTelemetryLLMProvider(
            provider,
            conn=conn,
            feed_run_id="l2-run",
            group_id="group:repo",
            stage="scoring",
        )
        payload = {
            "group_id": "group:repo",
            "evidence_hash": "evidence-a",
            "context_hash": "context-a",
        }

        self.assertEqual(
            wrapped.complete_json(
                task="layer2_scoring_investigator_turn",
                prompt_version="route-react-v1",
                input_payload=payload,
            ),
            {"call": 1},
        )
        self.assertEqual(
            wrapped.complete_json(
                task="layer2_scoring_investigator_turn",
                prompt_version="route-react-v1",
                input_payload={**payload, "evidence_hash": "evidence-b"},
            ),
            {"call": 2},
        )
        self.assertEqual(
            wrapped.complete_json(
                task="layer2_scoring_investigator_turn",
                prompt_version="route-react-v2",
                input_payload=payload,
            ),
            {"call": 3},
        )
        self.assertEqual(
            wrapped.complete_json(
                task="layer2_scoring_investigator_turn",
                prompt_version="route-react-v1",
                input_payload=payload,
            ),
            {"call": 1},
        )

        self.assertEqual(provider.calls, 3)
        self.assertEqual(conn.execute("select count(*) from llm_cache").fetchone()[0], 3)

    def test_cached_telemetry_full_contract_invalidates_prompt_schema_and_sampling(self):
        from dataclasses import replace

        from pipeline.decision.layer2_harness import CachedTelemetryLLMProvider
        from pipeline.decision.request_contract import LLMRequestContract

        class Provider:
            provider_name = "fake"
            model = "fake-json"
            actual_temperature = 1
            max_output_tokens = 1800
            response_format = {"type": "json_object"}

            def __init__(self):
                self.calls = 0

            def complete_json(self, **kwargs):
                self.calls += 1
                return {"call": self.calls}

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        provider = Provider()
        wrapped = CachedTelemetryLLMProvider(
            provider,
            conn=conn,
            feed_run_id="l2-run",
            group_id="group:repo",
            stage="scoring",
        )
        base = LLMRequestContract.for_provider(
            provider,
            task="layer2_scoring_investigator_turn",
            system_prompt="Score this candidate.",
            active_tools=[],
            output_schema={"type": "object", "required": ["action"]},
            context_policy_version="context-v1",
            input_payload={"candidate": {"group_id": "github:owner/repo"}},
        )

        self.assertEqual(
            wrapped.complete_json(
                task=base.task,
                prompt_version="v1",
                input_payload=base.input_payload,
                system_prompt=base.system_prompt,
                request_contract=base,
            ),
            {"call": 1},
        )
        self.assertEqual(
            wrapped.complete_json(
                task=base.task,
                prompt_version="v1",
                input_payload=base.input_payload,
                system_prompt=base.system_prompt,
                request_contract=base,
            ),
            {"call": 1},
        )
        variants = [
            replace(base, system_prompt="Changed policy."),
            replace(base, active_tools=({"name": "read_evidence_rows"},)),
            replace(base, output_schema={"type": "object", "required": ["score"]}),
            replace(base, context_policy_version="context-v2"),
            replace(base, actual_temperature=0),
            replace(base, max_output_tokens=1200),
            replace(base, response_format={"type": "json_schema"}),
        ]
        for expected_call, contract in enumerate(variants, start=2):
            self.assertEqual(
                wrapped.complete_json(
                    task=contract.task,
                    prompt_version="v1",
                    input_payload=contract.input_payload,
                    system_prompt=contract.system_prompt,
                    request_contract=contract,
                ),
                {"call": expected_call},
            )

        self.assertEqual(provider.calls, 1 + len(variants))
        requests = [
            row[0]
            for row in conn.execute("select request_json from llm_cache order by created_at")
        ]
        self.assertTrue(all("system_prompt_hash" in request for request in requests))

    def test_cached_telemetry_rejects_contract_that_does_not_match_actual_call(self):
        from pipeline.decision.layer2_harness import CachedTelemetryLLMProvider
        from pipeline.decision.request_contract import LLMRequestContract

        class Provider:
            provider_name = "fake"
            model = "fake-json"
            actual_temperature = 0
            max_output_tokens = 800
            response_format = {"type": "json_object"}

            def complete_json(self, **kwargs):
                raise AssertionError("provider must not be called")

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        wrapped = CachedTelemetryLLMProvider(
            Provider(),
            conn=conn,
            feed_run_id="l2-run",
            group_id="group:repo",
            stage="scoring",
        )
        contract = LLMRequestContract.for_provider(
            wrapped._provider,
            task="expected-task",
            system_prompt="system",
            active_tools=[],
            output_schema={},
            context_policy_version="v1",
            input_payload={"candidate": "expected"},
        )

        with self.assertRaisesRegex(ValueError, "task"):
            wrapped.complete_json(
                task="different-task",
                prompt_version="v1",
                input_payload=contract.input_payload,
                system_prompt=contract.system_prompt,
                request_contract=contract,
            )


if __name__ == "__main__":
    unittest.main()
