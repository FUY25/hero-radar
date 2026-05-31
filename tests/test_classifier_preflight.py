import json
import sqlite3
import unittest

from pipeline.decision.schema import init_decision_db


class ClassifierPreflightTest(unittest.TestCase):
    def make_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)
        conn.executescript(
            """
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

            create table x_tweets_store (
                tweet_id text primary key,
                author_username text not null,
                text text not null,
                url text,
                created_at text not null,
                imported_at text not null,
                raw_json text not null
            );
            """
        )
        return conn

    def test_x_preflight_uses_store_created_at_not_items_fetched_at(self):
        from pipeline.decision.classifier_preflight import classifier_preflight_summary

        conn = self.make_conn()
        # These dashboard/source rows were fetched today, but the tweets are old.
        # They must not be counted as rolling-7d classifier inputs.
        for row_id in range(3):
            conn.execute(
                """
                insert into items(run_id, snapshot_id, source, external_id, name, url, fetched_at, metadata_json, raw_json)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "source-run",
                    1,
                    "x_tweets",
                    f"30d:old-{row_id}",
                    "Old tweet",
                    f"https://x.com/a/status/old-{row_id}",
                    "2026-05-31T00:00:00Z",
                    json.dumps({"tweet_id": f"old-{row_id}", "created_at": "2026-04-01T00:00:00Z"}),
                    "{}",
                ),
            )
        conn.executemany(
            """
            insert into x_tweets_store(tweet_id, author_username, text, url, created_at, imported_at, raw_json)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "current",
                    "credible",
                    "Trying https://github.com/owner/repo",
                    "https://x.com/credible/status/current",
                    "2026-05-30T12:00:00Z",
                    "2026-05-31T00:00:00Z",
                    "{}",
                ),
                (
                    "old",
                    "credible",
                    "Old tweet imported today",
                    "https://x.com/credible/status/old",
                    "2026-04-01T00:00:00Z",
                    "2026-05-31T00:00:00Z",
                    "{}",
                ),
            ],
        )
        conn.commit()

        summary = classifier_preflight_summary(
            conn,
            now="2026-05-31T12:00:00Z",
            x_limit=500,
            hn_limit=0,
        )

        self.assertEqual(summary["x_time_basis"], "x_tweets_store.created_at")
        self.assertEqual(summary["x_items_rows"], 3)
        self.assertEqual(summary["x_store_rows"], 2)
        self.assertEqual(summary["x_classifier_candidates_7d"], 1)
        self.assertEqual(summary["x_classifier_will_process"], 1)

    def test_hn_preflight_reports_bounded_unit_count(self):
        from pipeline.decision.classifier_preflight import classifier_preflight_summary

        conn = self.make_conn()
        conn.execute(
            """
            insert into items(run_id, snapshot_id, source, external_id, name, url, fetched_at, heat, metadata_json, raw_json)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "source-run",
                1,
                "hn_firebase",
                "1",
                "Show HN: Repo",
                "https://repo.dev",
                "2026-05-31T00:00:00Z",
                120,
                json.dumps({"score": 120}),
                "{}",
            ),
        )
        conn.commit()

        summary = classifier_preflight_summary(
            conn,
            now="2026-05-31T12:00:00Z",
            x_limit=0,
            hn_limit=10,
        )

        self.assertEqual(summary["hn_classifier_units"], 1)
        self.assertEqual(summary["hn_classifier_will_process"], 1)


if __name__ == "__main__":
    unittest.main()
