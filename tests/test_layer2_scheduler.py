from __future__ import annotations

import unittest

from pipeline.decision.layer2_models import CandidateGroup


class Layer2SchedulerTest(unittest.TestCase):
    def group(self, group_id: str, level: str) -> CandidateGroup:
        return CandidateGroup(
            group_id=group_id,
            canonical_entity_id=f"entity:{group_id}",
            canonical_name=group_id,
            canonical_key=f"name:{group_id}",
            canonical_link="",
            member_entity_ids=[f"entity:{group_id}"],
            level=level,
            source_families=["hn"],
            evidence_hash=f"hash-{group_id}",
        )

    def test_potential_and_high_go_to_scoring_without_scout(self):
        from pipeline.decision.layer2_scheduler import schedule_layer2_work

        schedule = schedule_layer2_work(
            [self.group("a", "potential"), self.group("b", "high_potential")],
            previous_hashes={},
            max_edge_watch_scout=50,
            max_scored_candidates=150,
        )

        self.assertEqual([group.group_id for group in schedule.score_now], ["b", "a"])
        self.assertEqual(schedule.scout_edge_watch, [])

    def test_edge_watch_goes_to_scout_not_semantic_rejection(self):
        from pipeline.decision.layer2_scheduler import schedule_layer2_work

        schedule = schedule_layer2_work(
            [self.group("edge", "edge_watch")],
            previous_hashes={},
            max_edge_watch_scout=50,
            max_scored_candidates=150,
        )

        self.assertEqual(
            [group.group_id for group in schedule.scout_edge_watch], ["edge"]
        )
        self.assertEqual(schedule.skipped, [])

    def test_same_evidence_hash_is_skipped_mechanically(self):
        from pipeline.decision.layer2_scheduler import schedule_layer2_work

        group = self.group("a", "potential")
        schedule = schedule_layer2_work(
            [group],
            previous_hashes={"group:a": "hash-a"},
            max_edge_watch_scout=50,
            max_scored_candidates=150,
        )

        self.assertEqual(schedule.score_now, [])
        self.assertEqual(schedule.skipped[0]["reason"], "unchanged_evidence_hash")
