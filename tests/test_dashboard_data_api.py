from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class DashboardDataApiTest(unittest.TestCase):
    def make_db(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp = tempfile.TemporaryDirectory()
        db_path = Path(temp.name) / "hero.sqlite"
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            create table snapshots (
                id integer primary key autoincrement,
                run_id text not null,
                source text not null,
                fetched_at text not null,
                status text not null,
                item_count integer not null,
                error text
            );
            create table items (
                id integer primary key autoincrement,
                run_id text not null,
                snapshot_id integer not null,
                source text not null,
                external_id text not null,
                name text not null,
                url text not null,
                fetched_at text not null,
                heat real,
                velocity real,
                acceleration real,
                source_rank integer,
                description text,
                metadata_json text not null,
                raw_json text not null
            );
            create table scores (
                run_id text not null,
                item_id integer not null,
                rank integer not null,
                score real not null,
                components_json text not null
            );
            """
        )
        conn.execute(
            "insert into snapshots(run_id, source, fetched_at, status, item_count, error) values (?, ?, ?, ?, ?, ?)",
            ("source-run", "github_trending", "2026-05-31T10:00:00Z", "ok", 1, None),
        )
        snapshot_id = conn.execute("select id from snapshots").fetchone()[0]
        conn.execute(
            """
            insert into items(run_id, snapshot_id, source, external_id, name, url, fetched_at, heat, velocity, acceleration, source_rank, description, metadata_json, raw_json)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "source-run",
                snapshot_id,
                "github_trending",
                "daily:owner/repo",
                "owner/repo",
                "https://github.com/owner/repo",
                "2026-05-31T10:00:00Z",
                None,
                None,
                None,
                1,
                "Repo description",
                json.dumps({"period": "daily", "period_stars": 321, "stars_total": 999}, ensure_ascii=False),
                json.dumps({"full_name": "owner/repo"}, ensure_ascii=False),
            ),
        )
        conn.commit()
        conn.close()
        return temp, db_path

    def test_build_dashboard_data_uses_latest_source_rows_and_settings(self) -> None:
        from pipeline.dashboard_data import build_dashboard_data

        temp, db_path = self.make_db()
        self.addCleanup(temp.cleanup)
        config = {
            "github_trending": {"periods": ["daily"], "languages": [""]},
            "github_search": {"queries": [{"label": "agent", "query": "agent stars:>20"}]},
            "hn": {"algolia_queries": [{"label": "agent", "query": "agent"}]},
            "npm": {"queries": [{"label": "mcp", "query": "mcp"}]},
            "apify": {"enabled": False, "x_keyword_queries": ["agent workflow"]},
        }

        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "present"}, clear=False):
            payload = build_dashboard_data(db_path=db_path, config=config)

        self.assertEqual(payload["run_id"], "source-run")
        self.assertEqual(payload["fetched_at"], "2026-05-31T10:00:00Z")
        self.assertEqual(payload["channel_counts"]["github_trending"], 1)
        self.assertEqual(payload["channels"][0]["id"], "github_trending")
        self.assertEqual(payload["items"][0]["name"], "owner/repo")
        self.assertEqual(payload["items"][0]["native_metric"]["value"], 321)
        self.assertIn("settings_source_health", payload["channel_counts"])
        self.assertTrue(payload["config_meta"]["api_status"]["github"]["configured"])
        self.assertNotIn("present", json.dumps(payload, ensure_ascii=False))

    def test_dashboard_data_hides_retired_ossinsight_snapshots(self) -> None:
        from pipeline.dashboard_data import build_dashboard_data

        temp, db_path = self.make_db()
        self.addCleanup(temp.cleanup)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "insert into snapshots(run_id, source, fetched_at, status, item_count, error) values (?, ?, ?, ?, ?, ?)",
            (
                "source-run",
                "ossinsight_trending_optional",
                "2026-05-31T10:00:00Z",
                "error",
                0,
                "retired source should not appear",
            ),
        )
        conn.commit()
        conn.close()

        payload = build_dashboard_data(
            db_path=db_path,
            config={
                "github_search": {"queries": []},
                "hn": {"algolia_queries": []},
                "npm": {"queries": []},
                "apify": {"enabled": False, "x_keyword_queries": []},
            },
        )

        serialized = json.dumps(payload, ensure_ascii=False).lower()
        self.assertNotIn("ossinsight", serialized)

    def test_server_exposes_dashboard_data_endpoint_payload(self) -> None:
        import pipeline.server as server

        temp, db_path = self.make_db()
        self.addCleanup(temp.cleanup)
        with mock.patch.object(server, "DB_PATH", db_path):
            with mock.patch.object(
                server,
                "read_json",
                return_value={
                    "github_trending": {"periods": ["daily"], "languages": [""]},
                    "github_search": {"queries": []},
                    "hn": {"algolia_queries": []},
                    "npm": {"queries": []},
                    "apify": {"enabled": False, "x_keyword_queries": []},
                },
            ):
                payload = server.query_dashboard_data_payload()

        self.assertEqual(payload["run_id"], "source-run")
        self.assertEqual(payload["items"][0]["channel"], "github_trending")
        self.assertEqual(payload["candidates"]["run_id"], "")

    def test_server_candidates_include_evidence_context(self) -> None:
        import pipeline.server as server
        from pipeline.decision.schema import (
            begin_decision_run,
            finish_decision_run,
            init_decision_db,
        )

        temp, db_path = self.make_db()
        self.addCleanup(temp.cleanup)
        conn = sqlite3.connect(db_path)
        init_decision_db(conn)
        begin_decision_run(
            conn,
            run_id="decision-run",
            source_snapshot_run_id="source-run",
            config_hash="config",
            rule_version="rules-v1",
        )
        conn.execute(
            """
            insert into entities(entity_id, canonical_entity, canonical_key, key_type, first_seen, aliases_json, source_item_ids_json)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "entity:repo",
                "owner/repo",
                "github:owner/repo",
                "github",
                "2026-05-31T10:00:00Z",
                "[]",
                "[1]",
            ),
        )
        conn.execute(
            """
            insert into potential_candidates(entity_id, run_id, level, fired_families_json, first_trigger_at)
            values (?, ?, ?, ?, ?)
            """,
            (
                "entity:repo",
                "decision-run",
                "potential",
                json.dumps(["github"]),
                "2026-05-31T10:00:00Z",
            ),
        )
        conn.execute(
            """
            insert into evidence_rows(entity_id, canonical_entity, alias, source, event_at, metric_name, metric_value, family, rule_id, rule_version, signal_label, historical_safety, note, raw_url_or_ref, run_id)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "entity:repo",
                "owner/repo",
                "owner/repo",
                "github_trending",
                "2026-05-31T10:00:00Z",
                "stars_today",
                "321",
                "github",
                "github_daily",
                "rules-v1",
                "potential",
                "snapshot_only",
                "passed",
                "item:1",
                "decision-run",
            ),
        )
        finish_decision_run(conn, run_id="decision-run", status="ok")
        conn.close()

        with mock.patch.object(server, "DB_PATH", db_path):
            with mock.patch.object(
                server,
                "read_json",
                return_value={
                    "github_trending": {"periods": ["daily"], "languages": [""]},
                    "github_search": {"queries": []},
                    "hn": {"algolia_queries": []},
                    "npm": {"queries": []},
                    "apify": {"enabled": False, "x_keyword_queries": []},
                },
            ):
                payload = server.query_dashboard_data_payload()

        row = payload["candidates"]["candidates"][0]
        self.assertEqual(row["canonical_link"], "https://github.com/owner/repo")
        self.assertEqual(row["evidence_bullets"][0]["label"], "GH +321 stars / 24h")
        self.assertEqual(row["binding_confidence"], "verified")

    def test_server_run_command_uses_daily_pipeline_by_default(self) -> None:
        import pipeline.server as server

        with mock.patch.object(server, "PYTHON", "py"):
            with mock.patch.object(server, "ROOT", Path("/repo")):
                command = server.build_run_command({})

        self.assertEqual(command, ["py", "/repo/pipeline/run_daily.py"])

    def test_server_run_command_maps_only_sources_to_daily_pipeline(self) -> None:
        import pipeline.server as server

        with mock.patch.object(server, "PYTHON", "py"):
            with mock.patch.object(server, "ROOT", Path("/repo")):
                command = server.build_run_command({"only": ["hn_algolia", "hn_firebase"]})

        self.assertEqual(
            command,
            [
                "py",
                "/repo/pipeline/run_daily.py",
                "--only-source",
                "hn_algolia,hn_firebase",
            ],
        )

    def test_server_run_command_supports_hn_only_decision(self) -> None:
        import pipeline.server as server

        with mock.patch.object(server, "PYTHON", "py"):
            with mock.patch.object(server, "ROOT", Path("/repo")):
                command = server.build_run_command(
                    {
                        "skip_sources": True,
                        "no_backfill": True,
                        "classify_hn_limit": 400,
                        "classify_x_limit": 0,
                        "resolver_search_limit": 80,
                        "resolver_research_limit": 0,
                        "enrich_readme_limit": 0,
                    }
                )

        self.assertEqual(
            command,
            [
                "py",
                "/repo/pipeline/run_daily.py",
                "--skip-sources",
                "--no-backfill",
                "--classify-hn-limit",
                "400",
                "--classify-x-limit",
                "0",
                "--resolver-search-limit",
                "80",
                "--resolver-research-limit",
                "0",
                "--enrich-readme-limit",
                "0",
            ],
        )

    def test_server_run_command_maps_layer2_options_to_daily_pipeline(self) -> None:
        import pipeline.server as server

        with mock.patch.object(server, "PYTHON", "py"):
            with mock.patch.object(server, "ROOT", Path("/repo")):
                command = server.build_run_command(
                    {
                        "run_layer2": True,
                        "layer2_scout_limit": 10,
                        "layer2_scoring_limit": 20,
                        "layer2_deepdive_limit": 2,
                        "layer2_deepdive_min_l2_score": 71,
                        "layer2_enable_kimi_web_search": True,
                        "layer2_max_web_search_calls": 3,
                        "decision_io_concurrency": 7,
                        "decision_io_rate_limit_per_second": 1.5,
                        "layer2_scoring_concurrency": 6,
                        "layer2_brief_concurrency": 4,
                        "layer2_max_parallel_tool_calls": 3,
                        "layer2_github_tool_concurrency": 5,
                        "layer2_homepage_tool_concurrency": 4,
                        "layer2_web_search_tool_concurrency": 2,
                        "layer2_github_tool_rate_limit_per_second": 1.5,
                        "layer2_homepage_tool_rate_limit_per_second": 1.25,
                        "layer2_web_search_tool_rate_limit_per_second": 0.75,
                    }
                )

        self.assertIn("--run-layer2", command)
        self.assertIn("--layer2-scout-limit", command)
        self.assertIn("10", command)
        self.assertIn("--layer2-scoring-limit", command)
        self.assertIn("20", command)
        self.assertIn("--layer2-deepdive-limit", command)
        self.assertIn("2", command)
        self.assertIn("--layer2-deepdive-min-l2-score", command)
        self.assertIn("71", command)
        self.assertIn("--layer2-enable-kimi-web-search", command)
        self.assertIn("--layer2-max-web-search-calls", command)
        self.assertIn("3", command)
        self.assertEqual(command[command.index("--decision-io-concurrency") + 1], "7")
        self.assertEqual(
            command[command.index("--decision-io-rate-limit-per-second") + 1],
            "1.5",
        )
        expected = {
            "--layer2-scoring-concurrency": "6",
            "--layer2-brief-concurrency": "4",
            "--layer2-max-parallel-tool-calls": "3",
            "--layer2-github-tool-concurrency": "5",
            "--layer2-homepage-tool-concurrency": "4",
            "--layer2-web-search-tool-concurrency": "2",
            "--layer2-github-tool-rate-limit-per-second": "1.5",
            "--layer2-homepage-tool-rate-limit-per-second": "1.25",
            "--layer2-web-search-tool-rate-limit-per-second": "0.75",
        }
        for flag, value in expected.items():
            self.assertEqual(command[command.index(flag) + 1], value)


if __name__ == "__main__":
    unittest.main()
