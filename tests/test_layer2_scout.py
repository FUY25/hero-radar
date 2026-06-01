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

    def test_scout_promotes_only_returned_groups_and_defaults_others_to_filtered(self):
        from pipeline.decision.layer2_scout import scout_edge_watch_groups

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        provider = FakeLLMProvider(
            [
                {
                    "promotions": [
                        {
                            "group_id": "group:clicky",
                            "reason_code": "possible_workflow_shift",
                            "reason": (
                                "Cursor-adjacent Mac assistant might be an "
                                "interesting workflow shift."
                            ),
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
        self.assertEqual(provider.calls[0]["prompt_version"], "layer2-edge-scout-v3")
        self.assertEqual(len(provider.calls[0]["input_payload"]["candidates"]), 3)
        self.assertNotIn("evidence_rows", provider.calls[0]["input_payload"]["candidates"][0])
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
                    1.0,
                    "Cursor-adjacent Mac assistant might be an interesting workflow shift.",
                    "reason_code=possible_workflow_shift",
                ),
                (
                    "group:medium",
                    0,
                    0.0,
                    "Not selected by wide scout.",
                    "reason_code=not_selected",
                ),
                (
                    "group:news",
                    0,
                    0.0,
                    "Not selected by wide scout.",
                    "reason_code=not_selected",
                ),
            ],
        )

    def test_scout_defaults_to_batch_size_30(self):
        from pipeline.decision.layer2_scout import scout_edge_watch_groups

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        provider = FakeLLMProvider([{"promotions": []}, {"promotions": []}])
        groups = [self.group(f"group:{index}", f"Project {index}") for index in range(31)]

        included = scout_edge_watch_groups(conn, feed_run_id="l2-run", groups=groups, provider=provider)

        self.assertEqual(included, [])
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(len(provider.calls[0]["input_payload"]["candidates"]), 30)
        self.assertEqual(len(provider.calls[1]["input_payload"]["candidates"]), 1)

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
