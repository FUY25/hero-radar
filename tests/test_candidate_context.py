import json
import sqlite3
import unittest

from pipeline.decision.schema import init_decision_db


class CandidateContextTest(unittest.TestCase):
    def make_conn(self):
        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        conn.executescript(
            """
            create table items (
                id integer primary key autoincrement,
                run_id text not null,
                snapshot_id integer,
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
            """
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
                "2026-05-31T00:00:00Z",
                json.dumps(["owner/repo"]),
                json.dumps([1]),
            ),
        )
        conn.execute(
            """
            insert into items(id, run_id, snapshot_id, source, external_id, name, url, fetched_at, description, metadata_json, raw_json)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "source-run",
                1,
                "github_trending",
                "daily:owner/repo",
                "owner/repo",
                "https://github.com/owner/repo",
                "2026-05-31T00:00:00Z",
                "Repo description from source row.",
                json.dumps({"period_stars": 1300}),
                "{}",
            ),
        )
        rows = [
            ("github_trending", "stars_today", "1300", "github", "github_daily", "potential", "item:1"),
            ("hn_top", "hn_score", "142", "hn", "hn_frontpage", "potential", "item:2"),
            ("x_tweets", "x_tier", "potential", "x_social", "x_stage2", "potential", "tweet:t1"),
            ("resolver", "canonical_link", "github:owner/repo", "resolver", "resolver_link", "context", "alias:1"),
        ]
        for source, metric_name, metric_value, family, rule_id, signal_label, raw_ref in rows:
            conn.execute(
                """
                insert into evidence_rows(entity_id, canonical_entity, alias, source, event_at, metric_name, metric_value, family, rule_id, rule_version, signal_label, historical_safety, note, raw_url_or_ref, run_id)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "entity:repo",
                    "owner/repo",
                    "owner/repo",
                    source,
                    "2026-05-31T00:00:00Z",
                    metric_name,
                    metric_value,
                    family,
                    rule_id,
                    "rules-v1",
                    signal_label,
                    "snapshot_only",
                    "note",
                    raw_ref,
                    "run-1",
                ),
            )
        conn.commit()
        return conn

    def test_context_bundle_has_bullets_link_preview_and_binding(self):
        from pipeline.decision.candidate_context import context_bundle_for_entity

        conn = self.make_conn()
        bundle = context_bundle_for_entity(conn, entity_id="entity:repo", run_id="run-1")

        self.assertEqual(bundle["canonical_link"], "https://github.com/owner/repo")
        self.assertEqual(bundle["binding_confidence"], "verified")
        self.assertEqual(bundle["context_preview"], "Repo description from source row.")
        self.assertEqual(bundle["evidence_count"], 4)
        self.assertEqual(
            [bullet["label"] for bullet in bundle["evidence_bullets"][:3]],
            [
                "GH +1.3k stars / 24h",
                "HN front page, 142 pts",
                "X potential",
            ],
        )
        self.assertEqual(bundle["evidence_bullets"][2]["origin_type"], "source_classifier")

    def test_context_dedupes_repeated_evidence_bullets_and_keeps_refs(self):
        from pipeline.decision.candidate_context import context_bundle_for_entity

        conn = self.make_conn()
        duplicate_rows = [
            ("hn_llm_classifier", "hn_projectness", "project", "hn", "hn_llm_projectness", "potential", "item:10"),
            ("hn_llm_classifier", "hn_projectness", "project", "hn", "hn_llm_projectness", "potential", "item:11"),
        ]
        for source, metric_name, metric_value, family, rule_id, signal_label, raw_ref in duplicate_rows:
            conn.execute(
                """
                insert into evidence_rows(entity_id, canonical_entity, alias, source, event_at, metric_name, metric_value, family, rule_id, rule_version, signal_label, historical_safety, note, raw_url_or_ref, run_id)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "entity:repo",
                    "owner/repo",
                    "owner/repo",
                    source,
                    "2026-05-31T00:00:00Z",
                    metric_name,
                    metric_value,
                    family,
                    rule_id,
                    "rules-v1",
                    signal_label,
                    "snapshot_only",
                    "note",
                    raw_ref,
                    "run-1",
                ),
            )
        conn.commit()

        bundle = context_bundle_for_entity(conn, entity_id="entity:repo", run_id="run-1")

        classifier_bullets = [
            bullet
            for bullet in bundle["evidence_bullets"]
            if bullet["label"] == "HN classifier: project"
        ]
        self.assertEqual(len(classifier_bullets), 1)
        self.assertEqual(classifier_bullets[0]["source_refs"], ["item:10", "item:11"])
        self.assertEqual(bundle["evidence_count"], len(bundle["evidence_bullets"]))

    def test_context_prefers_resolver_alias_when_canonical_key_is_name(self):
        from pipeline.decision.candidate_context import context_bundle_for_entity

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        conn.execute(
            """
            insert into entities(entity_id, canonical_entity, canonical_key, key_type, first_seen, aliases_json, source_item_ids_json)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "entity:claw",
                "Clawdbot",
                "name:clawdbot",
                "name",
                "2026-05-31T00:00:00Z",
                "[]",
                "[]",
            ),
        )
        conn.execute(
            """
            insert into alias_links(entity_id, source, external_id, alias, confidence, origin, approved, created_at)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "entity:claw",
                "resolver",
                "name:clawdbot",
                "github:owner/clawdbot",
                "deterministic",
                "resolver",
                1,
                "2026-05-31T00:00:00Z",
            ),
        )

        bundle = context_bundle_for_entity(conn, entity_id="entity:claw", run_id="run-1")

        self.assertEqual(bundle["canonical_link"], "https://github.com/owner/clawdbot")
        self.assertEqual(bundle["binding_confidence"], "resolved")

    def test_context_prefers_resolver_alias_over_stale_stage_a_alias_for_name(self):
        from pipeline.decision.candidate_context import context_bundle_for_entity

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        conn.execute(
            """
            insert into entities(entity_id, canonical_entity, canonical_key, key_type, first_seen, aliases_json, source_item_ids_json)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "entity:firecrawl",
                "firecrawl",
                "name:firecrawl",
                "name",
                "2026-05-31T00:00:00Z",
                "[]",
                "[]",
            ),
        )
        rows = [
            ("github:nickscamara/open-deep-research", "stage_a", "github:nickscamara/open-deep-research"),
            ("github:firecrawl/firecrawl", "resolver", "name:firecrawl"),
        ]
        for alias, origin, external_id in rows:
            conn.execute(
                """
                insert into alias_links(entity_id, source, external_id, alias, confidence, origin, approved, created_at)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "entity:firecrawl",
                    "decision" if origin == "stage_a" else "resolver",
                    external_id,
                    alias,
                    "deterministic",
                    origin,
                    1,
                    "2026-05-31T00:00:00Z",
                ),
            )

        bundle = context_bundle_for_entity(conn, entity_id="entity:firecrawl", run_id="run-1")

        self.assertEqual(bundle["canonical_link"], "https://github.com/firecrawl/firecrawl")
        self.assertEqual(bundle["binding_confidence"], "resolved")


if __name__ == "__main__":
    unittest.main()
