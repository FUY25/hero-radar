from __future__ import annotations

import json
import sqlite3
import unittest

from pipeline.decision.llm_provider import FakeLLMProvider
from pipeline.decision.schema import init_decision_db


class XClassifierTest(unittest.TestCase):
    def make_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)
        conn.executescript(
            """
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
        rows = [
            (
                "t1",
                "credible1",
                "New repo https://github.com/owner/repo is useful",
                "https://x.com/credible1/status/t1",
                "2026-05-31T01:00:00Z",
                "2026-05-31T02:00:00Z",
                "{}",
            ),
            (
                "t2",
                "credible2",
                "Trying owner/repo for agents",
                "https://x.com/credible2/status/t2",
                "2026-05-31T03:00:00Z",
                "2026-05-31T04:00:00Z",
                "{}",
            ),
            (
                "noise",
                "credible3",
                "OpenAI Claude and MCP discourse today",
                "https://x.com/credible3/status/noise",
                "2026-05-31T03:30:00Z",
                "2026-05-31T04:00:00Z",
                "{}",
            ),
        ]
        conn.executemany(
            """
            insert into x_tweets_store(
                tweet_id, author_username, text, url, created_at, imported_at, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        return conn

    def test_stage1_extracts_mentions_and_stores_entity_mentions(self) -> None:
        from pipeline.decision.entity_resolution import entity_id_for_key
        from pipeline.decision.x_classifier import run_x_stage1

        conn = self.make_conn()
        provider = FakeLLMProvider(
            [
                {
                    "triage": [
                        {
                            "tweet_id": "t1",
                            "about_concrete_project": True,
                            "closer_look": True,
                            "product_names": ["owner/repo"],
                            "product_links": ["https://github.com/owner/repo"],
                            "project_refs": [
                                {
                                    "entity_key": "github:owner/repo",
                                    "entity_name": "owner/repo",
                                    "entity_confidence": "linked",
                                    "confidence": 0.9,
                                }
                            ],
                            "expression_strength": "recommendation",
                            "evidence_quote": "New repo",
                            "reason": "Links a concrete repo.",
                        },
                        {
                            "tweet_id": "t2",
                            "about_concrete_project": True,
                            "closer_look": True,
                            "product_names": ["owner/repo"],
                            "product_links": [],
                            "project_refs": [
                                {
                                    "entity_key": "github:owner/repo",
                                    "entity_name": "owner/repo",
                                    "entity_confidence": "exact_handle",
                                    "confidence": 0.8,
                                }
                            ],
                            "expression_strength": "adoption_or_usage",
                            "evidence_quote": "Trying owner/repo",
                            "reason": "Mentions trying the same repo.",
                        },
                        {
                            "tweet_id": "noise",
                            "about_concrete_project": False,
                            "closer_look": False,
                            "product_names": [],
                            "product_links": [],
                            "project_refs": [],
                            "expression_strength": "neutral",
                            "evidence_quote": "OpenAI Claude MCP",
                            "reason": "Generic known terms only.",
                        },
                    ]
                }
            ]
        )

        summary = run_x_stage1(
            conn,
            run_id="decision_run",
            provider=provider,
            credible_handles={"credible1", "credible2"},
            now="2026-05-31T04:00:00Z",
            limit=10,
            batch_size=10,
        )

        self.assertEqual(summary["mentions"], 2)
        mention = conn.execute(
            """
            select entity_id, window, distinct_authors, credible_authors,
                   mention_count, source_refs_json
            from entity_mentions
            where window = '24h'
            """
        ).fetchone()
        self.assertEqual(mention[0], entity_id_for_key("github:owner/repo"))
        self.assertEqual(mention[1:5], ("24h", 2, 2, 2))
        self.assertEqual(json.loads(mention[5]), ["tweet:t1", "tweet:t2"])

    def test_github_key_from_text_normalizes_like_stage_a(self) -> None:
        from pipeline.decision.x_classifier import github_key_from_text

        self.assertEqual(
            github_key_from_text("Try https://github.com/Owner/Repo.git today"),
            "github:owner/repo",
        )

    def test_candidate_tweets_support_current_store_schema_without_imported_at(self) -> None:
        from pipeline.decision.x_classifier import candidate_tweets

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.executescript(
            """
            create table x_tweets_store (
                tweet_id text primary key,
                author_username text not null,
                author_name text,
                text text not null,
                url text,
                created_at text not null,
                metrics_json text not null,
                mentioned_projects_json text not null,
                hashtags_json text not null,
                mentions_json text not null,
                raw_json text not null,
                first_seen_at text not null,
                last_seen_at text not null,
                last_import_run_id text
            );
            """
        )
        conn.execute(
            """
            insert into x_tweets_store(
                tweet_id, author_username, author_name, text, url, created_at,
                metrics_json, mentioned_projects_json, hashtags_json, mentions_json,
                raw_json, first_seen_at, last_seen_at, last_import_run_id
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "t1",
                "credible1",
                "Credible One",
                "Try https://github.com/Owner/Repo",
                "https://x.com/credible1/status/t1",
                "2026-05-31T01:00:00Z",
                "{}",
                "[]",
                "[]",
                "[]",
                "{}",
                "2026-05-31T02:00:00Z",
                "2026-05-31T03:00:00Z",
                "run",
            ),
        )

        tweets = candidate_tweets(conn, now="2026-05-31T04:00:00Z", limit=5)

        self.assertEqual(tweets[0]["imported_at"], "2026-05-31T02:00:00Z")
        self.assertEqual(tweets[0]["deterministic_hints"][0]["entity_key"], "github:owner/repo")

    def test_candidate_tweets_extract_stage0_hints_from_current_store_columns(self) -> None:
        from pipeline.decision.x_classifier import candidate_tweets

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.executescript(
            """
            create table x_tweets_store (
                tweet_id text primary key,
                author_username text not null,
                author_name text,
                text text not null,
                url text,
                created_at text not null,
                metrics_json text not null,
                mentioned_projects_json text not null,
                hashtags_json text not null,
                mentions_json text not null,
                raw_json text not null,
                first_seen_at text not null,
                last_seen_at text not null,
                last_import_run_id text
            );
            """
        )
        conn.execute(
            """
            insert into x_tweets_store(
                tweet_id, author_username, author_name, text, url, created_at,
                metrics_json, mentioned_projects_json, hashtags_json, mentions_json,
                raw_json, first_seen_at, last_seen_at, last_import_run_id
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "t-stage0",
                "credible1",
                "Credible One",
                "Clawdbot from @owner is useful #mcp https://github.com/Owner/Repo",
                "https://x.com/credible1/status/t-stage0",
                "2026-05-31T01:00:00Z",
                "{}",
                json.dumps(["Clawdbot"]),
                json.dumps(["mcp"]),
                json.dumps(["owner"]),
                json.dumps({"expanded_urls": ["https://github.com/Owner/Repo"]}),
                "2026-05-31T02:00:00Z",
                "2026-05-31T03:00:00Z",
                "run",
            ),
        )

        tweet = candidate_tweets(conn, now="2026-05-31T04:00:00Z", limit=1)[0]

        self.assertEqual(tweet["stage0_hints"]["mentioned_projects"], ["Clawdbot"])
        self.assertEqual(tweet["stage0_hints"]["hashtags"], ["mcp"])
        self.assertEqual(tweet["stage0_hints"]["mentions"], ["owner"])
        self.assertIn(
            {"entity_key": "github:owner/repo", "entity_confidence": "linked"},
            tweet["deterministic_hints"],
        )

    def test_stage1_allows_product_signal_without_entity_key(self) -> None:
        from pipeline.decision.x_classifier import validate_x_stage1_output

        output = validate_x_stage1_output(
            {
                "triage": [
                    {
                        "tweet_id": "t1",
                        "about_concrete_project": True,
                        "closer_look": True,
                        "product_names": ["Clawdbot"],
                        "product_links": [],
                        "project_refs": [],
                        "expression_strength": "recommendation",
                        "evidence_quote": "Clawdbot looks useful",
                        "reason": "Concrete product name with recommendation.",
                    }
                ]
            }
        )

        self.assertEqual(output["triage"][0]["product_names"], ["Clawdbot"])

    def test_stage1_requires_product_signal_fields(self) -> None:
        from pipeline.decision.x_classifier import validate_x_stage1_output

        with self.assertRaises(ValueError):
            validate_x_stage1_output(
                {
                    "triage": [
                        {
                            "tweet_id": "t1",
                            "about_concrete_project": True,
                            "closer_look": True,
                            "project_refs": [],
                            "expression_strength": "recommendation",
                            "evidence_quote": "Clawdbot looks useful",
                            "reason": "Missing product_names/product_links.",
                        }
                    ]
                }
            )

    def test_stage1_creates_name_mentions_from_product_names_without_refs(self) -> None:
        from pipeline.decision.entity_resolution import entity_id_for_key
        from pipeline.decision.x_classifier import run_x_stage1

        conn = self.make_conn()
        provider = FakeLLMProvider(
            [
                {
                    "triage": [
                        {
                            "tweet_id": "t1",
                            "about_concrete_project": True,
                            "closer_look": True,
                            "product_names": ["Clawdbot"],
                            "product_links": [],
                            "project_refs": [],
                            "expression_strength": "recommendation",
                            "evidence_quote": "Clawdbot is useful",
                            "reason": "Fuzzy but concrete product mention.",
                        }
                    ]
                }
            ]
        )

        summary = run_x_stage1(
            conn,
            run_id="decision_run",
            provider=provider,
            credible_handles={"credible1"},
            now="2026-05-31T04:00:00Z",
            limit=10,
            batch_size=10,
        )

        self.assertEqual(summary["mentions"], 1)
        entity_id = conn.execute(
            "select entity_id from entity_mentions where window = '24h'"
        ).fetchone()[0]
        self.assertEqual(entity_id, entity_id_for_key("name:clawdbot"))

    def test_stage1_normalizes_name_ref_entity_keys_before_grouping(self) -> None:
        from pipeline.decision.entity_resolution import entity_id_for_key
        from pipeline.decision.x_classifier import run_x_stage1

        conn = self.make_conn()
        provider = FakeLLMProvider(
            [
                {
                    "triage": [
                        {
                            "tweet_id": "t1",
                            "about_concrete_project": True,
                            "closer_look": True,
                            "product_names": ["Claude Opus 4.8"],
                            "product_links": [],
                            "project_refs": [
                                {
                                    "entity_key": "name:Claude Opus 4.8",
                                    "entity_name": "Claude Opus 4.8",
                                    "entity_confidence": "fuzzy_name",
                                    "confidence": 0.8,
                                }
                            ],
                            "expression_strength": "recommendation",
                            "evidence_quote": "Claude Opus 4.8",
                            "reason": "Concrete model version.",
                        },
                        {
                            "tweet_id": "t2",
                            "about_concrete_project": True,
                            "closer_look": True,
                            "product_names": ["claude-opus-4-8"],
                            "product_links": [],
                            "project_refs": [
                                {
                                    "entity_key": "name:claude-opus-4-8",
                                    "entity_name": "claude-opus-4-8",
                                    "entity_confidence": "fuzzy_name",
                                    "confidence": 0.8,
                                }
                            ],
                            "expression_strength": "adoption_or_usage",
                            "evidence_quote": "claude-opus-4-8",
                            "reason": "Same concrete model version with normalized spelling.",
                        },
                    ]
                }
            ]
        )

        run_x_stage1(
            conn,
            run_id="decision_run",
            provider=provider,
            credible_handles={"credible1", "credible2"},
            now="2026-05-31T04:00:00Z",
            limit=10,
            batch_size=10,
        )

        rows = conn.execute(
            "select distinct entity_id from entity_mentions where window = '24h'"
        ).fetchall()
        self.assertEqual(rows, [(entity_id_for_key("name:claude-opus-4-8"),)])

    def test_stage1_merges_name_only_mentions_to_linked_project_in_same_batch(self) -> None:
        from pipeline.decision.entity_resolution import entity_id_for_key
        from pipeline.decision.x_classifier import run_x_stage1

        conn = self.make_conn()
        provider = FakeLLMProvider(
            [
                {
                    "triage": [
                        {
                            "tweet_id": "t1",
                            "about_concrete_project": True,
                            "closer_look": True,
                            "product_names": ["Clawdbot"],
                            "product_links": ["https://github.com/owner/repo"],
                            "project_refs": [
                                {
                                    "entity_key": "github:owner/repo",
                                    "entity_name": "Clawdbot",
                                    "entity_confidence": "linked",
                                    "confidence": 0.95,
                                }
                            ],
                            "expression_strength": "recommendation",
                            "evidence_quote": "New repo",
                            "reason": "Links Clawdbot to the repo.",
                        },
                        {
                            "tweet_id": "t2",
                            "about_concrete_project": True,
                            "closer_look": True,
                            "product_names": ["Clawdbot"],
                            "product_links": [],
                            "project_refs": [],
                            "expression_strength": "adoption_or_usage",
                            "evidence_quote": "Trying Clawdbot",
                            "reason": "Name-only mention of the same product.",
                        },
                    ]
                }
            ]
        )

        run_x_stage1(
            conn,
            run_id="decision_run",
            provider=provider,
            credible_handles={"credible1", "credible2"},
            now="2026-05-31T04:00:00Z",
            limit=10,
            batch_size=10,
        )

        rows = conn.execute(
            """
            select entity_id, mention_count, source_refs_json
            from entity_mentions
            where window = '24h'
            """
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], entity_id_for_key("github:owner/repo"))
        self.assertEqual(rows[0][1], 2)
        self.assertEqual(json.loads(rows[0][2]), ["tweet:t1", "tweet:t2"])

    def test_stage1_reuses_per_tweet_cache_when_batch_shape_changes(self) -> None:
        from pipeline.decision.x_classifier import run_x_stage1

        conn = self.make_conn()
        provider = FakeLLMProvider(
            [
                {
                    "triage": [
                        {
                            "tweet_id": "noise",
                            "about_concrete_project": False,
                            "closer_look": False,
                            "product_names": [],
                            "product_links": [],
                            "project_refs": [],
                            "expression_strength": "neutral",
                            "evidence_quote": "OpenAI Claude MCP",
                            "reason": "Generic terms.",
                        },
                        {
                            "tweet_id": "t2",
                            "about_concrete_project": True,
                            "closer_look": True,
                            "product_names": ["owner/repo"],
                            "product_links": [],
                            "project_refs": [
                                {
                                    "entity_key": "github:owner/repo",
                                    "entity_name": "owner/repo",
                                    "entity_confidence": "exact_handle",
                                    "confidence": 0.8,
                                }
                            ],
                            "expression_strength": "adoption_or_usage",
                            "evidence_quote": "Trying owner/repo",
                            "reason": "Concrete repo mention.",
                        },
                        {
                            "tweet_id": "t1",
                            "about_concrete_project": True,
                            "closer_look": True,
                            "product_names": ["owner/repo"],
                            "product_links": ["https://github.com/owner/repo"],
                            "project_refs": [
                                {
                                    "entity_key": "github:owner/repo",
                                    "entity_name": "owner/repo",
                                    "entity_confidence": "linked",
                                    "confidence": 0.9,
                                }
                            ],
                            "expression_strength": "recommendation",
                            "evidence_quote": "New repo",
                            "reason": "Links concrete repo.",
                        },
                    ]
                }
            ]
        )

        first = run_x_stage1(
            conn,
            run_id="decision_run_1",
            provider=provider,
            credible_handles={"credible1", "credible2"},
            now="2026-05-31T04:00:00Z",
            limit=10,
            batch_size=10,
        )
        second = run_x_stage1(
            conn,
            run_id="decision_run_2",
            provider=provider,
            credible_handles={"credible1", "credible2"},
            now="2026-05-31T04:00:00Z",
            limit=10,
            batch_size=1,
        )

        self.assertEqual(first["stage1_cache_hits"], 0)
        self.assertEqual(first["stage1_cache_misses"], 3)
        self.assertEqual(second["stage1_cache_hits"], 3)
        self.assertEqual(second["stage1_cache_misses"], 0)
        self.assertEqual(len(provider.calls), 1)

    def test_stage2_gate_includes_single_credible_stage1_watch_candidate(self) -> None:
        from pipeline.decision.x_classifier import candidate_entity_mentions

        conn = self.make_conn()
        conn.execute(
            """
            insert into entity_mentions(
                entity_id, run_id, window, distinct_authors, credible_authors,
                mention_count, mention_acceleration, source_refs_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "entity:watch",
                "decision_run",
                "24h",
                1,
                1,
                1,
                1.0,
                json.dumps(["tweet:t1"]),
            ),
        )

        mentions = candidate_entity_mentions(conn, run_id="decision_run", limit=5)

        self.assertEqual([mention["entity_id"] for mention in mentions], ["entity:watch"])

    def test_stage2_gate_includes_single_credible_7d_project_candidate(self) -> None:
        from pipeline.decision.x_classifier import candidate_entity_mentions

        conn = self.make_conn()
        conn.execute(
            """
            insert into entity_mentions(
                entity_id, run_id, window, distinct_authors, credible_authors,
                mention_count, mention_acceleration, source_refs_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "entity:seven-day",
                "decision_run",
                "7d",
                1,
                1,
                1,
                1.0,
                json.dumps(["tweet:t1"]),
            ),
        )

        mentions = candidate_entity_mentions(conn, run_id="decision_run", limit=5)

        self.assertEqual(
            [(mention["entity_id"], mention["window"]) for mention in mentions],
            [("entity:seven-day", "7d")],
        )

    def test_stage2_gate_prefers_7d_aggregate_over_duplicate_24h_window(self) -> None:
        from pipeline.decision.x_classifier import candidate_entity_mentions

        conn = self.make_conn()
        for window, refs in (
            ("24h", ["tweet:t1"]),
            ("7d", ["tweet:t1", "tweet:t2"]),
        ):
            conn.execute(
                """
                insert into entity_mentions(
                    entity_id, run_id, window, distinct_authors, credible_authors,
                    mention_count, mention_acceleration, source_refs_json
                )
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "entity:duplicate",
                    "decision_run",
                    window,
                    len(refs),
                    len(refs),
                    len(refs),
                    float(len(refs)),
                    json.dumps(refs),
                ),
            )

        mentions = candidate_entity_mentions(conn, run_id="decision_run", limit=5)

        self.assertEqual(len(mentions), 1)
        self.assertEqual(mentions[0]["window"], "7d")
        self.assertEqual(mentions[0]["source_refs"], ["tweet:t1", "tweet:t2"])

    def test_stage2_writes_x_social_evidence(self) -> None:
        from pipeline.decision.x_classifier import run_x_stage2

        conn = self.make_conn()
        conn.execute(
            """
            insert into entity_mentions(
                entity_id, run_id, window, distinct_authors, credible_authors,
                mention_count, mention_acceleration, source_refs_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "entity:x",
                "decision_run",
                "24h",
                2,
                2,
                2,
                2.0,
                json.dumps(["tweet:t1", "tweet:t2"]),
            ),
        )
        provider = FakeLLMProvider(
            [
                {
                    "entity_key": "github:owner/repo",
                    "x_tier": "potential",
                    "entity_confidence": "linked",
                    "x_expression_strength": "recommendation",
                    "cited_tweet_ids": ["t1", "t2"],
                    "rationale": "Two credible authors cited the same repo.",
                    "cross_source_notes": [],
                }
            ]
        )

        summary = run_x_stage2(
            conn,
            run_id="decision_run",
            provider=provider,
            now="2026-05-31T04:00:00Z",
            limit=5,
        )

        self.assertEqual(summary["tiered"], 1)
        rows = conn.execute(
            """
            select source, family, metric_name, metric_value, raw_url_or_ref
            from evidence_rows
            order by metric_name
            """
        ).fetchall()
        metric_names = {row[2] for row in rows}
        self.assertIn("mention_count", metric_names)
        self.assertIn("x_tier", metric_names)
        tier = [row for row in rows if row[2] == "x_tier"][0]
        self.assertEqual(tier[:4], ("x_tweets", "x_social", "x_tier", "potential"))
        self.assertEqual(tier[4], "tweet:t1,tweet:t2")

    def test_stage2_downgrades_fuzzy_or_uncited_potential_to_watch_or_none(self) -> None:
        from pipeline.decision.x_classifier import accepted_x_tier, validate_x_stage2_output

        fuzzy = validate_x_stage2_output(
            {
                "entity_key": "name:clawdbot",
                "x_tier": "potential",
                "entity_confidence": "fuzzy_name",
                "x_expression_strength": "recommendation",
                "cited_tweet_ids": ["t1"],
                "rationale": "Name-only mention.",
                "cross_source_notes": [],
            }
        )
        uncited = validate_x_stage2_output(
            {
                "entity_key": "github:owner/repo",
                "x_tier": "potential",
                "entity_confidence": "linked",
                "x_expression_strength": "recommendation",
                "cited_tweet_ids": [],
                "rationale": "No citations.",
                "cross_source_notes": [],
            }
        )

        self.assertEqual(accepted_x_tier(fuzzy), "watch")
        self.assertEqual(accepted_x_tier(uncited), "none")

    def test_stage2_clamps_high_without_larger_credible_burst(self) -> None:
        from pipeline.decision.x_classifier import accepted_x_tier, validate_x_stage2_output

        output = validate_x_stage2_output(
            {
                "entity_key": "github:owner/repo",
                "x_tier": "high",
                "entity_confidence": "linked",
                "x_expression_strength": "strong_recommendation",
                "cited_tweet_ids": ["t1", "t2"],
                "rationale": "Two credible tweets, but not enough for high.",
                "cross_source_notes": [],
            }
        )

        self.assertEqual(
            accepted_x_tier(
                output,
                aggregate={"credible_authors": 2, "distinct_authors": 2},
            ),
            "potential",
        )

    def test_stage2_generic_known_terms_without_binding_are_none(self) -> None:
        from pipeline.decision.x_classifier import run_x_stage2

        conn = self.make_conn()
        conn.execute(
            """
            insert into entity_mentions(
                entity_id, run_id, window, distinct_authors, credible_authors,
                mention_count, mention_acceleration, source_refs_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "entity:generic",
                "decision_run",
                "24h",
                3,
                3,
                3,
                1.0,
                json.dumps(["tweet:noise"]),
            ),
        )
        provider = FakeLLMProvider(
            [
                {
                    "entity_key": "term:MCP",
                    "x_tier": "none",
                    "entity_confidence": "fuzzy_name",
                    "x_expression_strength": "neutral",
                    "cited_tweet_ids": [],
                    "rationale": "Generic known terms without a concrete binding.",
                    "cross_source_notes": [],
                }
            ]
        )

        summary = run_x_stage2(
            conn,
            run_id="decision_run",
            provider=provider,
            now="2026-05-31T04:00:00Z",
            limit=5,
        )

        self.assertEqual(summary["tiered"], 1)
        tier = conn.execute(
            "select metric_value, signal_label from evidence_rows where metric_name = 'x_tier'"
        ).fetchone()
        self.assertEqual(tier, ("none", "noise"))

    def test_validate_x_outputs_reject_bad_schema(self) -> None:
        from pipeline.decision.x_classifier import (
            validate_x_stage1_output,
            validate_x_stage2_output,
        )

        with self.assertRaises(ValueError):
            validate_x_stage1_output({"triage": [{"tweet_id": "t1"}]})
        with self.assertRaises(ValueError):
            validate_x_stage1_output(
                {
                    "triage": [
                        {
                            "tweet_id": "t1",
                            "about_concrete_project": True,
                            "closer_look": True,
                            "product_names": ["owner/repo"],
                            "product_links": ["https://github.com/owner/repo"],
                            "project_refs": [
                                {
                                    "entity_key": "github:owner/repo",
                                    "entity_name": "owner/repo",
                                    "entity_confidence": "linked",
                                    "confidence": 1.5,
                                }
                            ],
                            "expression_strength": "recommendation",
                            "evidence_quote": "repo",
                            "reason": "bad confidence",
                        }
                    ]
                }
            )
        with self.assertRaises(ValueError):
            validate_x_stage2_output(
                {
                    "entity_key": "github:owner/repo",
                    "x_tier": "potential",
                    "entity_confidence": "linked",
                    "x_expression_strength": "recommendation",
                    "cited_tweet_ids": [],
                    "rationale": "No citations.",
                    "cross_source_notes": [],
                },
                strict_for_promotion=True,
            )
        with self.assertRaises(ValueError):
            validate_x_stage2_output(
                {
                    "entity_key": "github:owner/repo",
                    "x_tier": "medium",
                    "entity_confidence": "linked",
                    "x_expression_strength": "recommendation",
                    "cited_tweet_ids": ["t1"],
                    "rationale": "Bad tier.",
                    "cross_source_notes": [],
                }
            )

    def test_prompt_payload_builders_are_reviewable_without_api_call(self) -> None:
        from pipeline.decision.x_classifier import (
            build_x_stage1_prompt_payload,
            build_x_stage2_prompt_payload,
            candidate_tweets,
        )

        conn = self.make_conn()
        tweets = candidate_tweets(conn, now="2026-05-31T04:00:00Z", limit=2)
        stage1 = build_x_stage1_prompt_payload(tweets)
        stage2 = build_x_stage2_prompt_payload(
            {
                "entity_id": "entity:x",
                "window": "24h",
                "distinct_authors": 2,
                "credible_authors": 2,
                "mention_count": 2,
                "mention_acceleration": 2.0,
                "source_refs": ["tweet:t1", "tweet:t2"],
            },
            tweets,
        )

        self.assertEqual(stage1["prompt_version"], "x-stage1-v2")
        self.assertIn("tweets", stage1)
        self.assertEqual(stage2["prompt_version"], "x-stage2-v2")
        self.assertEqual(stage2["aggregate"]["credible_authors"], 2)
        self.assertIn("High tier requires", stage2["instructions"])
        self.assertIn("three credible", stage2["instructions"])


if __name__ == "__main__":
    unittest.main()
