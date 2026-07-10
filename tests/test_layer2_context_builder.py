from __future__ import annotations

import unittest


class Layer2ContextBuilderTest(unittest.TestCase):
    def test_builds_one_candidate_packet_and_an_inspectable_budget_manifest(self):
        from pipeline.decision.layer2_context_builder import (
            ContextBudget,
            ScoringContextBuilder,
        )

        result = ScoringContextBuilder().build(
            task={
                "mode": "investigate",
                "turn_index": 1,
                "must_finalize": False,
            },
            candidate={
                "identity": {
                    "group_id": "group:repo",
                    "canonical_name": "owner/repo",
                    "canonical_link": "https://github.com/owner/repo",
                },
                "hard_facts": {"level": "potential", "source_families": ["github"]},
                "context_summary": "A repository with a validation workflow.",
            },
            evidence_rows=[
                {
                    "evidence_id": "evidence:readme",
                    "source": "github_readme",
                    "claim": "README documents the validation workflow.",
                    "decision_value": 90,
                },
                {
                    "evidence_id": "evidence:stars",
                    "source": "github",
                    "claim": "The repository gained 321 stars in 24 hours.",
                    "decision_value": 40,
                },
            ],
            observations=[],
            previous_turn=None,
            raw_tool_results=[],
            active_tools=[
                {
                    "name": "fetch_github_readme",
                    "description": "Fetch a repository README.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"repo_key": {"type": "string"}},
                        "required": ["repo_key"],
                        "additionalProperties": False,
                    },
                }
            ],
            remaining_budget={"turns": 2, "tool_calls": 6},
            system_prompt="Stable scoring policy.",
            output_schema={"type": "object", "required": ["action"]},
            budget=ContextBudget(max_context_tokens=4_000),
        )

        self.assertEqual(
            result.payload["candidate"]["identity"]["group_id"], "group:repo"
        )
        self.assertNotIn("candidate_identity", result.payload)
        self.assertEqual(
            [row["evidence_id"] for row in result.payload["candidate"]["top_evidence"]],
            ["evidence:readme", "evidence:stars"],
        )
        self.assertEqual(
            result.manifest["included_evidence_ids"],
            ["evidence:readme", "evidence:stars"],
        )
        self.assertIn("system_prompt", result.manifest["section_tokens"])
        self.assertIn("tool_schemas", result.manifest["section_tokens"])
        self.assertLessEqual(
            result.manifest["estimated_input_tokens"],
            result.manifest["maximum_input_tokens"],
        )

    def test_prunes_low_value_evidence_and_marks_it_retrievable(self):
        from pipeline.decision.layer2_context_builder import (
            ContextBudget,
            ScoringContextBuilder,
        )

        evidence_rows = [
            {
                "evidence_id": f"evidence:{index}",
                "claim": f"Evidence {index}: " + ("detail " * 100),
                "decision_value": 100 - index,
            }
            for index in range(6)
        ]
        result = ScoringContextBuilder().build(
            task={"mode": "investigate", "turn_index": 1},
            candidate={
                "identity": {"group_id": "group:repo", "canonical_name": "owner/repo"},
                "hard_facts": {"level": "potential"},
                "context_summary": "Repository summary.",
            },
            evidence_rows=evidence_rows,
            observations=[],
            previous_turn=None,
            raw_tool_results=[],
            active_tools=[],
            remaining_budget={"turns": 2},
            system_prompt="Stable scoring policy.",
            output_schema={"type": "object"},
            budget=ContextBudget(
                max_context_tokens=3_500,
                output_reserve=1_800,
                top_evidence_allocation=450,
            ),
        )

        included = result.manifest["included_evidence_ids"]
        retrievable = result.manifest["retrievable_evidence_ids"]
        self.assertGreater(len(included), 0)
        self.assertLess(len(included), len(evidence_rows))
        self.assertEqual(included + retrievable, [f"evidence:{index}" for index in range(6)])
        self.assertEqual(
            result.payload["candidate"]["evidence_availability"],
            {"omitted_count": len(retrievable), "retrievable": True},
        )

    def test_keeps_structured_observations_and_only_the_most_recent_raw_result(self):
        from pipeline.decision.layer2_context_builder import (
            ContextBudget,
            ScoringContextBuilder,
        )

        observations = [
            {
                "observation_id": f"observation:{index}",
                "tool": "fetch_github_readme",
                "status": "ok",
                "trust": "external_untrusted",
                "projected_facts": [f"Fact {index}"],
                "excerpt": "detail " * 80,
                "requested_turn": 1,
                "request_index": index,
            }
            for index in range(3)
        ]
        result = ScoringContextBuilder().build(
            task={"mode": "investigate", "turn_index": 2},
            candidate={
                "identity": {"group_id": "group:repo", "canonical_name": "owner/repo"},
                "hard_facts": {"level": "potential"},
                "context_summary": "Repository summary.",
            },
            evidence_rows=[],
            observations=observations,
            previous_turn={"information_need": "Need workflow proof."},
            raw_tool_results=[
                {"observation_id": "observation:0", "result": "old"},
                {"observation_id": "observation:1", "result": "middle"},
                {"observation_id": "observation:2", "result": "newest"},
            ],
            active_tools=[],
            remaining_budget={"turns": 1},
            system_prompt="Stable scoring policy.",
            output_schema={"type": "object"},
            budget=ContextBudget(
                max_context_tokens=4_000,
                tool_observation_allocation=350,
                recent_raw_tool_result_count=1,
            ),
        )

        included = result.manifest["included_observation_ids"]
        retrievable = result.manifest["retrievable_observation_ids"]
        self.assertGreater(len(included), 0)
        self.assertLess(len(included), len(observations))
        self.assertEqual(
            included + retrievable,
            ["observation:0", "observation:1", "observation:2"],
        )
        self.assertEqual(
            result.payload["working_state"]["recent_raw_tool_results"],
            [{"observation_id": "observation:2", "result": "newest"}],
        )

    def test_bounds_candidate_summary_without_truncating_identity(self):
        from pipeline.decision.layer2_context_builder import (
            ContextBudget,
            ScoringContextBuilder,
        )

        result = ScoringContextBuilder().build(
            task={"mode": "investigate", "turn_index": 1},
            candidate={
                "identity": {"group_id": "group:repo", "canonical_name": "owner/repo"},
                "hard_facts": {"level": "potential"},
                "context_summary": {"readme": "workflow " * 1_000},
            },
            evidence_rows=[],
            observations=[],
            previous_turn=None,
            raw_tool_results=[],
            active_tools=[],
            remaining_budget={"turns": 2},
            system_prompt="Stable scoring policy.",
            output_schema={"type": "object"},
            budget=ContextBudget(
                max_context_tokens=4_000,
                evidence_summary_allocation=120,
            ),
        )

        self.assertEqual(
            result.payload["candidate"]["identity"],
            {"group_id": "group:repo", "canonical_name": "owner/repo"},
        )
        self.assertTrue(result.payload["candidate"]["context_summary"]["truncated"])
        self.assertLessEqual(result.manifest["section_tokens"]["candidate_summary"], 120)

    def test_drops_low_priority_recent_raw_result_before_failing_preflight(self):
        from pipeline.decision.layer2_context_builder import (
            ContextBudget,
            ScoringContextBuilder,
        )

        result = ScoringContextBuilder().build(
            task={"mode": "investigate", "turn_index": 2},
            candidate={
                "identity": {"group_id": "group:repo", "canonical_name": "owner/repo"},
                "hard_facts": {"level": "potential"},
                "context_summary": "Repository summary.",
            },
            evidence_rows=[],
            observations=[
                {
                    "observation_id": "tool:t1:0",
                    "tool": "fetch_docs",
                    "status": "ok",
                    "trust": "external_untrusted",
                    "facts": {"workflow": True},
                    "excerpt": "bounded observation",
                }
            ],
            previous_turn={"question": "What workflow is documented?"},
            raw_tool_results=[
                {"observation_id": "tool:t1:0", "result": "raw " * 2_000}
            ],
            active_tools=[],
            remaining_budget={"turns": 1},
            system_prompt="Stable scoring policy.",
            output_schema={"type": "object"},
            budget=ContextBudget(max_context_tokens=3_000, output_reserve=1_800),
        )

        self.assertNotIn(
            "recent_raw_tool_results", result.payload["working_state"]
        )
        self.assertEqual(
            result.payload["working_state"]["verified_observations"][0][
                "observation_id"
            ],
            "tool:t1:0",
        )
        self.assertIn("tool:t1:0", result.manifest["excluded_raw_result_ids"])


if __name__ == "__main__":
    unittest.main()
