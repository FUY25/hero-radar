from __future__ import annotations

import unittest

from pipeline.decision.layer2_models import CandidateGroup
from pipeline.decision.llm_provider import FakeLLMProvider


class Layer2BriefPacketTest(unittest.TestCase):
    @staticmethod
    def make_group() -> CandidateGroup:
        return CandidateGroup(
            group_id="group:repo",
            canonical_entity_id="entity:repo",
            canonical_name="owner/repo",
            canonical_key="github:owner/repo",
            canonical_link="https://github.com/owner/repo",
            member_entity_ids=["entity:repo"],
            level="potential",
            source_families=["github"],
            context={"raw": "must not be copied wholesale"},
        )

    def test_builds_compact_attributable_packet_without_investigation_process_noise(self):
        from pipeline.decision.layer2_brief_packet import build_brief_writer_packet

        group = self.make_group()
        packet = build_brief_writer_packet(
            {
                "group": group,
                "object_type": "repo",
                "l2_score": 84,
                "primary_reason": "Validation workflow",
                "topic_tags": ["agent tooling"],
                "caveats": ["Early project"],
                "known_gaps": ["Adoption durability"],
                "supporting_claims": [
                    {
                        "claim": "The repository exposes a validation harness.",
                        "evidence_refs": ["evidence:12"],
                        "supports_axes": ["workflow_shift", "technical_substance"],
                        "claim_type": "observed",
                    }
                ],
                "negative_claims": [],
                "observations": [
                    {
                        "observation_id": "tool:t1:0",
                        "facts": {"interaction_model": "CLI"},
                        "relevant_axes": ["workflow_shift"],
                        "trust": "external_untrusted",
                    }
                ],
                "trace": [{"action": "use_tools"}],
                "tool_trace": [{"tool": "fetch_github_readme", "status": "ok"}],
                "cache_key": "secret-process-noise",
            },
            output_schema={"$id": "layer2-brief-output-v1", "type": "object"},
        )

        self.assertEqual(packet["candidate"]["identity"]["group_id"], "group:repo")
        self.assertEqual(
            packet["candidate"]["project_facts"][0]["evidence_refs"],
            ["evidence:12"],
        )
        self.assertEqual(packet["candidate"]["object_type"], "repo")
        self.assertNotIn("candidate_identity", packet)
        self.assertNotIn("investigation_trace", packet)
        self.assertNotIn("tool_trace", packet)
        self.assertNotIn("cache_key", packet)
        self.assertNotIn("available_tools", packet)

    def test_brief_provider_receives_only_the_compact_packet(self):
        from pipeline.decision.layer2_scoring_investigator import build_deepdive_brief

        provider = FakeLLMProvider(
            [
                {
                    "category": {"primary": "开发工具", "tags": ["agent"]},
                    "headline": "一个可验证的代理工作流",
                    "core_highlights": ["提供可执行验证 harness"],
                    "use_cases": ["开发者验证代理行为"],
                }
            ]
        )
        build_deepdive_brief(
            row={
                "group": self.make_group(),
                "object_type": "repo",
                "l2_score": 84,
                "primary_reason": "Validation workflow",
                "supporting_claims": [
                    {
                        "claim": "README documents a validation harness.",
                        "evidence_refs": ["evidence:12"],
                        "supports_axes": ["technical_substance"],
                        "claim_type": "observed",
                    }
                ],
                "trace": [{"action": "use_tools"}],
                "tool_trace": [{"tool": "fetch_github_readme", "status": "ok"}],
            },
            provider=provider,
        )

        payload = provider.calls[0]["input_payload"]
        self.assertEqual(payload["candidate"]["identity"]["group_id"], "group:repo")
        self.assertNotIn("candidate_identity", payload)
        self.assertNotIn("investigation_trace", payload)
        self.assertNotIn("tool_trace", payload)
        self.assertNotIn("schema", payload)
        self.assertIn("output_schema", payload)


if __name__ == "__main__":
    unittest.main()
