from __future__ import annotations

import json
import sqlite3
import unittest

from pipeline.decision.schema import init_decision_db


class FakeSearchClient:
    def __init__(self, results: list[dict[str, object]]) -> None:
        self.results = results
        self.calls: list[dict[str, object]] = []

    def search(self, query: str, *, limit: int) -> list[dict[str, object]]:
        self.calls.append({"query": query, "limit": limit})
        return self.results[:limit]


class ResolverTest(unittest.TestCase):
    def make_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)
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
        return conn

    def insert_item(
        self,
        conn: sqlite3.Connection,
        *,
        name: str,
        url: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        conn.execute(
            "insert into snapshots(run_id, source, fetched_at, status, item_count, error) values (?, ?, ?, ?, ?, ?)",
            ("source-run", "hn_firebase", "2026-05-31T00:00:00Z", "ok", 1, None),
        )
        snapshot_id = conn.execute("select id from snapshots").fetchone()[0]
        conn.execute(
            """
            insert into items(
                run_id, snapshot_id, source, external_id, name, url, fetched_at,
                description, metadata_json, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "source-run",
                snapshot_id,
                "hn_firebase",
                name.lower(),
                name,
                url,
                "2026-05-31T00:00:00Z",
                "",
                json.dumps(metadata or {}, ensure_ascii=False),
                "{}",
            ),
        )
        conn.commit()

    def test_resolver_uses_internal_rows_before_search_client(self) -> None:
        from pipeline.decision.resolver import resolve_candidate_links

        conn = self.make_conn()
        self.insert_item(
            conn,
            name="Clawdbot",
            url="https://github.com/owner/clawdbot",
        )
        search_client = FakeSearchClient(
            [
                {
                    "type": "github",
                    "key": "github:wrong/repo",
                    "url": "https://github.com/wrong/repo",
                    "confidence": 0.9,
                }
            ]
        )

        result = resolve_candidate_links(
            conn,
            "name:clawdbot",
            search_client=search_client,
            max_searches=1,
        )

        self.assertEqual(result["resolved_links"][0]["key"], "github:owner/clawdbot")
        self.assertEqual(search_client.calls, [])

    def test_resolver_is_bounded_and_cached_for_unresolved_name(self) -> None:
        from pipeline.decision.resolver import resolve_candidate_links

        conn = self.make_conn()
        search_client = FakeSearchClient(
            [
                {
                    "type": "domain",
                    "key": "domain:clawdbot.dev",
                    "url": "https://clawdbot.dev",
                    "confidence": 0.82,
                }
            ]
        )

        first = resolve_candidate_links(
            conn,
            "name:clawdbot",
            search_client=search_client,
            max_searches=1,
        )
        second = resolve_candidate_links(
            conn,
            "name:clawdbot",
            search_client=search_client,
            max_searches=1,
        )

        self.assertEqual(first, second)
        self.assertEqual(len(search_client.calls), 1)
        self.assertEqual(search_client.calls[0]["limit"], 1)

    def test_resolver_ignores_shortener_domains_from_internal_rows(self) -> None:
        from pipeline.decision.resolver import resolve_candidate_links

        conn = self.make_conn()
        self.insert_item(
            conn,
            name="Codex",
            url="https://t.co/short",
        )

        result = resolve_candidate_links(
            conn,
            "name:codex",
            search_client=None,
            max_searches=0,
        )

        self.assertEqual(result["resolved_links"], [])

    def test_enrich_classifier_candidates_writes_alias_after_accepted_x_tier(self) -> None:
        from pipeline.decision.resolver import enrich_classifier_candidates

        conn = self.make_conn()
        conn.execute(
            """
            insert into evidence_rows(
                entity_id, canonical_entity, alias, source, event_at,
                relative_to_reference, metric_name, metric_value, family, rule_id,
                rule_version, signal_label, historical_safety, note, raw_url_or_ref,
                run_id
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "entity:clawdbot",
                "name:clawdbot",
                "name:clawdbot",
                "x_tweets",
                "2026-05-31T00:00:00Z",
                None,
                "x_tier",
                "watch",
                "x_social",
                "x_social_x_tier",
                "x-stage2-v2",
                "watch",
                "llm_source_classifier",
                "accepted watch candidate",
                "tweet:t1",
                "run-1",
            ),
        )
        conn.commit()
        search_client = FakeSearchClient(
            [
                {
                    "type": "github",
                    "key": "github:owner/clawdbot",
                    "url": "https://github.com/owner/clawdbot",
                    "confidence": 0.9,
                }
            ]
        )

        summary = enrich_classifier_candidates(
            conn,
            run_id="run-1",
            search_client=search_client,
            max_searches_per_candidate=1,
            now="2026-05-31T00:00:00Z",
        )

        self.assertEqual(summary["enriched"], 1)
        alias = conn.execute(
            "select entity_id, alias, confidence, origin, approved from alias_links"
        ).fetchone()
        self.assertEqual(
            alias,
            ("entity:clawdbot", "github:owner/clawdbot", "deterministic", "resolver", 1),
        )


if __name__ == "__main__":
    unittest.main()
