import sqlite3
import unittest

from pipeline.decision.backfill import run_backfill_jobs
from pipeline.decision.schema import init_decision_db


class FakeGitHubClient:
    def repo_metadata(self, full_name):
        return {"full_name": full_name, "stargazers_count": 1500, "forks_count": 120}

    def stargazers_since(self, full_name, since_iso):
        return [
            {"user": "a", "starred_at": "2026-05-30T12:00:00Z"},
            {"user": "b", "starred_at": "2026-05-30T13:00:00Z"},
        ]


class BackfillCacheTest(unittest.TestCase):
    def test_backfill_writes_api_cache_and_evidence(self):
        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
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
                "2026-05-31T00:00:00Z",
                "[]",
                "[]",
            ),
        )
        conn.execute(
            """
            insert into backfill_jobs(entity_id, run_id, source, reason, status, requested_at)
            values (?, ?, ?, ?, ?, ?)
            """,
            (
                "entity:repo",
                "run-1",
                "github_stargazers",
                "potential_candidate",
                "pending",
                "2026-05-31T00:00:00Z",
            ),
        )
        conn.commit()

        summary = run_backfill_jobs(
            conn,
            run_id="run-1",
            github_client=FakeGitHubClient(),
            now="2026-05-31T00:00:00Z",
        )

        self.assertEqual(summary["completed"], 1)
        cache_count = conn.execute("select count(*) from api_cache").fetchone()[0]
        evidence_count = conn.execute(
            "select count(*) from evidence_rows where source = 'github_backfill'"
        ).fetchone()[0]
        self.assertEqual(cache_count, 1)
        self.assertGreaterEqual(evidence_count, 1)


if __name__ == "__main__":
    unittest.main()
