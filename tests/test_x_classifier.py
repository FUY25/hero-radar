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

        self.assertEqual(stage1["prompt_version"], "x-stage1-v1")
        self.assertIn("tweets", stage1)
        self.assertEqual(stage2["prompt_version"], "x-stage2-v1")
        self.assertEqual(stage2["aggregate"]["credible_authors"], 2)


if __name__ == "__main__":
    unittest.main()
