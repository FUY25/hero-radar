from __future__ import annotations

import sqlite3
import unittest

from pipeline.decision.layer2_models import CandidateGroup
from pipeline.decision.llm_provider import FakeLLMProvider
from pipeline.decision.schema import init_decision_db


class Layer2ScoringTest(unittest.TestCase):
    def test_aggregate_score_uses_weighted_axes_and_penalty(self):
        from pipeline.decision.layer2_scoring import aggregate_l2_score

        score = aggregate_l2_score(
            {
                "momentum": 80,
                "workflow_shift": 90,
                "technical_substance": 70,
                "adoption_path": 60,
                "confidence": 75,
                "derivative_news_penalty": 10,
            }
        )

        self.assertEqual(score, 66.75)

    def test_scores_groups_and_persists_result(self):
        from pipeline.decision.layer2_scoring import score_candidate_groups

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        provider = FakeLLMProvider(
            [
                {
                    "axes": {
                        "momentum": 80,
                        "workflow_shift": 90,
                        "technical_substance": 70,
                        "adoption_path": 60,
                        "confidence": 75,
                        "derivative_news_penalty": 10,
                    },
                    "primary_reason": "Workflow Shift",
                    "topic_tags": ["agent workflow"],
                    "rationale_short": "Concrete repo-native workflow evidence.",
                    "caveats": ["single day signal"],
                }
            ]
        )
        group = CandidateGroup(
            group_id="group:repo",
            canonical_entity_id="entity:repo",
            canonical_name="owner/repo",
            canonical_key="github:owner/repo",
            canonical_link="https://github.com/owner/repo",
            member_entity_ids=["entity:repo"],
            level="potential",
            source_families=["github"],
            evidence_hash="hash",
            context={"evidence_rows": [{"note": "stars"}]},
        )

        scores = score_candidate_groups(
            conn, feed_run_id="l2-run", groups=[group], provider=provider
        )

        self.assertEqual(scores[0]["l2_score"], 66.75)
        row = conn.execute(
            "select l2_score, primary_reason, provider, model from l2_scores"
        ).fetchone()
        self.assertEqual(row, (66.75, "Workflow Shift", "fake", "fake-json"))

    def test_scoring_repairs_missing_axes_once(self):
        from pipeline.decision.layer2_scoring import score_candidate_groups

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        provider = FakeLLMProvider(
            [
                {"primary_reason": "Missing axes"},
                {
                    "axes": {
                        "momentum": 80,
                        "workflow_shift": 80,
                        "technical_substance": 80,
                        "adoption_path": 80,
                        "confidence": 80,
                        "derivative_news_penalty": 0,
                    },
                    "primary_reason": "Repaired",
                    "topic_tags": ["agent workflow"],
                    "rationale_short": "Repaired response.",
                    "caveats": [],
                },
            ]
        )
        group = CandidateGroup(
            group_id="group:repo",
            canonical_entity_id="entity:repo",
            canonical_name="owner/repo",
            canonical_key="github:owner/repo",
            canonical_link="https://github.com/owner/repo",
            member_entity_ids=["entity:repo"],
            level="potential",
            source_families=["github"],
            evidence_hash="hash",
            context={"evidence_rows": [{"note": "stars"}]},
        )

        scores = score_candidate_groups(
            conn, feed_run_id="l2-run", groups=[group], provider=provider
        )

        self.assertEqual(scores[0]["primary_reason"], "Repaired")
        self.assertEqual(
            [call["task"] for call in provider.calls],
            ["layer2_scoring", "layer2_scoring_repair"],
        )
        row = conn.execute("select primary_reason from l2_scores").fetchone()
        self.assertEqual(row[0], "Repaired")
