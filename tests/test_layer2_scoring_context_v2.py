from __future__ import annotations

import json
import sqlite3
import unittest

from pipeline.decision.layer2_models import CandidateGroup
from pipeline.decision.llm_provider import FakeLLMProvider
from pipeline.decision.schema import init_decision_db


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
        evidence_hash="evidence-hash",
        context={
            "members": [
                {
                    "entity_id": "entity:repo",
                    "canonical_link": "https://github.com/owner/repo",
                    "binding_confidence": "verified",
                    "context_preview": "README documents a validation harness.",
                    "readme_excerpt_available": True,
                    "source_families": ["github"],
                }
            ],
            "evidence_rows": [
                {
                    "id": 42,
                    "source": "github_api",
                    "family": "github",
                    "metric_name": "readme_workflow",
                    "metric_value": "validation harness",
                    "signal_label": "first-party workflow proof",
                    "note": "README documents a validation harness.",
                }
            ],
        },
    )


def attributable_final() -> dict:
    return {
        "action": "final",
        "information_sufficiency": {
            "identity": "strong",
            "workflow_shift": "strong",
            "technical_substance": "strong",
            "product_market_fit": "medium",
            "momentum": "medium",
        },
        "score": {
            "object_type": "repo",
            "is_product_or_repo": True,
            "axes": {
                "workflow_shift": 82,
                "technical_substance": 88,
                "product_market_fit": 76,
                "momentum": 70,
                "confidence": 85,
                "risk_penalty": 3,
                "derivative_news_penalty": 0,
            },
            "supporting_evidence": [
                {
                    "claim": "The README documents a validation harness.",
                    "evidence_refs": ["evidence:42"],
                    "supports_axes": ["workflow_shift", "technical_substance"],
                    "claim_type": "observed",
                }
            ],
            "negative_evidence": [],
            "known_gaps": ["Long-term adoption is not yet established."],
            "primary_reason": "Validation harness",
            "rationale_short": "The repository supports a concrete validation workflow.",
            "topic_tags": ["agent tooling"],
            "caveats": [],
            "should_print": True,
        },
    }


class Layer2ScoringContextV2Test(unittest.TestCase):
    def test_cannot_score_is_deterministic_and_skips_provider(self):
        from pipeline.decision.layer2_scoring_investigator import score_with_investigator

        empty_group = CandidateGroup(
            group_id="group:empty",
            canonical_entity_id="",
            canonical_name="",
            canonical_key="",
            canonical_link="",
            member_entity_ids=[],
            level="watch",
            source_families=[],
            context={},
        )
        provider = FakeLLMProvider([])
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)

        result = score_with_investigator(
            conn,
            feed_run_id="l2-run",
            groups=[empty_group],
            provider=provider,
            tools={},
        )[0]

        self.assertEqual(provider.calls, [])
        self.assertEqual(result["l2_score"], 0)
        self.assertFalse(result["should_print"])
        self.assertIn("neither an identifiable identity", result["known_gaps"][0])

    def test_direct_final_gate_hides_tools_and_sets_must_finalize_for_rich_context(self):
        from dataclasses import replace

        from pipeline.decision.layer2_scoring_investigator import score_with_investigator

        group = make_group()
        rich_context = dict(group.context)
        rich_context["members"] = [
            {
                **rich_context["members"][0],
                "context_preview": "documented workflow and architecture " * 12,
            }
        ]
        rich_context["evidence_rows"] = [
            *rich_context["evidence_rows"],
            {
                "id": 43,
                "source": "github_backfill",
                "family": "github",
                "metric_name": "release_count",
                "metric_value": "12",
            },
        ]
        provider = FakeLLMProvider([attributable_final()])
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)

        score_with_investigator(
            conn,
            feed_run_id="l2-run",
            groups=[replace(group, context=rich_context)],
            provider=provider,
            tools={"unused": lambda _arguments: {"status": "ok"}},
            direct_final_enabled=True,
        )

        payload = provider.calls[0]["input_payload"]
        self.assertEqual(payload["task"]["mode"], "score_from_context")
        self.assertTrue(payload["task"]["must_finalize"])
        self.assertEqual(payload["available_tools"], [])

    def test_provider_request_and_persistence_use_context_and_claim_contracts_v2(self):
        from pipeline.decision.layer2_scoring_investigator import (
            DEFAULT_INVESTIGATOR_PROMPT_VERSION,
            score_with_investigator,
        )

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)
        provider = FakeLLMProvider([attributable_final()])

        result = score_with_investigator(
            conn,
            feed_run_id="l2-run",
            groups=[make_group()],
            provider=provider,
            tools={},
        )[0]

        call = provider.calls[0]
        payload = call["input_payload"]
        self.assertEqual(DEFAULT_INVESTIGATOR_PROMPT_VERSION, "layer2-scoring-investigator-v2")
        self.assertEqual(payload["candidate"]["identity"]["group_id"], "group:repo")
        self.assertNotIn("candidate_identity", payload)
        self.assertNotIn("known_facts", payload["working_state"])
        self.assertEqual(payload["output_schema"]["$id"], "layer2-scoring-output-v2")
        self.assertIn("untrusted external evidence", call["system_prompt"])
        self.assertEqual(
            result["supporting_claims"][0]["evidence_refs"], ["evidence:42"]
        )
        score_row = conn.execute(
            "select supporting_claims_json, known_gaps_json from l2_scores"
        ).fetchone()
        self.assertEqual(json.loads(score_row[0])[0]["claim_type"], "observed")
        self.assertEqual(
            json.loads(score_row[1]), ["Long-term adoption is not yet established."]
        )
        investigation = conn.execute(
            "select context_manifests_json from l2_scoring_investigations"
        ).fetchone()
        manifests = json.loads(investigation[0])
        self.assertEqual(manifests[0]["included_evidence_ids"], ["evidence:42"])

    def test_tool_schema_executor_and_observation_come_from_one_toolspec(self):
        from pipeline.decision.layer2_scoring_investigator import score_with_investigator
        from pipeline.decision.layer2_tool_registry import ToolSpec

        calls: list[dict] = []

        def execute(arguments: dict) -> dict:
            calls.append(arguments)
            return {"status": "ok", "excerpt": "Documented workflow."}

        def project(result: dict, observation_id: str, arguments: dict) -> dict:
            return {
                "observation_id": observation_id,
                "tool": "fetch_docs",
                "status": result["status"],
                "trust": "external_untrusted",
                "provenance": {"url": arguments["url"]},
                "facts": {"documented_workflow": True},
                "excerpt": result["excerpt"],
                "truncated": False,
                "relevant_axes": ["workflow_shift"],
            }

        spec = ToolSpec(
            name="fetch_docs",
            version="1",
            description="Fetch first-party documentation.",
            input_schema={
                "type": "object",
                "properties": {"url": {"type": "string", "minLength": 1}},
                "required": ["url"],
                "additionalProperties": False,
            },
            family="homepage",
            cost="remote_cached_medium",
            executor=execute,
            availability=lambda _candidate: True,
            timeout_seconds=10,
            max_result_tokens=1_000,
            cache_policy="url",
            concurrency_key="homepage",
            max_in_flight=2,
            starts_per_second=1.0,
            result_projector=project,
        )
        provider = FakeLLMProvider(
            [
                {
                    "action": "use_tools",
                    "information_sufficiency": {
                        "identity": "strong",
                        "workflow_shift": "weak",
                        "technical_substance": "medium",
                        "product_market_fit": "medium",
                        "momentum": "medium",
                    },
                    "information_need": {
                        "question": "What workflow is documented?",
                        "target_axes": ["workflow_shift"],
                        "expected_decision_impact": "May raise workflow_shift.",
                    },
                    "tool_requests": [
                        {
                            "name": "fetch_docs",
                            "arguments": {"url": "https://example.com/docs"},
                        }
                    ],
                },
                attributable_final(),
            ]
        )
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)

        result = score_with_investigator(
            conn,
            feed_run_id="l2-run",
            groups=[make_group()],
            provider=provider,
            tools={"fetch_docs": spec.execute},
            tool_specs={"fetch_docs": spec},
        )[0]

        model_tool = provider.calls[0]["input_payload"]["available_tools"][0]
        self.assertEqual(model_tool["name"], "fetch_docs")
        self.assertFalse(model_tool["input_schema"]["additionalProperties"])
        self.assertEqual(calls, [{"url": "https://example.com/docs"}])
        self.assertEqual(result["observations"][0]["trust"], "external_untrusted")
        self.assertEqual(
            provider.calls[1]["input_payload"]["working_state"][
                "verified_observations"
            ][0]["facts"],
            {"documented_workflow": True},
        )


if __name__ == "__main__":
    unittest.main()
