from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from pipeline.decision.schema import init_decision_db


class Layer2RunnerTest(unittest.TestCase):
    def make_db_with_potentials(self, db_path: Path, names: list[str]) -> None:
        conn = sqlite3.connect(db_path)
        init_decision_db(conn)
        conn.execute(
            "insert into decision_runs(run_id, source_snapshot_run_id, started_at, completed_at, status, config_hash, rule_version, note) values (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "decision-run",
                "source-run",
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:01:00Z",
                "ok",
                "hash",
                "rules-v1",
                "",
            ),
        )
        for index, name in enumerate(names):
            entity_id = f"entity:{index}"
            conn.execute(
                "insert into entities(entity_id, canonical_entity, canonical_key, key_type, first_seen, aliases_json, source_item_ids_json) values (?, ?, ?, ?, ?, ?, ?)",
                (
                    entity_id,
                    name,
                    f"github:{name}",
                    "github",
                    "2026-06-01T00:00:00Z",
                    "[]",
                    "[]",
                ),
            )
            conn.execute(
                "insert into potential_candidates(entity_id, run_id, level, fired_families_json, first_trigger_at) values (?, ?, ?, ?, ?)",
                (
                    entity_id,
                    "decision-run",
                    "potential",
                    '["github"]',
                    "2026-06-01T00:00:00Z",
                ),
            )
        conn.commit()
        conn.close()

    def valid_score_response(self, reason: str = "Workflow Shift") -> dict:
        return {
            "axes": {
                "momentum": 80,
                "workflow_shift": 80,
                "technical_substance": 80,
                "adoption_path": 80,
                "confidence": 80,
                "derivative_news_penalty": 0,
            },
            "primary_reason": reason,
            "topic_tags": ["agent workflow"],
            "rationale_short": "Worth reading.",
            "caveats": [],
        }

    def test_default_feed_run_id_is_stable_prefix(self):
        from pipeline.decision.run_layer2_feed import default_feed_run_id

        self.assertEqual(
            default_feed_run_id("2026-05-31T12:34:56Z"),
            "l2_20260531T123456",
        )

    def test_run_layer2_with_fake_provider_writes_feed_run(self):
        from pipeline.decision.llm_provider import FakeLLMProvider
        from pipeline.decision.run_layer2_feed import run_layer2_feed

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            conn = sqlite3.connect(db_path)
            init_decision_db(conn)
            conn.executescript(
                """
                create table items (
                    id integer primary key,
                    source text not null,
                    name text not null,
                    url text,
                    description text,
                    metadata_json text not null,
                    raw_json text not null
                );
                """
            )
            conn.execute(
                "insert into decision_runs(run_id, source_snapshot_run_id, started_at, completed_at, status, config_hash, rule_version, note) values (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "decision-run",
                    "source-run",
                    "2026-05-31T00:00:00Z",
                    "2026-05-31T00:01:00Z",
                    "ok",
                    "hash",
                    "rules-v1",
                    "",
                ),
            )
            conn.execute(
                "insert into entities(entity_id, canonical_entity, canonical_key, key_type, first_seen, aliases_json, source_item_ids_json) values (?, ?, ?, ?, ?, ?, ?)",
                (
                    "entity:repo",
                    "owner/repo",
                    "github:owner/repo",
                    "github",
                    "2026-05-31T00:00:00Z",
                    "[]",
                    "[]",
                ),
            )
            conn.execute(
                "insert into potential_candidates(entity_id, run_id, level, fired_families_json, first_trigger_at) values (?, ?, ?, ?, ?)",
                (
                    "entity:repo",
                    "decision-run",
                    "potential",
                    '["github"]',
                    "2026-05-31T00:00:00Z",
                ),
            )
            conn.commit()
            conn.close()

            provider = FakeLLMProvider(
                [
                    {
                        "axes": {
                            "momentum": 80,
                            "workflow_shift": 80,
                            "technical_substance": 80,
                            "adoption_path": 80,
                            "confidence": 80,
                            "derivative_news_penalty": 0,
                        },
                        "primary_reason": "Workflow Shift",
                        "topic_tags": ["agent workflow"],
                        "rationale_short": "Worth reading.",
                        "caveats": [],
                    },
                    {"tool_requests": []},
                    {
                        "summary": "Summary",
                        "why_now": "Now",
                        "what_changed": "Changed",
                        "evidence": ["Evidence"],
                        "adoption_path": "Path",
                        "risks": [],
                        "open_questions": [],
                        "recommended_action": "read",
                    },
                ]
            )
            summary = run_layer2_feed(
                db_path=db_path,
                decision_run_id="decision-run",
                feed_run_id="l2-test",
                now="2026-05-31T12:00:00Z",
                provider=provider,
                config={"max_deepdives_per_run": 1, "deepdive_min_l2_score": 0},
            )

            self.assertTrue(summary["ok"])
            self.assertEqual(summary["feed_run_id"], "l2-test")
            conn = sqlite3.connect(db_path)
            self.assertEqual(
                conn.execute("select count(*) from l2_feed_runs").fetchone()[0], 1
            )
            self.assertEqual(
                conn.execute("select count(*) from l2_scores").fetchone()[0], 1
            )
            conn.close()

    def test_run_layer2_continues_when_one_scoring_candidate_fails(self):
        from pipeline.decision.llm_provider import FakeLLMProvider
        from pipeline.decision.run_layer2_feed import run_layer2_feed

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            conn = sqlite3.connect(db_path)
            init_decision_db(conn)
            conn.execute(
                "insert into decision_runs(run_id, source_snapshot_run_id, started_at, completed_at, status, config_hash, rule_version, note) values (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "decision-run",
                    "source-run",
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:01:00Z",
                    "ok",
                    "hash",
                    "rules-v1",
                    "",
                ),
            )
            for entity_id, name in [
                ("entity:bad", "bad/repo"),
                ("entity:good", "good/repo"),
            ]:
                conn.execute(
                    "insert into entities(entity_id, canonical_entity, canonical_key, key_type, first_seen, aliases_json, source_item_ids_json) values (?, ?, ?, ?, ?, ?, ?)",
                    (
                        entity_id,
                        name,
                        f"github:{name}",
                        "github",
                        "2026-06-01T00:00:00Z",
                        "[]",
                        "[]",
                    ),
                )
                conn.execute(
                    "insert into potential_candidates(entity_id, run_id, level, fired_families_json, first_trigger_at) values (?, ?, ?, ?, ?)",
                    (
                        entity_id,
                        "decision-run",
                        "potential",
                        '["github"]',
                        "2026-06-01T00:00:00Z",
                    ),
                )
            conn.commit()
            conn.close()

            provider = FakeLLMProvider(
                [
                    {"axes": {"momentum": "not-a-number"}},
                    {
                        "axes": {
                            "momentum": 80,
                            "workflow_shift": 80,
                            "technical_substance": 80,
                            "adoption_path": 80,
                            "confidence": 80,
                            "derivative_news_penalty": 0,
                        },
                        "primary_reason": "Workflow Shift",
                        "topic_tags": ["agent workflow"],
                        "rationale_short": "Worth reading.",
                        "caveats": [],
                    },
                    {"tool_requests": []},
                    {
                        "summary": "Summary",
                        "why_now": "Now",
                        "what_changed": "Changed",
                        "evidence": ["Evidence"],
                        "adoption_path": "Path",
                        "risks": [],
                        "open_questions": [],
                        "recommended_action": "read",
                    },
                ]
            )

            summary = run_layer2_feed(
                db_path=db_path,
                decision_run_id="decision-run",
                feed_run_id="l2-errors",
                now="2026-06-01T12:00:00Z",
                provider=provider,
                config={"max_deepdives_per_run": 1, "deepdive_min_l2_score": 0},
            )

            self.assertTrue(summary["ok"])
            self.assertEqual(summary["status"], "ok_with_errors")
            self.assertEqual(summary["scored"], 1)
            self.assertEqual(summary["errors"], 1)

            conn = sqlite3.connect(db_path)
            run_row = conn.execute(
                "select status, note from l2_feed_runs where feed_run_id = ?",
                ("l2-errors",),
            ).fetchone()
            statuses = [
                row[0]
                for row in conn.execute(
                    "select status from l2_stage_events where feed_run_id = ? order by id",
                    ("l2-errors",),
                ).fetchall()
            ]
            conn.close()

            self.assertEqual(run_row[0], "ok_with_errors")
            self.assertIn("scoring_error", statuses)
            self.assertIn("scoring_ok", statuses)
            self.assertIn("error_counts", run_row[1])

    def test_run_layer2_applies_total_scoring_cap(self):
        from pipeline.decision.llm_provider import FakeLLMProvider
        from pipeline.decision.run_layer2_feed import run_layer2_feed

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            self.make_db_with_potentials(db_path, ["one/repo", "two/repo"])
            provider = FakeLLMProvider(
                [
                    self.valid_score_response("One"),
                    self.valid_score_response("Two"),
                ]
            )

            summary = run_layer2_feed(
                db_path=db_path,
                decision_run_id="decision-run",
                feed_run_id="l2-total-cap",
                now="2026-06-01T12:00:00Z",
                provider=provider,
                config={
                    "max_scored_candidates": 2,
                    "max_total_scoring_candidates": 1,
                    "max_deepdives_per_run": 0,
                },
            )

            conn = sqlite3.connect(db_path)
            stage_rows = conn.execute(
                "select stage, status from l2_stage_events where feed_run_id = ? order by id",
                ("l2-total-cap",),
            ).fetchall()
            conn.close()

        self.assertEqual(summary["scored"], 1)
        self.assertIn(("scoring", "pending_budget"), stage_rows)

    def test_run_layer2_marks_stale_running_runs_before_starting(self):
        from pipeline.decision.llm_provider import FakeLLMProvider
        from pipeline.decision.run_layer2_feed import run_layer2_feed

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            self.make_db_with_potentials(db_path, ["one/repo"])
            conn = sqlite3.connect(db_path)
            conn.execute(
                "insert into l2_feed_runs(feed_run_id, decision_run_id, started_at, completed_at, status, config_hash, model_profile_json, note) values (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "l2-stale",
                    "decision-run",
                    "2026-06-01T00:00:00Z",
                    None,
                    "running",
                    "manual",
                    "{}",
                    "",
                ),
            )
            conn.commit()
            conn.close()

            provider = FakeLLMProvider([self.valid_score_response()])
            run_layer2_feed(
                db_path=db_path,
                decision_run_id="decision-run",
                feed_run_id="l2-new",
                now="2026-06-01T12:00:00Z",
                provider=provider,
                config={
                    "max_deepdives_per_run": 0,
                    "finalize_stale_running_before": "2026-06-01T01:00:00Z",
                },
            )

            conn = sqlite3.connect(db_path)
            old_status, old_note = conn.execute(
                "select status, note from l2_feed_runs where feed_run_id = ?",
                ("l2-stale",),
            ).fetchone()
            conn.close()

        self.assertEqual(old_status, "error")
        self.assertIn("stale running", old_note)

    def test_cli_no_deepdive_and_timeout_knobs_map_to_config(self):
        from pipeline.decision.run_layer2_feed import config_from_args, parse_args

        args = parse_args(
            [
                "--no-deepdive",
                "--scout-timeout-seconds",
                "7",
                "--max-total-scoring-candidates",
                "2",
            ]
        )
        config = config_from_args(args)

        self.assertEqual(config["max_deepdives_per_run"], 0)
        self.assertEqual(config["scout_timeout_seconds"], 7)
        self.assertEqual(config["max_total_scoring_candidates"], 2)
