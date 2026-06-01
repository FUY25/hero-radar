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

    def test_context_preview_cleans_readme_markup(self):
        from pipeline.decision.candidate_context import context_bundle_for_entity

        conn = self.make_conn()
        conn.execute(
            """
            insert into api_cache(cache_key, source, external_id, window, input_hash, response_json, status, fetched_at)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "readme:owner/repo",
                "github_readme",
                "owner/repo",
                "candidate_context",
                "hash",
                json.dumps(
                    {
                        "preview": (
                            '<p align="center"><a href="https://opencode.ai"><picture>'
                            '<source srcset="packages/logo.svg"><img alt="OpenCode logo">'
                            "</picture></a></p>\n"
                            "![npm](https://img.shields.io/npm/v/opencode-ai.svg) "
                            "# OpenCode\n"
                            "An agentic terminal coding tool with [docs](https://opencode.ai/docs).\n"
                            "```bash\nnpm install opencode-ai\n```"
                        )
                    }
                ),
                "ok",
                "2026-05-31T00:00:00Z",
            ),
        )
        conn.commit()

        bundle = context_bundle_for_entity(conn, entity_id="entity:repo", run_id="run-1")

        self.assertEqual(
            bundle["context_preview"],
            "OpenCode An agentic terminal coding tool with docs.",
        )

    def test_context_preview_cleans_truncated_html_fragments(self):
        from pipeline.decision.candidate_context import context_bundle_for_entity

        conn = self.make_conn()
        conn.execute(
            "update items set description = ? where id = 1",
            ('<img src=" Banner title <p align="center">Readable project summary',),
        )
        conn.commit()

        bundle = context_bundle_for_entity(conn, entity_id="entity:repo", run_id="run-1")

        self.assertEqual(bundle["context_preview"], "Banner title Readable project summary")

    def test_context_preview_cleans_reference_style_markdown_badges(self):
        from pipeline.decision.candidate_context import context_bundle_for_entity

        conn = self.make_conn()
        description = (
            "# Claude Agent SDK [![npm]][npm]\n"
            "[npm]: https://img.shields.io/npm/v/@anthropic-ai/claude-agent-sdk.svg\n"
            "The Claude Agent SDK enables programmatic agents."
        )
        conn.execute(
            "update items set description = ? where id = 1",
            (description,),
        )
        conn.commit()

        bundle = context_bundle_for_entity(conn, entity_id="entity:repo", run_id="run-1")

        self.assertEqual(
            bundle["context_preview"],
            "Claude Agent SDK The Claude Agent SDK enables programmatic agents.",
        )

    def test_context_preview_cleans_trailing_truncated_badges(self):
        from pipeline.decision.candidate_context import context_bundle_for_entity

        conn = self.make_conn()
        conn.execute(
            "update items set description = ? where id = 1",
            ('Useful coding agent. [![Code style: Ruff](https:/ <img src="" ![Release',),
        )
        conn.commit()

        bundle = context_bundle_for_entity(conn, entity_id="entity:repo", run_id="run-1")

        self.assertEqual(bundle["context_preview"], "Useful coding agent.")

        conn.execute("update items set description = ? where id = 1", ("Personal AI Infrastructure ![Releas",))
        conn.commit()

        bundle = context_bundle_for_entity(conn, entity_id="entity:repo", run_id="run-1")

        self.assertEqual(bundle["context_preview"], "Personal AI Infrastructure")

    def test_npm_registry_evidence_is_backfill_not_llm_classifier(self):
        from pipeline.decision.candidate_context import context_bundle_for_entity

        conn = self.make_conn()
        conn.execute("delete from evidence_rows")
        conn.execute(
            """
            insert into evidence_rows(entity_id, canonical_entity, alias, source, event_at, metric_name, metric_value, family, rule_id, rule_version, signal_label, historical_safety, note, raw_url_or_ref, run_id)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "entity:repo",
                "owner/repo",
                "@scope/pkg",
                "npm_registry",
                "2026-05-31T00:00:00Z",
                "npm_repository_link",
                "github:owner/repo",
                "package_family",
                "npm_registry_npm_repository_link",
                "rules-v1",
                "backfill",
                "partial_as_of",
                "npm repository link backfill",
                "https://www.npmjs.com/package/@scope/pkg",
                "run-1",
            ),
        )
        conn.commit()

        bundle = context_bundle_for_entity(conn, entity_id="entity:repo", run_id="run-1")

        self.assertEqual(bundle["evidence_bullets"][0]["origin_type"], "backfill")
        self.assertEqual(bundle["evidence_bullets"][0]["provenance_badge"], "backfill")

    def test_context_bundle_expands_evidence_refs_to_internal_source_links(self):
        from pipeline.decision.candidate_context import context_bundle_for_entity

        conn = self.make_conn()
        conn.execute(
            """
            insert into items(id, run_id, snapshot_id, source, external_id, name, url, fetched_at, description, metadata_json, raw_json)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                2,
                "source-run",
                1,
                "hn_algolia",
                "story:123",
                "Show HN: owner/repo",
                "https://news.ycombinator.com/item?id=123",
                "2026-05-31T00:00:00Z",
                "HN story.",
                json.dumps({"points": 142, "window": "7d"}),
                "{}",
            ),
        )
        conn.execute(
            """
            insert into items(id, run_id, snapshot_id, source, external_id, name, url, fetched_at, description, metadata_json, raw_json)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                3,
                "source-run",
                1,
                "x_tweets",
                "tweet:t1",
                "X post about owner/repo",
                "https://x.com/alice/status/t1",
                "2026-05-31T00:00:00Z",
                "tweet.",
                json.dumps({"author": "alice", "window": "7d"}),
                "{}",
            ),
        )
        conn.commit()

        bundle = context_bundle_for_entity(conn, entity_id="entity:repo", run_id="run-1")

        links = bundle["source_links"]
        self.assertGreaterEqual(bundle["source_link_count"], 3)
        self.assertEqual(links[0]["ref"], "item:1")
        self.assertEqual(links[0]["item_id"], 1)
        self.assertEqual(links[0]["channel"], "github_trending")
        self.assertEqual(links[0]["channel_label"], "GitHub Trending")
        self.assertEqual(links[0]["external_url"], "https://github.com/owner/repo")
        self.assertIn(
            {
                "ref": "item:2",
                "item_id": 2,
                "source": "hn_algolia",
                "channel": "hn_search",
                "channel_label": "HN Search",
                "label": "HN Search",
                "name": "Show HN: owner/repo",
                "external_url": "https://news.ycombinator.com/item?id=123",
                "window": "7d",
            },
            links,
        )
        self.assertTrue(any(link["ref"] == "tweet:t1" and link["channel"] == "x_tweets" for link in links))

    def test_hn_firebase_source_links_use_native_list_labels(self):
        from pipeline.decision.candidate_context import context_bundle_for_entity

        conn = self.make_conn()
        conn.execute(
            """
            insert into items(id, run_id, snapshot_id, source, external_id, name, url, fetched_at, description, metadata_json, raw_json)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                4,
                "source-run",
                1,
                "hn_firebase",
                "beststories:48318174",
                "Claude Code – Everything you can configure that the docs don't tell you",
                "https://buildingbetter.tech/p/i-read-the-claude-code-source-code",
                "2026-05-31T00:00:00Z",
                "",
                json.dumps({"list": "beststories", "window": "current", "score": 324}),
                "{}",
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
                "hn_firebase",
                "2026-05-31T00:00:00Z",
                "hn_max_points_7d",
                "324",
                "hn",
                "hn_max_points_7d_potential",
                "rules-v1",
                "early_trigger",
                "as_of_safe",
                "note",
                "item:4",
                "run-1",
            ),
        )
        conn.commit()

        bundle = context_bundle_for_entity(conn, entity_id="entity:repo", run_id="run-1")

        best_link = next(link for link in bundle["source_links"] if link["ref"] == "item:4")
        self.assertEqual(best_link["channel"], "hn_top")
        self.assertEqual(best_link["channel_label"], "HN Best")
        self.assertEqual(best_link["label"], "HN Best")

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

    def test_context_labels_hn_max_points_evidence(self):
        from pipeline.decision.candidate_context import context_bundle_for_entity

        conn = self.make_conn()
        conn.execute("delete from evidence_rows")
        conn.execute(
            """
            insert into evidence_rows(entity_id, canonical_entity, alias, source, event_at, metric_name, metric_value, family, rule_id, rule_version, signal_label, historical_safety, note, raw_url_or_ref, run_id)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "entity:repo",
                "owner/repo",
                "owner/repo",
                "hn_algolia",
                "2026-05-31T00:00:00Z",
                "hn_max_points_7d",
                "143",
                "hn",
                "hn_max_points_7d_watch",
                "rules-v1",
                "watch",
                "as_of_safe",
                "max HN points in 7d",
                "item:1",
                "run-1",
            ),
        )
        conn.commit()

        bundle = context_bundle_for_entity(conn, entity_id="entity:repo", run_id="run-1")

        self.assertEqual(bundle["evidence_bullets"][0]["label"], "HN max 143 pts / 7d")

    def test_context_dedupes_same_label_even_when_signal_strength_differs(self):
        from pipeline.decision.candidate_context import context_bundle_for_entity

        conn = self.make_conn()
        rows = [
            ("x_tweets", "x_tier", "potential", "x_social", "x_social_x_tier", "potential", "tweet:t1"),
            ("x_tweets", "x_tier", "potential", "x_social", "x_social_tier_potential", "early_trigger", "tweet:t1"),
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

        bundle = context_bundle_for_entity(conn, entity_id="entity:repo", run_id="run-1")

        labels = [bullet["label"] for bullet in bundle["evidence_bullets"]]
        self.assertEqual(labels.count("X potential"), 1)

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
