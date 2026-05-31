from __future__ import annotations

import json
import sqlite3
import threading
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

    def insert_hn_item(
        self,
        conn: sqlite3.Connection,
        *,
        source: str,
        external_id: str,
        title: str,
        url: str,
        score: int,
        comments: int = 0,
    ) -> int:
        snapshot = conn.execute(
            "select id from snapshots where source = ? order by id desc limit 1",
            (source,),
        ).fetchone()
        if snapshot is None:
            conn.execute(
                """
                insert into snapshots(run_id, source, fetched_at, status, item_count, error)
                values (?, ?, ?, ?, ?, ?)
                """,
                ("run", source, "2026-05-31T00:00:00Z", "ok", 1, None),
            )
            snapshot_id = conn.execute("select last_insert_rowid()").fetchone()[0]
        else:
            snapshot_id = snapshot[0]
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
                source,
                external_id,
                title,
                url,
                "2026-05-31T00:00:00Z",
                None,
                None,
                None,
                1,
                "HN row",
                json.dumps(
                    {
                        "score": score,
                        "points": score,
                        "comments": comments,
                        "story_id": external_id,
                    },
                    ensure_ascii=False,
                ),
                "{}",
            ),
        )
        item_id = conn.execute("select last_insert_rowid()").fetchone()[0]
        conn.commit()
        return int(item_id)

    def test_candidate_hn_units_dedupe_by_useful_external_url(self) -> None:
        from pipeline.decision.hn_classifier import candidate_hn_units

        conn = self.make_conn(title="Seed")
        conn.execute("delete from items")
        url = "https://github.com/owner/repo"
        first = self.insert_hn_item(
            conn,
            source="hn_firebase",
            external_id="a",
            title="Show HN: Repo",
            url=url,
            score=120,
        )
        second = self.insert_hn_item(
            conn,
            source="hn_algolia",
            external_id="b",
            title="Show HN: Repo duplicate",
            url=f"{url}?ref=hn",
            score=80,
        )

        units = candidate_hn_units(conn, limit=10)

        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]["unit_key"], "url:https://github.com/owner/repo")
        self.assertEqual(units[0]["item_ids"], [first, second])
        self.assertEqual(units[0]["best_score"], 120)

    def test_candidate_hn_units_use_title_for_hn_self_posts(self) -> None:
        from pipeline.decision.hn_classifier import candidate_hn_units

        conn = self.make_conn(title="Seed")
        conn.execute("delete from items")
        first = self.insert_hn_item(
            conn,
            source="hn_firebase",
            external_id="a",
            title="Ask HN: What do you use for agents?",
            url="https://news.ycombinator.com/item?id=1",
            score=10,
        )
        second = self.insert_hn_item(
            conn,
            source="hn_algolia",
            external_id="b",
            title="Ask HN: What do you use for agents?",
            url="https://news.ycombinator.com/item?id=2",
            score=20,
        )

        units = candidate_hn_units(conn, limit=10)

        self.assertEqual(len(units), 1)
        self.assertTrue(units[0]["unit_key"].startswith("title:ask-hn-what-do-you-use"))
        self.assertEqual(units[0]["item_ids"], [first, second])

    def test_candidate_hn_units_rank_candidate_impact_before_heat(self) -> None:
        from pipeline.decision.entity_resolution import entity_id_for_key
        from pipeline.decision.hn_classifier import candidate_hn_units

        conn = self.make_conn(title="Seed")
        conn.execute("delete from items")
        low = self.insert_hn_item(
            conn,
            source="hn_firebase",
            external_id="candidate",
            title="Show HN: Candidate",
            url="https://candidate.dev",
            score=40,
        )
        self.insert_hn_item(
            conn,
            source="hn_firebase",
            external_id="hot",
            title="Hot news",
            url="https://example.com/news",
            score=300,
        )
        entity_id = entity_id_for_key("domain:candidate.dev")
        conn.execute(
            """
            insert into entities(
                entity_id, canonical_entity, canonical_key, key_type, first_seen,
                aliases_json, source_item_ids_json
            )
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity_id,
                "Candidate",
                "domain:candidate.dev",
                "domain",
                "2026-05-31T00:00:00Z",
                "[]",
                json.dumps([low]),
            ),
        )
        conn.execute(
            """
            insert into potential_candidates(
                entity_id, run_id, level, fired_families_json, first_trigger_at
            )
            values (?, ?, ?, ?, ?)
            """,
            (entity_id, "decision_run", "potential", json.dumps(["hn"]), "2026-05-31T00:00:00Z"),
        )
        conn.commit()

        units = candidate_hn_units(conn, limit=10)

        self.assertEqual(units[0]["url"], "https://candidate.dev")

    def test_candidate_hn_units_uses_explicit_current_run_impact_before_discovery(self) -> None:
        from pipeline.decision.hn_classifier import candidate_hn_units

        conn = self.make_conn(title="Seed")
        conn.execute("delete from items")
        candidate = self.insert_hn_item(
            conn,
            source="hn_firebase",
            external_id="candidate",
            title="Low score candidate",
            url="https://candidate.dev",
            score=20,
        )
        self.insert_hn_item(
            conn,
            source="hn_firebase",
            external_id="hot",
            title="Very hot discovery",
            url="https://hot.example",
            score=500,
        )

        units = candidate_hn_units(
            conn,
            limit=1,
            potential_item_ids={candidate},
            edge_item_ids=set(),
        )

        self.assertEqual([unit["item_ids"] for unit in units], [[candidate]])

    def test_run_hn_classifier_classifies_deduped_unit_once_and_maps_all_items(self) -> None:
        from pipeline.decision.hn_classifier import run_hn_classifier

        conn = self.make_conn(title="Seed")
        conn.execute("delete from items")
        url = "https://example.com/news"
        self.insert_hn_item(
            conn,
            source="hn_firebase",
            external_id="a",
            title="AI news",
            url=url,
            score=120,
        )
        self.insert_hn_item(
            conn,
            source="hn_algolia",
            external_id="b",
            title="AI news duplicate",
            url=url,
            score=80,
        )
        provider = FakeLLMProvider(
            [
                {
                    "item_id": 1,
                    "projectness": "news_article",
                    "confidence": 0.91,
                    "canonical_name": "",
                    "deterministic_links": [],
                    "proposed_links": [],
                    "summary": "News, not project evidence.",
                }
            ]
        )

        summary = run_hn_classifier(
            conn,
            run_id="decision_run",
            provider=provider,
            limit=10,
            now="2026-05-31T00:00:00Z",
        )

        self.assertEqual(summary["classified"], 1)
        self.assertEqual(summary["classified_items"], 2)
        self.assertEqual(len(provider.calls), 1)
        refs = conn.execute(
            "select raw_url_or_ref from evidence_rows order by raw_url_or_ref"
        ).fetchall()
        self.assertEqual(len(refs), 2)

    def test_run_hn_classifier_spends_limit_on_explicit_candidate_impact_first(self) -> None:
        from pipeline.decision.hn_classifier import run_hn_classifier

        conn = self.make_conn(title="Seed")
        conn.execute("delete from items")
        candidate = self.insert_hn_item(
            conn,
            source="hn_firebase",
            external_id="candidate",
            title="Low score candidate",
            url="https://candidate.dev",
            score=20,
        )
        hot = self.insert_hn_item(
            conn,
            source="hn_firebase",
            external_id="hot",
            title="Very hot discovery",
            url="https://hot.example",
            score=500,
        )
        provider = FakeLLMProvider(
            [
                {
                    "item_id": candidate,
                    "projectness": "news_article",
                    "confidence": 0.9,
                    "canonical_name": "",
                    "deterministic_links": [],
                    "proposed_links": [],
                    "summary": "Candidate-impact unit is classified first.",
                }
            ]
        )

        summary = run_hn_classifier(
            conn,
            run_id="decision_run",
            provider=provider,
            limit=1,
            now="2026-05-31T00:00:00Z",
            potential_item_ids={candidate},
            edge_item_ids=set(),
        )

        self.assertEqual(summary["classified"], 1)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(
            conn.execute("select raw_url_or_ref from evidence_rows").fetchone()[0],
            f"item:{candidate}",
        )
        self.assertNotEqual(candidate, hot)

    def test_hn_unit_cache_reuses_result_when_duplicate_rows_arrive_later(self) -> None:
        from pipeline.decision.hn_classifier import run_hn_classifier

        conn = self.make_conn(title="Seed")
        conn.execute("delete from items")
        url = "https://example.com/news"
        self.insert_hn_item(
            conn,
            source="hn_firebase",
            external_id="a",
            title="AI news",
            url=url,
            score=80,
        )
        provider = FakeLLMProvider(
            [
                {
                    "item_id": 1,
                    "projectness": "news_article",
                    "confidence": 0.91,
                    "canonical_name": "",
                    "deterministic_links": [],
                    "proposed_links": [],
                    "summary": "News, not project evidence.",
                }
            ]
        )

        first = run_hn_classifier(
            conn,
            run_id="decision_run_1",
            provider=provider,
            limit=10,
            now="2026-05-31T00:00:00Z",
        )
        self.insert_hn_item(
            conn,
            source="hn_algolia",
            external_id="b",
            title="AI news duplicate with more points",
            url=url,
            score=300,
        )
        second = run_hn_classifier(
            conn,
            run_id="decision_run_2",
            provider=provider,
            limit=10,
            now="2026-06-01T00:00:00Z",
        )

        self.assertEqual(first["classified"], 1)
        self.assertEqual(second["classified"], 1)
        self.assertEqual(second["classified_items"], 2)
        self.assertEqual(len(provider.calls), 1)

    def test_hn_classifier_parallelizes_uncached_units_without_parallel_db_writes(self) -> None:
        from pipeline.decision.hn_classifier import run_hn_classifier

        class BarrierHNProvider:
            provider_name = "fake"
            model = "barrier-hn"

            def __init__(self) -> None:
                self.barrier = threading.Barrier(2, timeout=3)
                self.lock = threading.Lock()
                self.active = 0
                self.max_active = 0
                self.calls: list[int] = []

            def complete_json(
                self,
                *,
                task: str,
                prompt_version: str,
                input_payload: dict,
                system_prompt: str = "",
            ) -> dict:
                item_id = int(input_payload["item"]["item_id"])
                with self.lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                    self.calls.append(item_id)
                try:
                    self.barrier.wait()
                    return {
                        "item_id": item_id,
                        "projectness": "news_article",
                        "confidence": 0.9,
                        "canonical_name": "",
                        "deterministic_links": [],
                        "proposed_links": [],
                        "summary": "News, not project evidence.",
                    }
                finally:
                    with self.lock:
                        self.active -= 1

        conn = self.make_conn(title="Seed")
        conn.execute("delete from items")
        self.insert_hn_item(
            conn,
            source="hn_firebase",
            external_id="a",
            title="AI news A",
            url="https://example.com/a",
            score=120,
        )
        self.insert_hn_item(
            conn,
            source="hn_firebase",
            external_id="b",
            title="AI news B",
            url="https://example.com/b",
            score=110,
        )
        provider = BarrierHNProvider()

        summary = run_hn_classifier(
            conn,
            run_id="decision_run",
            provider=provider,
            limit=2,
            now="2026-05-31T00:00:00Z",
            llm_concurrency=2,
        )

        self.assertEqual(summary["classified"], 2)
        self.assertEqual(summary["cache_hits"], 0)
        self.assertEqual(summary["cache_misses"], 2)
        self.assertGreaterEqual(provider.max_active, 2)
        self.assertEqual(conn.execute("select count(*) from evidence_rows").fetchone()[0], 2)
        self.assertEqual(conn.execute("select count(*) from llm_cache").fetchone()[0], 2)

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

    def test_validate_hn_output_derives_missing_github_key_from_url(self) -> None:
        from pipeline.decision.hn_classifier import validate_hn_output

        output = validate_hn_output(
            {
                "item_id": 1,
                "projectness": "project",
                "confidence": 0.9,
                "canonical_name": "Demo",
                "deterministic_links": [
                    {
                        "type": "github",
                        "key": None,
                        "url": "https://github.com/Owner/Repo",
                    }
                ],
                "proposed_links": [],
                "summary": "Project with a GitHub URL.",
            }
        )

        self.assertEqual(output["deterministic_links"][0]["key"], "github:owner/repo")

    def test_run_hn_classifier_discards_malformed_provider_links_without_aborting(self) -> None:
        from pipeline.decision.hn_classifier import run_hn_classifier

        conn = self.make_conn()
        provider = FakeLLMProvider(
            [
                {
                    "item_id": 1,
                    "projectness": "project",
                    "confidence": 0.9,
                    "canonical_name": "Superpowers",
                    "deterministic_links": [
                        {
                            "type": "domain",
                            "key": "obra:superpowers",
                            "url": "",
                        }
                    ],
                    "proposed_links": [
                        {
                            "type": "domain",
                            "key": "also-bad",
                            "url": "",
                            "confidence": 0.4,
                        }
                    ],
                    "summary": "A project mention with malformed link fields.",
                }
            ]
        )

        summary = run_hn_classifier(
            conn,
            run_id="decision_run",
            provider=provider,
            limit=1,
            now="2026-05-31T00:00:00Z",
        )

        self.assertEqual(summary["classified"], 1)
        self.assertEqual(summary["aliases"], 0)
        self.assertEqual(summary["proposals"], 0)
        evidence = conn.execute(
            "select metric_value, canonical_entity from evidence_rows"
        ).fetchone()
        self.assertEqual(evidence, ("project", "Superpowers"))

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
