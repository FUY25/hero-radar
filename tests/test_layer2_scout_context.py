from __future__ import annotations

import json
import unittest

from pipeline.decision.layer2_models import CandidateGroup


class Layer2ScoutContextTest(unittest.TestCase):
    def test_scout_context_uses_qualitative_fields_and_excludes_metrics(self):
        from pipeline.decision.layer2_scout_context import scout_context_for_group

        group = CandidateGroup(
            group_id="group:clicky",
            canonical_entity_id="entity:clicky",
            canonical_name="Clicky",
            canonical_key="domain:heyclicky.com",
            canonical_link="https://www.heyclicky.com/",
            member_entity_ids=["entity:clicky"],
            level="edge_watch",
            source_families=["hn", "x_social"],
            context={
                "evidence_rows": [
                    {
                        "metric_name": "hn_max_points_7d",
                        "metric_value": "144",
                        "note": "Clicky: AI buddy that lives on your Mac.",
                        "source": "hn_firebase",
                    }
                ],
                "members": [
                    {
                        "entity_id": "entity:clicky",
                        "canonical_link": "https://www.heyclicky.com/",
                        "context_preview": (
                            "An AI buddy that lives on your Mac and sees what you see."
                        ),
                        "readme_excerpt_available": False,
                        "source_links": [
                            {
                                "source": "hn_firebase",
                                "channel": "hn_top",
                                "name": "Clicky",
                                "external_url": "https://www.heyclicky.com/",
                                "author": "farza",
                            }
                        ],
                    }
                ],
            },
        )

        view = scout_context_for_group(group)

        self.assertEqual(view["group_id"], "group:clicky")
        self.assertEqual(view["candidate"]["name"], "Clicky")
        self.assertEqual(
            view["candidate"]["canonical_link"], "https://www.heyclicky.com/"
        )
        self.assertIn("AI buddy", view["candidate"]["project_context"][0])
        self.assertIn("AI buddy", view["candidate"]["qualitative_summaries"][0])
        self.assertEqual(view["source_context"][0]["title"], "Clicky")
        self.assertEqual(view["source_context"][0]["author"], "farza")
        serialized = json.dumps(view)
        self.assertNotIn("evidence_rows", view)
        self.assertNotIn("hn_max_points_7d", serialized)
        self.assertNotIn("144", serialized)

    def test_scout_context_keeps_readme_excerpt_as_project_context(self):
        from pipeline.decision.layer2_scout_context import scout_context_for_group

        readme = "Agent runtime " + ("memory skills browser control " * 80)
        group = CandidateGroup(
            group_id="group:agent",
            canonical_entity_id="entity:agent",
            canonical_name="Agent Runtime",
            canonical_key="github:owner/agent-runtime",
            canonical_link="https://github.com/owner/agent-runtime",
            member_entity_ids=["entity:agent"],
            level="edge_watch",
            source_families=["github"],
            context={
                "members": [
                    {
                        "entity_id": "entity:agent",
                        "canonical_link": "https://github.com/owner/agent-runtime",
                        "context_preview": readme,
                        "readme_excerpt_available": True,
                        "source_links": [],
                    }
                ]
            },
        )

        view = scout_context_for_group(group)

        self.assertTrue(view["candidate"]["has_readme"])
        self.assertIn(
            "memory skills browser control", view["candidate"]["project_context"][0]
        )
        self.assertGreater(len(view["candidate"]["project_context"][0]), 1000)

    def test_wide_scout_context_is_compact(self):
        from pipeline.decision.layer2_scout_context import wide_scout_context_for_group

        group = CandidateGroup(
            group_id="group:clicky",
            canonical_entity_id="entity:clicky",
            canonical_name="Clicky",
            canonical_key="domain:heyclicky.com",
            canonical_link="https://www.heyclicky.com/",
            member_entity_ids=["entity:clicky"],
            level="edge_watch",
            source_families=["hn", "x_social"],
            context={
                "evidence_rows": [
                    {
                        "metric_name": "hn_max_points_7d",
                        "metric_value": "144",
                        "note": "Clicky: screen-aware Mac assistant.",
                    }
                ],
                "members": [
                    {
                        "context_preview": "Clicky lives beside your cursor on Mac.",
                        "readme_excerpt_available": False,
                        "source_links": [
                            {
                                "source": "hn_firebase",
                                "channel": "hn_top",
                                "name": "Show HN: Clicky",
                                "external_url": "https://www.heyclicky.com/",
                            }
                        ],
                    }
                ],
            },
        )

        view = wide_scout_context_for_group(group)

        self.assertEqual(
            view,
            {
                "group_id": "group:clicky",
                "name": "Clicky",
                "link": "https://www.heyclicky.com/",
                "object_hint": "domain",
                "one_liner": "Clicky lives beside your cursor on Mac.",
                "source_titles": ["Show HN: Clicky"],
                "source_types": ["hn_firebase"],
            },
        )
        serialized = json.dumps(view)
        self.assertNotIn("hn_max_points_7d", serialized)
        self.assertNotIn("144", serialized)


if __name__ == "__main__":
    unittest.main()
