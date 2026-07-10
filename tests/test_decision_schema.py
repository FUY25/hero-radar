import sqlite3
import unittest

from pipeline.decision.schema import (
    begin_decision_run,
    finish_decision_run,
    init_decision_db,
    reset_decision_stage,
)


class DecisionSchemaTest(unittest.TestCase):
    def test_init_creates_expected_tables(self):
        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)

        rows = conn.execute(
            "select name from sqlite_master where type = 'table' order by name"
        ).fetchall()
        names = {row[0] for row in rows}

        self.assertIn("decision_runs", names)
        self.assertIn("entities", names)
        self.assertIn("alias_links", names)
        self.assertIn("potential_candidates", names)
        self.assertIn("edge_watch_candidates", names)
        self.assertIn("backfill_jobs", names)
        self.assertIn("entity_mentions", names)
        self.assertIn("evidence_rows", names)
        self.assertIn("api_cache", names)

    def test_run_lifecycle_is_idempotent_by_run_id(self):
        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)

        begin_decision_run(
            conn,
            run_id="decision_20260531",
            source_snapshot_run_id="source_1",
            config_hash="config-a",
            rule_version="rules-v1",
        )
        begin_decision_run(
            conn,
            run_id="decision_20260531",
            source_snapshot_run_id="source_1",
            config_hash="config-a",
            rule_version="rules-v1",
        )

        count = conn.execute("select count(*) from decision_runs").fetchone()[0]
        self.assertEqual(count, 1)

        finish_decision_run(conn, run_id="decision_20260531", status="ok", note="done")
        row = conn.execute(
            "select status, note from decision_runs where run_id = ?",
            ("decision_20260531",),
        ).fetchone()
        self.assertEqual(row, ("ok", "done"))

    def test_reset_stage_removes_run_scoped_outputs(self):
        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)

        conn.execute(
            "insert into potential_candidates(entity_id, run_id, level, fired_families_json, first_trigger_at) values (?, ?, ?, ?, ?)",
            ("entity:one", "run-a", "potential", "[]", "2026-05-31T00:00:00Z"),
        )
        conn.execute(
            "insert into evidence_rows(entity_id, canonical_entity, alias, source, event_at, metric_name, metric_value, family, rule_id, rule_version, signal_label, historical_safety, note, raw_url_or_ref, run_id) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "entity:one",
                "One",
                "One",
                "github_trending",
                "2026-05-31T00:00:00Z",
                "stars_today",
                "1200",
                "github",
                "github_trending_daily_potential",
                "rules-v1",
                "early_trigger",
                "snapshot_only",
                "passed",
                "item:1",
                "run-a",
            ),
        )

        reset_decision_stage(
            conn,
            run_id="run-a",
            tables=["potential_candidates", "evidence_rows"],
        )

        self.assertEqual(
            conn.execute("select count(*) from potential_candidates").fetchone()[0],
            0,
        )
        self.assertEqual(
            conn.execute("select count(*) from evidence_rows").fetchone()[0],
            0,
        )

    def test_init_creates_layer2_feed_tables(self):
        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)

        names = {
            row[0]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }

        self.assertIn("l2_feed_runs", names)
        self.assertIn("l2_candidate_groups", names)
        self.assertIn("l2_scout_results", names)
        self.assertIn("l2_scores", names)
        self.assertIn("l2_scoring_investigations", names)
        self.assertIn("l2_deepdive_briefs", names)
        self.assertIn("deepdive_reports", names)
        self.assertIn("l2_feed_items", names)
        self.assertIn("feed_feedback", names)
        self.assertIn("l2_stage_events", names)

    def test_init_creates_layer2_stage_events_table(self):
        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)

        conn.execute(
            """
            insert into l2_stage_events(
              feed_run_id, group_id, stage, status, error_type, error,
              metadata_json, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "l2-run",
                "group:repo",
                "scoring",
                "scoring_error",
                "ValueError",
                "bad response",
                "{}",
                "2026-06-01T00:00:00Z",
            ),
        )

        row = conn.execute(
            "select status, error_type from l2_stage_events where feed_run_id = ?",
            ("l2-run",),
        ).fetchone()
        self.assertEqual(row, ("scoring_error", "ValueError"))

    def test_init_adds_attributable_context_and_model_call_telemetry(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)

        score_columns = {
            row[1] for row in conn.execute("pragma table_info(l2_scores)").fetchall()
        }
        self.assertTrue(
            {"supporting_claims_json", "negative_claims_json", "known_gaps_json"}
            <= score_columns
        )
        investigation_columns = {
            row[1]
            for row in conn.execute(
                "pragma table_info(l2_scoring_investigations)"
            ).fetchall()
        }
        self.assertTrue(
            {
                "observation_trace_json",
                "context_manifests_json",
                "raw_tool_results_json",
            }
            <= investigation_columns
        )
        model_call_columns = {
            row[1] for row in conn.execute("pragma table_info(l2_model_calls)").fetchall()
        }
        self.assertTrue(
            {
                "feed_run_id",
                "group_id",
                "component",
                "turn_index",
                "request_fingerprint",
                "context_policy_version",
                "prompt_tokens",
                "completion_tokens",
                "temperature",
                "max_output_tokens",
                "context_manifest_json",
            }
            <= model_call_columns
        )

    def test_reset_stage_allows_layer2_run_scoped_tables(self):
        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        conn.execute(
            """
            insert into l2_feed_runs(feed_run_id, decision_run_id, started_at, status, config_hash, model_profile_json, note)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            ("l2-run", "decision-run", "2026-05-31T00:00:00Z", "ok", "hash", "{}", ""),
        )
        conn.execute(
            """
            insert into l2_candidate_groups(group_id, feed_run_id, canonical_entity_id, canonical_name, canonical_key, canonical_link, member_entity_ids_json, level, source_families_json, evidence_hash, grouping_reason_json, context_json)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "group:one",
                "l2-run",
                "entity:one",
                "One",
                "github:owner/repo",
                "https://github.com/owner/repo",
                '["entity:one"]',
                "potential",
                '["github"]',
                "evidence-hash",
                "{}",
                "{}",
            ),
        )
        conn.execute(
            """
            insert into l2_scores(feed_run_id, group_id, l2_score, axes_json, primary_reason, topic_tags_json, rationale_short, caveats_json, provider, model, prompt_version, cache_key)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("l2-run", "group:one", 80, "{}", "Workflow Shift", "[]", "Good", "[]", "kimi", "kimi-k2.5", "v1", "cache"),
        )
        conn.execute(
            """
            insert into l2_stage_events(feed_run_id, group_id, stage, status, error_type, error, metadata_json, created_at)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "l2-run",
                "group:one",
                "scoring",
                "scoring_ok",
                "",
                "",
                "{}",
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.execute(
            """
            insert into l2_scoring_investigations(
              feed_run_id, group_id, status, trace_json, tool_trace_json,
              provider, model, prompt_version, cache_key, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "l2-run",
                "group:one",
                "ok",
                "{}",
                "[]",
                "kimi",
                "kimi-k2.5",
                "v2",
                "trace-cache",
                "2026-06-01T00:00:00Z",
            ),
        )
        conn.execute(
            """
            insert into l2_deepdive_briefs(
              feed_run_id, group_id, status, brief_json, language,
              provider, model, prompt_version, cache_key, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "l2-run",
                "group:one",
                "ok",
                "{}",
                "zh",
                "kimi",
                "kimi-k2.5",
                "v2",
                "brief-cache",
                "2026-06-01T00:00:00Z",
            ),
        )

        reset_decision_stage(
            conn,
            run_id="l2-run",
            tables=[
                "l2_candidate_groups",
                "l2_scores",
                "l2_scoring_investigations",
                "l2_deepdive_briefs",
                "l2_stage_events",
            ],
        )

        self.assertEqual(conn.execute("select count(*) from l2_candidate_groups").fetchone()[0], 0)
        self.assertEqual(conn.execute("select count(*) from l2_scores").fetchone()[0], 0)
        self.assertEqual(conn.execute("select count(*) from l2_scoring_investigations").fetchone()[0], 0)
        self.assertEqual(conn.execute("select count(*) from l2_deepdive_briefs").fetchone()[0], 0)
        self.assertEqual(conn.execute("select count(*) from l2_stage_events").fetchone()[0], 0)
        self.assertEqual(conn.execute("select count(*) from l2_feed_runs").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
