import sqlite3
import unittest
from unittest.mock import patch

from pipeline.decision.backfill import GitHubClient, run_backfill_jobs
from pipeline.decision.cache import api_cache_key, get_api_cache, put_api_cache, stable_hash
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
    def test_github_client_limits_every_http_request_including_each_page(self):
        class Limiter:
            def __init__(self):
                self.calls = 0

            def wait(self):
                self.calls += 1

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                import json

                return json.dumps(self.payload).encode("utf-8")

        limiter = Limiter()
        responses = [
            Response({"stargazers_count": 250}),
            Response([]),
            Response([]),
            Response([]),
        ]
        client = GitHubClient(request_limiter=limiter)

        with patch("pipeline.decision.backfill.urllib.request.urlopen", side_effect=responses):
            client.stargazers_since("owner/repo", "2026-05-24T00:00:00Z")

        self.assertEqual(limiter.calls, 4)

    def test_api_cache_can_join_a_caller_owned_transaction(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)
        input_hash = stable_hash({"request": "candidate"})
        cache_key = api_cache_key(
            source="resolver",
            external_id="name:example",
            window="classifier_enrichment",
            input_hash=input_hash,
        )

        put_api_cache(
            conn,
            cache_key=cache_key,
            source="resolver",
            external_id="name:example",
            window="classifier_enrichment",
            input_hash=input_hash,
            response={"resolved_links": []},
            commit=False,
        )
        conn.rollback()

        self.assertIsNone(get_api_cache(conn, cache_key))

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
