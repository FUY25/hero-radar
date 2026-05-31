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


if __name__ == "__main__":
    unittest.main()
