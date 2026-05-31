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


if __name__ == "__main__":
    unittest.main()
