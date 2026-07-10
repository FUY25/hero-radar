from __future__ import annotations

import unittest


class Layer2CandidatePreflightTest(unittest.TestCase):
    def test_rich_attributable_first_party_context_can_direct_finalize(self) -> None:
        from pipeline.decision.layer2_candidate_preflight import preflight_candidate

        result = preflight_candidate(
            {
                "group_id": "group:workflow-kit",
                "canonical_entity_id": "entity:workflow-kit",
                "canonical_name": "acme/workflow-kit",
                "canonical_key": "github:acme/workflow-kit",
                "canonical_link": "https://github.com/acme/workflow-kit",
                "source_families": ["github", "hn"],
                "context": {
                    "members": [
                        {
                            "entity_id": "entity:workflow-kit",
                            "binding_confidence": "verified",
                            "readme_excerpt_available": True,
                            "context_preview": (
                                "Workflow Kit provides a reusable CLI and Python SDK for "
                                "validating agent plans, running typed checks, and publishing "
                                "the resulting reports in CI. The README documents installation, "
                                "configuration, commands, and the package architecture."
                            ),
                        }
                    ],
                    "evidence_rows": [
                        {
                            "id": 41,
                            "source": "github_api",
                            "family": "github",
                            "metric_name": "stars",
                            "metric_value": 730,
                            "raw_url_or_ref": "https://github.com/acme/workflow-kit",
                        },
                        {
                            "id": 42,
                            "source": "github_backfill",
                            "family": "github",
                            "metric_name": "release_count",
                            "metric_value": 12,
                            "raw_url_or_ref": "https://github.com/acme/workflow-kit/releases",
                        },
                    ],
                },
            },
            context_manifest={
                "included_evidence_ids": ["evidence:41", "evidence:42"],
                "retrievable_evidence_ids": [],
                "excluded_evidence_ids": [],
            },
            direct_final_enabled=True,
        )

        self.assertEqual(result.mode, "score_from_context")
        self.assertTrue(result.must_finalize)
        self.assertEqual(
            result.information_sufficiency,
            {
                "identity": "strong",
                "workflow_shift": "strong",
                "technical_substance": "strong",
                "product_market_fit": "medium",
                "momentum": "medium",
            },
        )
        self.assertEqual(result.open_questions, ())
        self.assertEqual(result.tool_candidate_context.repo_key, "acme/workflow-kit")
        self.assertFalse(result.tool_candidate_context.needs_technical_evidence)

    def test_direct_final_is_gated_off_by_default(self) -> None:
        from pipeline.decision.layer2_candidate_preflight import preflight_candidate

        result = preflight_candidate(
            {
                "canonical_name": "acme/workflow-kit",
                "canonical_key": "github:acme/workflow-kit",
                "canonical_link": "https://github.com/acme/workflow-kit",
                "readme_context": "documented reusable workflow and architecture " * 12,
                "evidence_rows": [
                    {"id": 1, "source": "github_api", "family": "github"},
                    {"id": 2, "source": "github_backfill", "family": "github"},
                ],
            }
        )

        self.assertEqual(result.mode, "investigate")
        self.assertFalse(result.must_finalize)
        self.assertIn("disabled", result.reason.lower())

    def test_unresolved_name_routes_to_investigation_with_web_search_flags(self) -> None:
        from pipeline.decision.layer2_candidate_preflight import preflight_candidate

        result = preflight_candidate(
            {
                "canonical_entity_id": "entity:mystery-agent",
                "canonical_name": "Mystery Agent",
                "source_families": ["hn"],
                "evidence_rows": [
                    {
                        "id": 7,
                        "source": "hn_search",
                        "family": "hn",
                        "note": "Show HN: Mystery Agent",
                    }
                ],
            }
        )

        self.assertEqual(result.mode, "investigate")
        self.assertEqual(result.information_sufficiency["identity"], "medium")
        self.assertTrue(result.tool_candidate_context.unresolved_identity)
        self.assertTrue(result.tool_candidate_context.missing_first_party_material)
        self.assertIsNone(result.tool_candidate_context.repo_key)
        self.assertTrue(any("identity" in question.lower() for question in result.open_questions))

    def test_homepage_only_candidate_requests_product_description_not_repo_tools(self) -> None:
        from pipeline.decision.layer2_candidate_preflight import preflight_candidate

        result = preflight_candidate(
            {
                "canonical_entity_id": "entity:site",
                "canonical_name": "Useful Site",
                "canonical_key": "domain:useful.example",
                "canonical_link": "https://useful.example/docs",
                "source_families": ["product_hunt"],
            },
            direct_final_enabled=True,
        )

        tools = result.tool_candidate_context
        self.assertEqual(result.mode, "investigate")
        self.assertFalse(result.must_finalize)
        self.assertIsNone(tools.repo_key)
        self.assertEqual(tools.canonical_url, "https://useful.example/docs")
        self.assertTrue(tools.needs_product_description)
        self.assertFalse(tools.needs_technical_evidence)
        self.assertFalse(tools.unresolved_identity)
        self.assertFalse(tools.missing_first_party_material)
        self.assertEqual(result.information_sufficiency["momentum"], "medium")
        self.assertTrue(
            any("product description" in question.lower() for question in result.open_questions)
        )

    def test_omitted_evidence_prevents_direct_final_and_enables_retrieval(self) -> None:
        from pipeline.decision.layer2_candidate_preflight import preflight_candidate

        result = preflight_candidate(
            {
                "canonical_name": "acme/workflow-kit",
                "canonical_key": "github:acme/workflow-kit",
                "canonical_link": "https://github.com/acme/workflow-kit",
                "readme_context": "documented reusable workflow and architecture " * 12,
                "evidence_rows": [
                    {"id": 1, "source": "github_api", "family": "github"},
                    {"id": 2, "source": "github_backfill", "family": "github"},
                ],
            },
            context_manifest={
                "included_evidence_ids": ["evidence:1", "evidence:2"],
                "retrievable_evidence_ids": ["evidence:3"],
                "excluded_evidence_ids": [],
            },
            direct_final_enabled=True,
        )

        self.assertEqual(result.mode, "investigate")
        self.assertFalse(result.must_finalize)
        self.assertTrue(result.tool_candidate_context.has_retrievable_evidence)
        self.assertTrue(any("omitted" in question.lower() for question in result.open_questions))

    def test_no_identity_and_no_evidence_is_a_deterministic_cannot_score(self) -> None:
        from pipeline.decision.layer2_candidate_preflight import preflight_candidate

        first = preflight_candidate({"context": {}})
        second = preflight_candidate({"context": {}})

        self.assertEqual(first, second)
        self.assertEqual(first.mode, "cannot_score")
        self.assertTrue(first.must_finalize)
        self.assertEqual(
            first.information_sufficiency,
            {
                "identity": "weak",
                "workflow_shift": "weak",
                "technical_substance": "weak",
                "product_market_fit": "weak",
                "momentum": "weak",
            },
        )
        self.assertEqual(
            first.reason,
            "Candidate has neither an identifiable identity nor attributable evidence.",
        )
        self.assertTrue(first.tool_candidate_context.unresolved_identity)

    def test_resolved_repo_without_readme_requests_technical_workflow_evidence(self) -> None:
        from pipeline.decision.layer2_candidate_preflight import preflight_candidate

        result = preflight_candidate(
            {
                "canonical_entity_id": "entity:thin-repo",
                "canonical_name": "acme/thin-repo",
                "canonical_key": "github:acme/thin-repo",
                "canonical_link": "https://github.com/acme/thin-repo",
                "source_families": ["github"],
                "evidence_rows": [
                    {"id": 9, "source": "github_api", "family": "github"}
                ],
            },
            direct_final_enabled=True,
        )

        self.assertEqual(result.mode, "investigate")
        self.assertFalse(result.must_finalize)
        self.assertEqual(result.information_sufficiency["identity"], "strong")
        self.assertEqual(result.information_sufficiency["workflow_shift"], "weak")
        self.assertEqual(result.information_sufficiency["technical_substance"], "medium")
        self.assertTrue(result.tool_candidate_context.needs_technical_evidence)
        self.assertTrue(
            any("workflow" in question.lower() for question in result.open_questions)
        )

    def test_requested_independent_momentum_check_blocks_direct_final(self) -> None:
        from pipeline.decision.layer2_candidate_preflight import preflight_candidate

        result = preflight_candidate(
            {
                "canonical_name": "acme/workflow-kit",
                "canonical_key": "github:acme/workflow-kit",
                "canonical_link": "https://github.com/acme/workflow-kit",
                "readme_context": "documented reusable workflow and architecture " * 12,
                "evidence_rows": [
                    {"id": 1, "source": "github_api", "family": "github"},
                    {"id": 2, "source": "github_backfill", "family": "github"},
                ],
                "needs_momentum_verification": True,
            },
            direct_final_enabled=True,
        )

        self.assertEqual(result.mode, "investigate")
        self.assertTrue(result.tool_candidate_context.needs_momentum_verification)
        self.assertTrue(
            any("momentum" in question.lower() for question in result.open_questions)
        )

    def test_accepts_candidate_group_as_the_pipeline_domain_input(self) -> None:
        from pipeline.decision.layer2_candidate_preflight import preflight_candidate
        from pipeline.decision.layer2_models import CandidateGroup

        group = CandidateGroup(
            group_id="group:repo",
            canonical_entity_id="entity:repo",
            canonical_name="acme/repo",
            canonical_key="github:acme/repo",
            canonical_link="https://github.com/acme/repo",
            member_entity_ids=["entity:repo", "entity:alias"],
            level="potential",
            source_families=["github"],
            context={"evidence_rows": []},
        )

        result = preflight_candidate(group)

        self.assertEqual(result.mode, "investigate")
        self.assertEqual(
            result.tool_candidate_context.entity_ids,
            ("entity:repo", "entity:alias"),
        )
        self.assertEqual(result.tool_candidate_context.repo_key, "acme/repo")

    def test_weak_github_source_link_is_not_treated_as_a_resolved_repository(self) -> None:
        from pipeline.decision.layer2_candidate_preflight import preflight_candidate

        result = preflight_candidate(
            {
                "canonical_entity_id": "entity:mention",
                "canonical_name": "Repo Mention",
                "canonical_link": "https://github.com/acme/unverified",
                "context": {
                    "members": [
                        {
                            "entity_id": "entity:mention",
                            "binding_confidence": "weak",
                            "readme_excerpt_available": True,
                            "context_preview": "reusable workflow and architecture " * 12,
                        }
                    ],
                    "evidence_rows": [
                        {"id": 1, "source": "hn_search", "family": "hn"},
                        {"id": 2, "source": "x_tweets", "family": "x_social"},
                    ],
                },
            },
            direct_final_enabled=True,
        )

        self.assertEqual(result.mode, "investigate")
        self.assertIsNone(result.tool_candidate_context.repo_key)
        self.assertTrue(result.tool_candidate_context.unresolved_identity)

    def test_excluded_nonretrievable_evidence_does_not_enable_retrieval_tool(self) -> None:
        from pipeline.decision.layer2_candidate_preflight import preflight_candidate

        result = preflight_candidate(
            {
                "canonical_name": "acme/repo",
                "canonical_key": "github:acme/repo",
                "canonical_link": "https://github.com/acme/repo",
            },
            context_manifest={
                "included_evidence_ids": [],
                "retrievable_evidence_ids": [],
                "excluded_evidence_ids": ["evidence:unsafe"],
            },
        )

        self.assertFalse(result.tool_candidate_context.has_retrievable_evidence)

    def test_existing_product_description_hides_redundant_homepage_fetch(self) -> None:
        from pipeline.decision.layer2_candidate_preflight import preflight_candidate

        result = preflight_candidate(
            {
                "canonical_name": "Useful Site",
                "canonical_key": "domain:useful.example",
                "canonical_link": "https://useful.example/docs",
                "summary": (
                    "Useful Site is a hosted agent validation workspace that lets teams "
                    "define typed checks, run them in CI, and publish attributable reports."
                ),
                "source_families": ["product_hunt"],
            }
        )

        self.assertFalse(result.tool_candidate_context.needs_product_description)
        self.assertEqual(result.information_sufficiency["workflow_shift"], "medium")

    def test_canonical_repository_key_is_itself_a_resolved_identity(self) -> None:
        from pipeline.decision.layer2_candidate_preflight import preflight_candidate

        result = preflight_candidate(
            {
                "canonical_key": "github:acme/repo",
                "canonical_link": "https://github.com/acme/repo",
            }
        )

        self.assertEqual(result.information_sufficiency["identity"], "strong")
        self.assertFalse(result.tool_candidate_context.unresolved_identity)


if __name__ == "__main__":
    unittest.main()
