from __future__ import annotations

import sqlite3
import unittest

from pipeline.decision.layer2_models import CandidateGroup
from pipeline.decision.llm_provider import FakeLLMProvider
from pipeline.decision.schema import init_decision_db


class Layer2ScoutTest(unittest.TestCase):
    def test_scout_validates_and_persists_include_decision(self):
        from pipeline.decision.layer2_scout import scout_edge_watch_groups

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        provider = FakeLLMProvider(
            [
                {
                    "include_in_l2_scoring": True,
                    "scout_score": 0.73,
                    "reason": "Concrete early workflow evidence.",
                    "needed_context": ["readme"],
                    "risk": "single source",
                    "confidence": 0.7,
                }
            ]
        )
        group = CandidateGroup(
            group_id="group:edge",
            canonical_entity_id="entity:edge",
            canonical_name="Edge Project",
            canonical_key="name:edge-project",
            canonical_link="",
            member_entity_ids=["entity:edge"],
            level="edge_watch",
            source_families=["hn"],
            evidence_hash="hash",
            context={"evidence_rows": [{"note": "Show HN project"}]},
        )

        included = scout_edge_watch_groups(
            conn,
            feed_run_id="l2-run",
            groups=[group],
            provider=provider,
            prompt_version="layer2-edge-scout-v1",
        )

        self.assertEqual([row.group_id for row in included], ["group:edge"])
        row = conn.execute(
            "select included_in_scoring, reason, provider, model from l2_scout_results"
        ).fetchone()
        self.assertEqual(
            row, (1, "Concrete early workflow evidence.", "fake", "fake-json")
        )
        self.assertEqual(provider.calls[0]["task"], "layer2_edge_scout")

    def test_scout_rejects_invalid_provider_shape(self):
        from pipeline.decision.layer2_scout import scout_edge_watch_groups

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        provider = FakeLLMProvider([{"reason": "missing fields"}])
        group = CandidateGroup(
            group_id="group:edge",
            canonical_entity_id="entity:edge",
            canonical_name="Edge Project",
            canonical_key="name:edge-project",
            canonical_link="",
            member_entity_ids=["entity:edge"],
            level="edge_watch",
            source_families=["hn"],
            evidence_hash="hash",
            context={},
        )

        with self.assertRaises(ValueError):
            scout_edge_watch_groups(
                conn,
                feed_run_id="l2-run",
                groups=[group],
                provider=provider,
            )
