import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from pipeline.decision.run_decision import run_decision
from pipeline.decision.schema import init_decision_db


class FakeGitHubClient:
    def repo_metadata(self, full_name):
        return {"full_name": full_name, "stargazers_count": 1500, "forks_count": 120}

    def stargazers_since(self, full_name, since_iso):
        return [
            {"user": "a", "starred_at": "2026-05-30T12:00:00Z"},
            {"user": "b", "starred_at": "2026-05-30T13:00:00Z"},
        ]


def seed_source_tables(conn: sqlite3.Connection) -> None:
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
            url text,
            fetched_at text not null,
            heat real,
            velocity real,
            acceleration real,
            source_rank integer,
            description text,
            metadata_json text not null,
            raw_json text not null
        );
        """
    )
    conn.execute(
        "insert into snapshots(run_id, source, fetched_at, status, item_count, error) values (?, ?, ?, ?, ?, ?)",
        ("source-run-1", "github_trending", "2026-05-31T00:00:00Z", "ok", 1, None),
    )
    snapshot_id = conn.execute("select id from snapshots").fetchone()[0]
    conn.execute(
        """
        insert into items(run_id, snapshot_id, source, external_id, name, url, fetched_at, metadata_json, raw_json)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "source-run-1",
            snapshot_id,
            "github_trending",
            "owner/repo",
            "owner/repo",
            "https://github.com/owner/repo",
            "2026-05-31T00:00:00Z",
            json.dumps(
                {
                    "period": "daily",
                    "window": "24h",
                    "period_stars": 1200,
                    "stars_total": 3000,
                }
            ),
            "{}",
        ),
    )
    conn.commit()


class DecisionRunnerTest(unittest.TestCase):
    def test_runner_writes_entities_candidates_and_export(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            export_path = Path(tmpdir) / "candidates.json"
            conn = sqlite3.connect(db_path)
            seed_source_tables(conn)
            init_decision_db(conn)
            conn.close()

            summary = run_decision(
                db_path=db_path,
                run_id="decision-run-1",
                export_json_path=export_path,
                now="2026-05-31T00:00:00Z",
            )

            self.assertEqual(summary["potential_candidates"], 1)
            self.assertTrue(export_path.exists())

            payload = json.loads(export_path.read_text())
            self.assertEqual(payload["run_id"], "decision-run-1")
            self.assertEqual(payload["candidates"][0]["level"], "potential")

    def test_runner_does_not_reset_completed_backfill_jobs_to_pending(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            export_path = Path(tmpdir) / "candidates.json"
            conn = sqlite3.connect(db_path)
            seed_source_tables(conn)
            init_decision_db(conn)
            conn.close()

            run_decision(
                db_path=db_path,
                run_id="decision-run-1",
                export_json_path=export_path,
                now="2026-05-31T00:00:00Z",
                github_client=FakeGitHubClient(),
            )

            conn = sqlite3.connect(db_path)
            statuses = conn.execute(
                "select status from backfill_jobs where run_id = ?",
                ("decision-run-1",),
            ).fetchall()
            conn.close()
            self.assertEqual(statuses, [("completed",)])


if __name__ == "__main__":
    unittest.main()
