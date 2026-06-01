from __future__ import annotations

import sqlite3
import unittest

from pipeline.decision.layer2_models import CandidateGroup
from pipeline.decision.llm_provider import FakeLLMProvider
from pipeline.decision.schema import init_decision_db


class Layer2ScoutTest(unittest.TestCase):
    def group(self, group_id: str, name: str = "Edge Project") -> CandidateGroup:
        return CandidateGroup(
            group_id=group_id,
            canonical_entity_id=f"entity:{group_id}",
            canonical_name=name,
            canonical_key=f"name:{group_id}",
            canonical_link="",
            member_entity_ids=[f"entity:{group_id}"],
            level="edge_watch",
            source_families=["hn"],
            evidence_hash="hash",
            context={
                "members": [
                    {
                        "entity_id": f"entity:{group_id}",
                        "context_preview": f"{name} is a concrete product.",
                        "readme_excerpt_available": False,
                        "source_links": [],
                    }
                ],
                "evidence_rows": [{"note": f"Show HN: {name}"}],
            },
        )

    def test_scout_micro_batches_and_includes_only_strong_novelty_products(self):
        from pipeline.decision.layer2_scout import scout_edge_watch_groups

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        provider = FakeLLMProvider(
            [
                {
                    "decisions": [
                        {
                            "group_id": "group:clicky",
                            "is_concrete_product": True,
                            "object_type": "product",
                            "workflow_shift": "strong",
                            "technical_substance": "weak",
                            "product_market_fit": "medium",
                            "confidence": 0.8,
                            "reason": (
                                "Cursor-adjacent Mac assistant is a new "
                                "interaction model."
                            ),
                        },
                        {
                            "group_id": "group:medium",
                            "is_concrete_product": True,
                            "object_type": "repo",
                            "workflow_shift": "medium",
                            "technical_substance": "medium",
                            "product_market_fit": "medium",
                            "confidence": 0.8,
                            "reason": "Interesting but no strong novelty axis.",
                        },
                        {
                            "group_id": "group:news",
                            "is_concrete_product": False,
                            "object_type": "news",
                            "workflow_shift": "strong",
                            "technical_substance": "strong",
                            "product_market_fit": "strong",
                            "confidence": 0.8,
                            "reason": "News article, not a product.",
                        },
                    ]
                }
            ]
        )
        groups = [
            self.group("group:clicky", "Clicky"),
            self.group("group:medium", "Medium Project"),
            self.group("group:news", "AI Company News"),
        ]

        included = scout_edge_watch_groups(
            conn,
            feed_run_id="l2-run",
            groups=groups,
            provider=provider,
            batch_size=3,
        )

        self.assertEqual([row.group_id for row in included], ["group:clicky"])
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(provider.calls[0]["task"], "layer2_edge_scout")
        self.assertEqual(provider.calls[0]["prompt_version"], "layer2-edge-scout-v2")
        self.assertEqual(len(provider.calls[0]["input_payload"]["candidates"]), 3)
        rows = conn.execute(
            """
            select group_id, included_in_scoring, scout_score, reason, risk
            from l2_scout_results
            order by group_id
            """
        ).fetchall()
        self.assertEqual(
            rows,
            [
                (
                    "group:clicky",
                    1,
                    0.75,
                    "Cursor-adjacent Mac assistant is a new interaction model.",
                    "object_type=product;strong_axes=workflow_shift",
                ),
                (
                    "group:medium",
                    0,
                    0.35,
                    "Interesting but no strong novelty axis.",
                    "object_type=repo;strong_axes=none",
                ),
                (
                    "group:news",
                    0,
                    0.0,
                    "News article, not a product.",
                    "object_type=news;strong_axes=workflow_shift,technical_substance,product_market_fit",
                ),
            ],
        )

    def test_scout_includes_technical_or_product_market_fit_strong_axes(self):
        from pipeline.decision.layer2_scout import scout_edge_watch_groups

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        provider = FakeLLMProvider(
            [
                {
                    "decisions": [
                        {
                            "group_id": "group:hermes",
                            "is_concrete_product": True,
                            "object_type": "repo",
                            "workflow_shift": "medium",
                            "technical_substance": "strong",
                            "product_market_fit": "medium",
                            "confidence": 0.82,
                            "reason": "Self-improving memory and skill loop.",
                        },
                        {
                            "group_id": "group:pmf",
                            "is_concrete_product": True,
                            "object_type": "product",
                            "workflow_shift": "medium",
                            "technical_substance": "weak",
                            "product_market_fit": "strong",
                            "confidence": 0.76,
                            "reason": "Clear user wedge and solved workflow pain.",
                        },
                    ]
                }
            ]
        )

        included = scout_edge_watch_groups(
            conn,
            feed_run_id="l2-run",
            groups=[
                self.group("group:hermes", "Hermes"),
                self.group("group:pmf", "PMF Tool"),
            ],
            provider=provider,
            batch_size=3,
        )

        self.assertEqual(
            [group.group_id for group in included], ["group:hermes", "group:pmf"]
        )
        scores = conn.execute(
            "select group_id, scout_score from l2_scout_results order by group_id"
        ).fetchall()
        self.assertEqual(scores, [("group:hermes", 0.75), ("group:pmf", 0.75)])

    def test_scout_rejects_invalid_provider_shape(self):
        from pipeline.decision.layer2_scout import scout_edge_watch_groups

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        provider = FakeLLMProvider([{"reason": "legacy single object"}])

        with self.assertRaises(ValueError):
            scout_edge_watch_groups(
                conn,
                feed_run_id="l2-run",
                groups=[self.group("group:edge")],
                provider=provider,
            )
