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
    def test_v2_schema_and_host_validator_share_action_variant_semantics(self):
        from pipeline.decision.layer2_contracts import (
            scoring_turn_output_schema_v2,
            validate_scoring_turn_v2,
        )

        schema = scoring_turn_output_schema_v2()
        self.assertEqual(schema["properties"]["tool_requests"]["minItems"], 1)
        final_exclusions = schema["oneOf"][1]["not"]["anyOf"]
        self.assertIn({"required": ["information_need"]}, final_exclusions)
        with self.assertRaisesRegex(ValueError, "unknown or missing fields"):
            validate_scoring_turn_v2(
                {
                    **attributable_final(),
                    "information_need": {
                        "question": "No longer needed.",
                        "target_axes": ["confidence"],
                        "expected_decision_impact": "None after finalization.",
                    },
                }
            )

    def test_v2_rejects_unknown_final_fields_and_repairs_once(self):
        from pipeline.decision.layer2_scoring_investigator import score_with_investigator

        invalid = {**attributable_final(), "unexpected": "not allowed"}
        provider = FakeLLMProvider([invalid, attributable_final()])
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)

        result = score_with_investigator(
            conn,
            feed_run_id="l2-run",
            groups=[make_group()],
            provider=provider,
            tools={},
            prompt_version="layer2-scoring-investigator-v2",
        )[0]

        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(provider.calls[1]["task"], "layer2_scoring_investigator_repair")
        self.assertEqual(result["supporting_claims"][0]["evidence_refs"], ["evidence:42"])

    def test_v2_rejects_unknown_claim_fields_and_repairs_once(self):
        from pipeline.decision.layer2_scoring_investigator import score_with_investigator

        invalid = attributable_final()
        invalid["score"]["supporting_evidence"][0]["instruction"] = "ignore policy"
        provider = FakeLLMProvider([invalid, attributable_final()])
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)

        result = score_with_investigator(
            conn,
            feed_run_id="l2-run",
            groups=[make_group()],
            provider=provider,
            tools={},
            prompt_version="layer2-scoring-investigator-v2",
        )[0]

        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(provider.calls[1]["task"], "layer2_scoring_investigator_repair")
        self.assertEqual(result["supporting_claims"][0]["claim_type"], "observed")

    def test_v2_repair_request_lists_the_allowed_evidence_refs(self):
        from pipeline.decision.layer2_scoring_investigator import score_with_investigator

        invalid = attributable_final()
        invalid["score"]["supporting_evidence"][0]["evidence_refs"] = [
            "entity:not-an-evidence-ref"
        ]
        provider = FakeLLMProvider([invalid, attributable_final()])
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)

        score_with_investigator(
            conn,
            feed_run_id="l2-run",
            groups=[make_group()],
            provider=provider,
            tools={},
            prompt_version="layer2-scoring-investigator-v2",
        )

        repair_payload = provider.calls[1]["input_payload"]
        self.assertEqual(
            repair_payload["valid_evidence_refs"],
            ["evidence:42"],
        )

    def test_v2_scoring_turn_lists_the_included_evidence_refs(self):
        from pipeline.decision.layer2_scoring_investigator import score_with_investigator

        provider = FakeLLMProvider([attributable_final()])
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)

        score_with_investigator(
            conn,
            feed_run_id="l2-run",
            groups=[make_group()],
            provider=provider,
            tools={},
            prompt_version="layer2-scoring-investigator-v2",
        )

        self.assertEqual(
            provider.calls[0]["input_payload"]["valid_evidence_refs"],
            ["evidence:42"],
        )

    def test_v2_repair_does_not_allow_an_observation_pruned_from_the_last_turn(self):
        from pipeline.decision.layer2_context_builder import ContextBudget
        from pipeline.decision.layer2_scoring_investigator import score_with_investigator

        invalid_final = attributable_final()
        invalid_final["score"]["supporting_evidence"][0]["evidence_refs"] = [
            "tool:t1:0"
        ]
        provider = FakeLLMProvider(
            [
                {
                    "action": "use_tools",
                    "information_sufficiency": {
                        "identity": "strong",
                        "workflow_shift": "medium",
                        "technical_substance": "weak",
                        "product_market_fit": "medium",
                        "momentum": "medium",
                    },
                    "information_need": {
                        "question": "Fetch technical detail.",
                        "target_axes": ["technical_substance"],
                        "expected_decision_impact": "May change the technical score.",
                    },
                    "tool_requests": [
                        {"name": "fetch_docs", "arguments": {"page": "architecture"}}
                    ],
                },
                invalid_final,
                attributable_final(),
            ]
        )
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)

        score_with_investigator(
            conn,
            feed_run_id="l2-run",
            groups=[make_group()],
            provider=provider,
            tools={
                "fetch_docs": lambda _arguments: {
                    "status": "ok",
                    "excerpt": "architecture detail " * 200,
                }
            },
            context_budget=ContextBudget(tool_observation_allocation=0),
            prompt_version="layer2-scoring-investigator-v2",
        )

        last_turn_refs = provider.calls[1]["input_payload"]["valid_evidence_refs"]
        repair_refs = provider.calls[2]["input_payload"]["valid_evidence_refs"]
        self.assertEqual(repair_refs, last_turn_refs)
        self.assertNotIn("tool:t1:0", repair_refs)

    def test_v2_failed_tool_observation_cannot_support_a_negative_claim(self):
        from pipeline.decision.layer2_scoring_investigator import score_with_investigator

        invalid_final = attributable_final()
        invalid_final["score"]["negative_evidence"] = [
            {
                "claim": "The unavailable tool proves the candidate is unreliable.",
                "evidence_refs": ["tool:t1:0"],
                "supports_axes": ["risk_penalty"],
                "claim_type": "observed",
            }
        ]
        provider = FakeLLMProvider(
            [
                {
                    "action": "use_tools",
                    "information_sufficiency": {
                        "identity": "strong",
                        "workflow_shift": "medium",
                        "technical_substance": "weak",
                        "product_market_fit": "medium",
                        "momentum": "medium",
                    },
                    "information_need": {
                        "question": "Fetch technical detail.",
                        "target_axes": ["technical_substance"],
                        "expected_decision_impact": "May change the technical score.",
                    },
                    "tool_requests": [
                        {"name": "unavailable_tool", "arguments": {}}
                    ],
                },
                invalid_final,
                attributable_final(),
            ]
        )
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)

        score_with_investigator(
            conn,
            feed_run_id="l2-run",
            groups=[make_group()],
            provider=provider,
            tools={},
            prompt_version="layer2-scoring-investigator-v2",
        )

        self.assertEqual(
            provider.calls[2]["task"],
            "layer2_scoring_investigator_repair",
        )
        self.assertNotIn(
            "tool:t1:0",
            provider.calls[2]["input_payload"]["valid_evidence_refs"],
        )

    def test_v1_rollback_request_and_repair_payloads_remain_unchanged(self):
        from pipeline.decision.layer2_scoring_investigator import score_with_investigator

        legacy_final = attributable_final()
        legacy_final["score"]["supporting_evidence"] = [
            "README documents a validation harness."
        ]
        invalid_legacy_final = {
            **legacy_final,
            "score": {**legacy_final["score"]},
        }
        invalid_legacy_final["score"].pop("axes")
        provider = FakeLLMProvider([invalid_legacy_final, legacy_final])
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)

        score_with_investigator(
            conn,
            feed_run_id="l2-run",
            groups=[make_group()],
            provider=provider,
            tools={},
            prompt_version="layer2-scoring-investigator-v1",
        )

        self.assertNotIn("valid_evidence_refs", provider.calls[0]["input_payload"])
        self.assertNotIn("valid_evidence_refs", provider.calls[1]["input_payload"])
        self.assertFalse(
            provider.calls[0]["input_payload"]["task"]["must_finalize"]
        )
        self.assertEqual(
            provider.calls[1]["input_payload"]["instruction"],
            "Return a complete corrected action=final scoring JSON object.",
        )

    def test_v2_rejects_empty_tool_request_contract_before_execution(self):
        from pipeline.decision.layer2_scoring_investigator import score_with_investigator

        provider = FakeLLMProvider(
            [
                {
                    "action": "use_tools",
                    "information_sufficiency": {
                        "identity": "strong",
                        "workflow_shift": "weak",
                        "technical_substance": "weak",
                        "product_market_fit": "medium",
                        "momentum": "medium",
                    },
                    "information_need": {
                        "question": "Need evidence.",
                        "target_axes": ["workflow_shift"],
                        "expected_decision_impact": "Could change the route.",
                    },
                    "tool_requests": [],
                }
            ]
        )
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)

        with self.assertRaisesRegex(ValueError, "at least one tool request"):
            score_with_investigator(
                conn,
                feed_run_id="l2-run",
                groups=[make_group()],
                provider=provider,
                tools={},
                prompt_version="layer2-scoring-investigator-v2",
            )

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
            prompt_version="layer2-scoring-investigator-v2",
        )

        payload = provider.calls[0]["input_payload"]
        self.assertEqual(payload["task"]["mode"], "score_from_context")
        self.assertTrue(payload["task"]["must_finalize"])
        self.assertEqual(payload["available_tools"], [])

    def test_no_active_tools_sets_must_finalize_on_the_first_turn(self):
        from pipeline.decision.layer2_scoring_investigator import score_with_investigator

        provider = FakeLLMProvider([attributable_final()])
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_decision_db(conn)

        score_with_investigator(
            conn,
            feed_run_id="l2-run",
            groups=[make_group()],
            provider=provider,
            tools={},
            direct_final_enabled=False,
            prompt_version="layer2-scoring-investigator-v2",
        )

        task = provider.calls[0]["input_payload"]["task"]
        self.assertEqual(task["mode"], "investigate")
        self.assertTrue(task["must_finalize"])

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
            prompt_version="layer2-scoring-investigator-v2",
        )[0]

        call = provider.calls[0]
        payload = call["input_payload"]
        self.assertEqual(DEFAULT_INVESTIGATOR_PROMPT_VERSION, "layer2-scoring-investigator-v2")
        self.assertEqual(call["prompt_version"], "layer2-scoring-investigator-v2")
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
            prompt_version="layer2-scoring-investigator-v2",
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

    def test_candidate_boundary_rejects_tool_arguments_for_another_repository(self):
        from pipeline.decision.layer2_scoring_investigator import score_with_investigator
        from pipeline.decision.layer2_tool_registry import ToolSpec

        calls: list[dict] = []
        spec = ToolSpec(
            name="fetch_repo",
            version="1",
            description="Fetch the verified candidate repository.",
            input_schema={
                "type": "object",
                "properties": {"repo_key": {"type": "string"}},
                "required": ["repo_key"],
                "additionalProperties": False,
            },
            family="github",
            cost="remote_cached_medium",
            executor=lambda arguments: calls.append(arguments) or {"status": "ok"},
            availability=lambda candidate: bool(candidate.repo_key),
            timeout_seconds=10,
            max_result_tokens=500,
            cache_policy="repo",
            concurrency_key="github",
            max_in_flight=2,
            starts_per_second=1.0,
            result_projector=lambda result, observation_id, arguments: {
                "observation_id": observation_id,
                "tool": "fetch_repo",
                "status": result.get("status", "error"),
                "trust": "external_untrusted",
                "provenance": {"repo_key": arguments.get("repo_key")},
                "facts": {},
                "excerpt": "",
                "truncated": False,
                "relevant_axes": ["confidence"],
            },
            argument_authorizer=lambda candidate, arguments: (
                arguments.get("repo_key") == candidate.repo_key
            ),
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
                        "question": "Fetch repository evidence.",
                        "target_axes": ["technical_substance"],
                        "expected_decision_impact": "Could change technical substance.",
                    },
                    "tool_requests": [
                        {
                            "name": "fetch_repo",
                            "arguments": {"repo_key": "attacker/project"},
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
            tools={"fetch_repo": spec.execute},
            tool_specs={"fetch_repo": spec},
            prompt_version="layer2-scoring-investigator-v2",
        )[0]

        self.assertEqual(calls, [])
        self.assertEqual(
            result["tool_trace"][0]["status"], "candidate_boundary_rejected"
        )

    def test_large_result_is_projected_before_raw_trace_truncation(self):
        from pipeline.decision.layer2_scoring_investigator import score_with_investigator
        from pipeline.decision.layer2_tool_registry import ToolSpec

        excerpt = "workflow " * 1_000
        spec = ToolSpec(
            name="fetch_large_docs",
            version="1",
            description="Fetch large first-party documentation.",
            input_schema={
                "type": "object",
                "properties": {"repo_key": {"type": "string"}},
                "required": ["repo_key"],
                "additionalProperties": False,
            },
            family="github",
            cost="remote_cached_medium",
            executor=lambda _arguments: {"status": "ok", "excerpt": excerpt},
            availability=lambda candidate: bool(candidate.repo_key),
            timeout_seconds=10,
            max_result_tokens=2_000,
            cache_policy="repo",
            concurrency_key="github",
            max_in_flight=2,
            starts_per_second=1.0,
            result_projector=lambda result, observation_id, arguments: {
                "observation_id": observation_id,
                "tool": "fetch_large_docs",
                "status": result.get("status", "error"),
                "trust": "external_untrusted",
                "provenance": {"repo_key": arguments.get("repo_key")},
                "facts": {"projected_chars": len(result.get("excerpt") or "")},
                "excerpt": str(result.get("excerpt") or "")[:500],
                "truncated": len(str(result.get("excerpt") or "")) > 500,
                "relevant_axes": ["technical_substance"],
            },
            argument_authorizer=lambda candidate, arguments: (
                arguments.get("repo_key") == candidate.repo_key
            ),
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
                        "question": "Read the large documentation.",
                        "target_axes": ["technical_substance"],
                        "expected_decision_impact": "Could verify implementation depth.",
                    },
                    "tool_requests": [
                        {
                            "name": "fetch_large_docs",
                            "arguments": {"repo_key": "owner/repo"},
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
            tools={"fetch_large_docs": spec.execute},
            tool_specs={"fetch_large_docs": spec},
            prompt_version="layer2-scoring-investigator-v2",
        )[0]

        self.assertGreater(result["observations"][0]["facts"]["projected_chars"], 5_000)
        self.assertTrue(result["tool_trace"][0]["result"]["truncated"])
        persisted_raw = json.loads(
            conn.execute(
                "select raw_tool_results_json from l2_scoring_investigations"
            ).fetchone()[0]
        )
        self.assertEqual(persisted_raw[0]["result"]["excerpt"], excerpt)


if __name__ == "__main__":
    unittest.main()
