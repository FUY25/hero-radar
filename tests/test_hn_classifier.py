from __future__ import annotations

import json
import sqlite3
import unittest

from pipeline.decision.llm_provider import FakeLLMProvider
from pipeline.decision.schema import init_decision_db


class HnClassifierTest(unittest.TestCase):
    def make_conn(self, *, title: str = "Show HN: Clawdbot") -> sqlite3.Connection:
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
            insert into snapshots(run_id, source, fetched_at, status, item_count, error)
            values (?, ?, ?, ?, ?, ?)
            """,
            ("run", "hn_firebase", "2026-05-31T00:00:00Z", "ok", 1, None),
        )
        snapshot_id = conn.execute("select id from snapshots").fetchone()[0]
        conn.execute(
            """
            insert into items(
                run_id, snapshot_id, source, external_id, name, url, fetched_at,
                heat, velocity, acceleration, source_rank, description,
                metadata_json, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run",
                snapshot_id,
                "hn_firebase",
                "123",
                title,
                "https://news.ycombinator.com/item?id=123",
                "2026-05-31T00:00:00Z",
                None,
                None,
                None,
                1,
                "Launch post",
                json.dumps(
                    {
                        "score": 160,
                        "hn_url": "https://news.ycombinator.com/item?id=123",
                    },
                    ensure_ascii=False,
                ),
                "{}",
            ),
        )
        conn.commit()
        return conn

    def test_hn_classifier_writes_projectness_evidence_and_alias_link(self) -> None:
        from pipeline.decision.entity_resolution import entity_id_for_key
        from pipeline.decision.hn_classifier import run_hn_classifier

        conn = self.make_conn()
        provider = FakeLLMProvider(
            [
                {
                    "item_id": 1,
                    "projectness": "project",
                    "confidence": 0.93,
                    "canonical_name": "Clawdbot",
                    "deterministic_links": [
                        {
                            "type": "github",
                            "key": "github:owner/clawdbot",
                            "url": "https://github.com/owner/clawdbot",
                        }
                    ],
                    "proposed_links": [],
                    "summary": "Show HN launch for Clawdbot.",
                }
            ]
        )

        summary = run_hn_classifier(
            conn,
            run_id="decision_run",
            provider=provider,
            limit=5,
            now="2026-05-31T00:00:00Z",
        )

        self.assertEqual(summary["classified"], 1)
        evidence = conn.execute(
            "select entity_id, source, family, metric_name, metric_value, signal_label, note from evidence_rows"
        ).fetchone()
        self.assertEqual(evidence[0], entity_id_for_key("github:owner/clawdbot"))
        self.assertEqual(
            evidence[1:6],
            ("hn_llm_classifier", "hn", "hn_projectness", "project", "watch"),
        )
        self.assertIn("Clawdbot", evidence[6])
        alias = conn.execute(
            "select alias, confidence, origin, approved from alias_links"
        ).fetchone()
        self.assertEqual(
            alias,
            ("github:owner/clawdbot", "deterministic", "hn_llm_classifier", 1),
        )
        cached = conn.execute(
            "select provider, model, prompt_version, task, status from llm_cache"
        ).fetchone()
        self.assertEqual(
            cached, ("fake", "fake-json", "hn-projectness-v1", "hn_classifier", "ok")
        )

    def test_hn_classifier_marks_news_article_as_noise_for_rules(self) -> None:
        from pipeline.decision.hn_classifier import run_hn_classifier

        conn = self.make_conn(title="AI lab announces a new policy")
        provider = FakeLLMProvider(
            [
                {
                    "item_id": 1,
                    "projectness": "news_article",
                    "confidence": 0.9,
                    "canonical_name": "",
                    "deterministic_links": [],
                    "proposed_links": [],
                    "summary": "Article about a broader market event, not a project.",
                }
            ]
        )

        run_hn_classifier(
            conn,
            run_id="decision_run",
            provider=provider,
            limit=5,
            now="2026-05-31T00:00:00Z",
        )

        row = conn.execute(
            "select metric_name, metric_value, signal_label from evidence_rows"
        ).fetchone()
        self.assertEqual(row, ("hn_projectness", "news_article", "noise"))
        self.assertEqual(conn.execute("select count(*) from alias_links").fetchone()[0], 0)

    def test_hn_classifier_marks_topic_discussion_as_noise_without_promotion_evidence(
        self,
    ) -> None:
        from pipeline.decision.hn_classifier import run_hn_classifier

        conn = self.make_conn(title="Ask HN: What do you use for MCP?")
        provider = FakeLLMProvider(
            [
                {
                    "item_id": 1,
                    "projectness": "topic_discussion",
                    "confidence": 0.88,
                    "canonical_name": "",
                    "deterministic_links": [],
                    "proposed_links": [
                        {
                            "type": "domain",
                            "key": "domain:mcp.example",
                            "url": "https://mcp.example",
                            "confidence": 0.4,
                        }
                    ],
                    "summary": "General discussion of a topic, not a launch.",
                }
            ]
        )

        run_hn_classifier(
            conn,
            run_id="decision_run",
            provider=provider,
            limit=5,
            now="2026-05-31T00:00:00Z",
        )

        row = conn.execute(
            "select metric_value, signal_label from evidence_rows"
        ).fetchone()
        self.assertEqual(row, ("topic_discussion", "noise"))
        proposal = conn.execute(
            "select orphan, confidence, status from entity_merge_proposals"
        ).fetchone()
        self.assertEqual(proposal, ("domain:mcp.example", 0.4, "open"))

    def test_validate_hn_output_rejects_bad_schema(self) -> None:
        from pipeline.decision.hn_classifier import validate_hn_output

        base = {
            "item_id": 1,
            "projectness": "project",
            "confidence": 0.8,
            "canonical_name": "Clawdbot",
            "deterministic_links": [],
            "proposed_links": [],
            "summary": "A project.",
        }
        for bad in (
            {**base, "projectness": "launch"},
            {**base, "confidence": 1.2},
            {k: v for k, v in base.items() if k != "summary"},
            {
                **base,
                "deterministic_links": [
                    {"type": "github", "key": "owner/repo", "url": "https://github.com/owner/repo"}
                ],
            },
            {
                **base,
                "proposed_links": [
                    {
                        "type": "domain",
                        "key": "domain:example.com",
                        "url": "https://example.com",
                        "confidence": 1.5,
                    }
                ],
            },
        ):
            with self.assertRaises(ValueError):
                validate_hn_output(bad)

    def test_build_hn_prompt_payload_is_reviewable_without_api_call(self) -> None:
        from pipeline.decision.hn_classifier import build_hn_prompt_payload, candidate_hn_rows

        conn = self.make_conn()
        row = candidate_hn_rows(conn, limit=1)[0]
        payload = build_hn_prompt_payload(row)

        self.assertEqual(payload["item"]["item_id"], 1)
        self.assertEqual(payload["allowed_projectness"][0], "project")
        self.assertIn("output_schema", payload)
        self.assertIn("title", payload["item"])

    def test_candidate_hn_rows_orders_by_score_or_points(self) -> None:
        from pipeline.decision.hn_classifier import candidate_hn_rows

        conn = self.make_conn()
        snapshot_id = conn.execute("select id from snapshots").fetchone()[0]
        conn.execute(
            """
            insert into items(
                run_id, snapshot_id, source, external_id, name, url, fetched_at,
                heat, velocity, acceleration, source_rank, description,
                metadata_json, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run",
                snapshot_id,
                "hn_algolia",
                "456",
                "Launch: Higher points project",
                "https://news.ycombinator.com/item?id=456",
                "2026-05-31T00:00:00Z",
                None,
                None,
                None,
                2,
                "",
                json.dumps({"points": 220}, ensure_ascii=False),
                "{}",
            ),
        )
        conn.commit()

        rows = candidate_hn_rows(conn, limit=2)

        self.assertEqual(rows[0]["external_id"], "456")


if __name__ == "__main__":
    unittest.main()
