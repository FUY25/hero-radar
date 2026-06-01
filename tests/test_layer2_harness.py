from __future__ import annotations

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
        self.assertEqual([row[0] for row in rows], ["llm_call_started", "llm_call_error"])
        self.assertNotIn("secret-token", rows[1][1])
        self.assertEqual(stage_summary(conn, "l2-run")["error_total"], 0)


if __name__ == "__main__":
    unittest.main()
