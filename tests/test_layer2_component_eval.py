from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


DATASET = Path("evals/layer2/datasets/scoring_cases.v1.jsonl")


class Layer2ComponentEvalTest(unittest.TestCase):
    def test_versioned_dataset_has_twenty_strict_cases_and_keeps_gold_out_of_model_input(self):
        from pipeline.decision.layer2_eval import load_eval_dataset

        dataset = load_eval_dataset(DATASET)

        self.assertEqual(dataset.version, "layer2-scoring-cases-v1")
        self.assertEqual(len(dataset.cases), 20)
        self.assertEqual(len({case.case_id for case in dataset.cases}), 20)
        for case in dataset.cases:
            serialized_input = json.dumps(case.model_input, sort_keys=True)
            self.assertNotIn("expected_", serialized_input)
            self.assertNotIn("gold", serialized_input.lower())
            self.assertNotIn("response", case.model_input)
            self.assertNotIn("simulated_tool_result", serialized_input)

    def test_dataset_rejects_unknown_contract_fields(self):
        from pipeline.decision.layer2_eval import DatasetContractError, load_eval_dataset

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "contract_version": "layer2-eval-case-v1",
                        "dataset_version": "layer2-scoring-cases-v1",
                        "case_id": "bad",
                        "model_input": {},
                        "replay": {"recording_version": "v1", "tools": []},
                        "gold": {},
                        "grader": {},
                        "human_review": {},
                        "surprise": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(DatasetContractError):
                load_eval_dataset(path)

    def test_dataset_rejects_malformed_nested_gold_before_execution(self):
        from pipeline.decision.layer2_eval import DatasetContractError, load_eval_dataset

        row = json.loads(DATASET.read_text(encoding="utf-8").splitlines()[0])
        row["gold"]["allowed_tool_names"] = "fetch_github_readme"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad-nested.jsonl"
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")

            with self.assertRaises(DatasetContractError):
                load_eval_dataset(path)

    def test_replay_tools_return_recorded_failures_and_never_fall_through_to_network(self):
        from pipeline.decision.layer2_eval import ReplayToolRegistry, load_eval_dataset

        dataset = load_eval_dataset(DATASET)
        missing_manifest = next(
            case for case in dataset.cases if case.case_id == "missing-manifest-returns-404"
        )
        replay = ReplayToolRegistry(missing_manifest)

        result = replay.tools["fetch_github_file"](
            {"repo_key": "example/no-manifest-agent", "path": "package.json"}
        )
        unexpected = replay.tools["fetch_github_file"](
            {"repo_key": "example/no-manifest-agent", "path": "README.md"}
        )

        self.assertEqual(result["http_status"], 404)
        self.assertEqual(unexpected["status"], "unavailable")
        self.assertEqual(unexpected["error"], "recording_not_found")
        self.assertEqual(
            sorted(replay.tools),
            [
                "fetch_github_file",
                "fetch_github_readme",
                "fetch_homepage_or_docs",
                "read_evidence_rows",
                "web_search",
            ],
        )

    def test_component_case_exercises_production_scorer_without_label_leakage(self):
        from pipeline.decision.layer2_eval import load_eval_dataset, run_eval_case

        class BehavioralProvider:
            provider_name = "offline-behavior"
            model = "recorded-model"
            actual_temperature = 0
            max_output_tokens = 1600
            response_format = {"type": "json_object"}

            def __init__(self):
                self.calls = []

            def complete_json(self, **call):
                self.calls.append(call)
                if len(self.calls) == 1:
                    return {
                        "action": "use_tools",
                        "information_sufficiency": {
                            "identity": "strong",
                            "workflow_shift": "weak",
                            "technical_substance": "weak",
                            "product_market_fit": "medium",
                            "momentum": "medium",
                        },
                        "information_need": {
                            "question": "What workflow does the README document?",
                            "target_axes": ["workflow_shift", "technical_substance"],
                            "expected_decision_impact": "It can establish the implementation wedge.",
                        },
                        "tool_requests": [
                            {
                                "name": "fetch_github_readme",
                                "arguments": {"repo_key": "example/workflow-engine"},
                            }
                        ],
                    }
                return self.final_response()

            @staticmethod
            def final_response():
                return {
                    "action": "final",
                    "information_sufficiency": {
                        "identity": "strong",
                        "workflow_shift": "strong",
                        "technical_substance": "strong",
                        "product_market_fit": "strong",
                        "momentum": "medium",
                    },
                    "score": {
                        "object_type": "repo",
                        "is_product_or_repo": True,
                        "axes": {
                            "workflow_shift": 85,
                            "technical_substance": 82,
                            "product_market_fit": 78,
                            "momentum": 45,
                            "confidence": 78,
                            "risk_penalty": 4,
                            "derivative_news_penalty": 0,
                        },
                        "supporting_evidence": [
                            {
                                "claim": "The README documents a bounded programmable workflow with approvals and replay.",
                                "evidence_refs": ["tool:t1:0"],
                                "supports_axes": ["workflow_shift", "technical_substance"],
                                "claim_type": "observed",
                            }
                        ],
                        "negative_evidence": [],
                        "known_gaps": [],
                        "primary_reason": "Programmable workflow engine",
                        "rationale_short": "The recorded README establishes a concrete workflow.",
                        "topic_tags": ["agent workflow"],
                        "caveats": [],
                        "should_print": True,
                    },
                }

        dataset = load_eval_dataset(DATASET)
        case = next(
            case for case in dataset.cases if case.case_id == "readme-gated-workflow-engine"
        )
        provider = BehavioralProvider()

        artifact = run_eval_case(
            case,
            provider=provider,
            prompt_version="layer2-scoring-investigator-v2",
            trial=1,
            include_brief=False,
        )

        request = provider.calls[0]["input_payload"]
        serialized_request = json.dumps(request, ensure_ascii=False, sort_keys=True)
        self.assertEqual(request["task"]["decision"], "score_candidate")
        self.assertEqual(artifact["preflight_mode"], "investigate")
        self.assertEqual(artifact["tool_trace"][0]["tool"], "fetch_github_readme")
        self.assertTrue(artifact["request_fingerprints"])
        self.assertTrue(artifact["grades"]["score"]["passed"])
        self.assertTrue(artifact["grades"]["preflight"]["passed"])
        self.assertTrue(artifact["grades"]["tool_trajectory"]["passed"])
        self.assertTrue(artifact["grades"]["evidence_references"]["passed"])
        self.assertTrue(artifact["grades"]["claim_grounding"]["passed"])
        self.assertIsNone(artifact["telemetry"]["input_tokens"])
        self.assertEqual(artifact["telemetry"]["latency_ms"], 0)
        self.assertNotIn("score_interval", serialized_request)
        self.assertNotIn("expected_tool_outcome", serialized_request)
        self.assertNotIn("score_band", serialized_request)
        self.assertNotIn('"gold"', serialized_request.lower())

    def test_brief_required_case_exercises_production_compact_brief_seam(self):
        from pipeline.decision.layer2_eval import load_eval_dataset, run_eval_case

        class ScorerAndBriefProvider:
            provider_name = "offline-behavior"
            model = "recorded-model"
            actual_temperature = 0
            max_output_tokens = 1600
            response_format = {"type": "json_object"}

            def __init__(self):
                self.calls = []

            def complete_json(self, **call):
                self.calls.append(call)
                if call["task"] == "layer2_scoring_investigator_brief":
                    return {
                        "category": {"primary": "AI 助手", "tags": ["本地代理"]},
                        "headline": "把可执行 AI 助手带到本地工作流",
                        "core_highlights": ["连接系统、浏览器、记忆与可复用技能"],
                        "use_cases": ["个人用户自动化跨应用任务"],
                        "caveat": "长期采用仍需观察",
                    }
                payload = call["input_payload"]
                evidence_ref = payload["candidate"]["top_evidence"][0]["evidence_id"]
                return {
                    "action": "final",
                    "information_sufficiency": {
                        "identity": "strong",
                        "workflow_shift": "strong",
                        "technical_substance": "strong",
                        "product_market_fit": "strong",
                        "momentum": "strong",
                    },
                    "score": {
                        "object_type": "repo",
                        "is_product_or_repo": True,
                        "axes": {
                            "workflow_shift": 88,
                            "technical_substance": 88,
                            "product_market_fit": 84,
                            "momentum": 72,
                            "confidence": 84,
                            "risk_penalty": 4,
                            "derivative_news_penalty": 0,
                        },
                        "supporting_evidence": [
                            {
                                "claim": "Recorded evidence identifies a local assistant with system and browser workflows.",
                                "evidence_refs": [evidence_ref],
                                "supports_axes": ["workflow_shift", "technical_substance"],
                                "claim_type": "observed",
                            }
                        ],
                        "negative_evidence": [],
                        "known_gaps": ["Long-term adoption remains uncertain."],
                        "primary_reason": "Local executable assistant",
                        "rationale_short": "The supplied evidence supports a concrete local workflow.",
                        "topic_tags": ["local agent"],
                        "caveats": ["Long-term adoption remains uncertain."],
                        "should_print": True,
                    },
                }

        case = load_eval_dataset(DATASET).cases[0]
        provider = ScorerAndBriefProvider()

        artifact = run_eval_case(
            case,
            provider=provider,
            prompt_version="layer2-scoring-investigator-v2",
            trial=1,
        )

        brief = artifact["brief"]
        serialized_input = json.dumps(brief["input"], ensure_ascii=False, sort_keys=True)
        self.assertEqual(brief["input"]["candidate"]["identity"]["group_id"], case.case_id)
        self.assertEqual(brief["output"]["headline"], "把可执行 AI 助手带到本地工作流")
        self.assertNotIn("tool_trace", serialized_input)
        self.assertNotIn("investigation_trace", serialized_input)
        self.assertNotIn("score_interval", serialized_input)
        self.assertIn(
            "layer2_scoring_investigator_brief",
            [row["task"] for row in artifact["model_calls"]],
        )

    def test_paired_runner_uses_three_isolated_trials_and_writes_inspectable_artifacts(self):
        from pipeline.decision.layer2_eval import (
            Layer2EvalDataset,
            PairedEvalConfig,
            load_eval_dataset,
            run_paired_evaluation,
        )

        created_providers = []

        class LowScoreProvider:
            provider_name = "offline-behavior"
            model = "recorded-model"
            actual_temperature = 0
            max_output_tokens = 1600
            response_format = {"type": "json_object"}
            eval_cache_mode = "disabled"

            def complete_json(self, **_call):
                return {
                    "action": "final",
                    "information_sufficiency": {
                        "identity": "strong",
                        "workflow_shift": "weak",
                        "technical_substance": "weak",
                        "product_market_fit": "medium",
                        "momentum": "weak",
                    },
                    "score": {
                        "object_type": "product",
                        "is_product_or_repo": True,
                        "axes": {
                            "workflow_shift": 32,
                            "technical_substance": 28,
                            "product_market_fit": 42,
                            "momentum": 35,
                            "confidence": 76,
                            "risk_penalty": 2,
                            "derivative_news_penalty": 8,
                        },
                        "supporting_evidence": [],
                        "negative_evidence": [],
                        "known_gaps": [],
                        "primary_reason": "Generic wrapper",
                        "rationale_short": "No distinctive workflow is documented.",
                        "topic_tags": [],
                        "caveats": [],
                        "should_print": False,
                    },
                }

        def provider_factory(_version, _trial, _case):
            provider = LowScoreProvider()
            created_providers.append(provider)
            return provider

        generic = load_eval_dataset(DATASET).cases[3]
        dataset = Layer2EvalDataset(
            version="layer2-scoring-cases-v1", cases=(generic,)
        )
        with tempfile.TemporaryDirectory() as directory:
            output = run_paired_evaluation(
                dataset,
                provider_factory=provider_factory,
                output_dir=Path(directory),
                config=PairedEvalConfig(trials=3, include_briefs=False),
            )

            results = [json.loads(line) for line in output.results_jsonl.read_text().splitlines()]
            report = output.report_markdown.read_text(encoding="utf-8")
            self.assertEqual(len(results), 6)
            self.assertEqual(len({id(provider) for provider in created_providers}), 6)
            self.assertEqual({row["trial"] for row in results}, {1, 2, 3})
            self.assertEqual(
                {row["case_input_fingerprint"] for row in results},
                {results[0]["case_input_fingerprint"]},
            )
            self.assertEqual(
                {row["cache_isolation"]["provider_cache"] for row in results},
                {"disabled"},
            )
            self.assertEqual(len({row["run_id"] for row in results}), 1)
            self.assertIn("v1 / v2 case and trial comparison", report)
            self.assertIn("missing", report)
            self.assertIn("route v1 / v2", report)
            aggregate = json.loads(output.aggregate_json.read_text())
            self.assertIn("by_case", aggregate)
            self.assertIn("by_grader", aggregate)
            self.assertIn("by_tool_family", aggregate)
            self.assertIn("by_failure_type", aggregate)
            self.assertTrue(output.aggregate_json.exists())
            self.assertTrue(output.run_metadata_json.exists())

        class UndeclaredCacheProvider(LowScoreProvider):
            eval_cache_mode = ""

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "eval_cache_mode"):
                run_paired_evaluation(
                    dataset,
                    provider_factory=lambda *_args: UndeclaredCacheProvider(),
                    output_dir=Path(directory),
                    config=PairedEvalConfig(trials=3, include_briefs=False),
                )

    def test_cli_has_distinct_validation_and_missing_provider_exit_codes(self):
        from pipeline.decision.run_layer2_component_eval import (
            EXIT_CONFIG,
            EXIT_OK,
            main,
        )

        self.assertEqual(main(["--dataset", str(DATASET), "--validate-only"]), EXIT_OK)
        self.assertEqual(main(["--dataset", str(DATASET)]), EXIT_CONFIG)

    def test_all_twenty_externalized_cases_execute_through_production_scorer(self):
        from pipeline.decision.layer2_eval import load_eval_dataset, run_eval_case

        class ConservativeProvider:
            provider_name = "offline-behavior"
            model = "conservative-recorded-model"
            actual_temperature = 0
            max_output_tokens = 1600
            response_format = {"type": "json_object"}

            def complete_json(self, **_call):
                return {
                    "action": "final",
                    "information_sufficiency": {
                        "identity": "medium",
                        "workflow_shift": "weak",
                        "technical_substance": "weak",
                        "product_market_fit": "weak",
                        "momentum": "weak",
                    },
                    "score": {
                        "object_type": "unknown",
                        "is_product_or_repo": False,
                        "axes": {
                            "workflow_shift": 20,
                            "technical_substance": 20,
                            "product_market_fit": 20,
                            "momentum": 20,
                            "confidence": 40,
                            "risk_penalty": 0,
                            "derivative_news_penalty": 0,
                        },
                        "supporting_evidence": [],
                        "negative_evidence": [],
                        "known_gaps": ["The offline behavior provider made no tool request."],
                        "primary_reason": "Insufficient evidence",
                        "rationale_short": "The available evidence does not support a stronger score.",
                        "topic_tags": [],
                        "caveats": [],
                        "should_print": False,
                    },
                }

        dataset = load_eval_dataset(DATASET)
        artifacts = [
            run_eval_case(
                case,
                provider=ConservativeProvider(),
                prompt_version="layer2-scoring-investigator-v2",
                trial=1,
                include_brief=False,
            )
            for case in dataset.cases
        ]

        self.assertEqual(len(artifacts), 20)
        self.assertEqual({row["case_id"] for row in artifacts}, {case.case_id for case in dataset.cases})
        self.assertTrue(all(row["context_manifests"] for row in artifacts))
        self.assertTrue(
            all(row["request_fingerprints"] for row in artifacts if row["preflight_mode"] != "cannot_score")
        )

    def test_brief_grader_requires_caveat_when_compact_decision_has_known_gaps(self):
        from pipeline.decision.layer2_eval import grade_eval_artifact, load_eval_dataset

        case = load_eval_dataset(DATASET).cases[0]
        grades = grade_eval_artifact(
            case,
            {
                "score": 80,
                "preflight_mode": "score_from_context",
                "route": "score_only",
                "tool_trace": [],
                "result": {"supporting_claims": [], "negative_claims": [], "known_gaps": []},
                "context_manifests": [],
                "observations": [],
                "trace": [{"action": "final"}],
                "turns": 1,
                "repair_count": 0,
                "final_output_valid": True,
                "telemetry": {
                    "input_tokens": None,
                    "output_tokens": None,
                    "cached_input_tokens": None,
                    "total_tokens": None,
                    "latency_ms": 0,
                    "cost": {"amount": None, "currency": "USD", "source": "missing"},
                },
                "brief": {
                    "input": {"decision": {"known_gaps": ["Adoption durability"]}},
                    "output": {
                        "category": {"primary": "AI 助手", "tags": []},
                        "headline": "本地工作流助手",
                        "core_highlights": ["连接多个工具"],
                        "use_cases": ["自动化跨应用任务"],
                    },
                },
            },
        )

        self.assertFalse(grades["brief"]["passed"])
        self.assertFalse(grades["brief"]["checks"]["caveat"])

    def test_agent_behavior_exercises_timeout_unavailable_error_and_host_budget_failures(self):
        from pipeline.decision.layer2_eval import load_eval_dataset, run_eval_case
        from pipeline.decision.layer2_scoring_investigator import InvestigatorLimits

        class FailureSeekingProvider:
            provider_name = "offline-behavior"
            model = "failure-recorded-model"
            actual_temperature = 0
            max_output_tokens = 1600
            response_format = {"type": "json_object"}

            def __init__(self, tool, arguments):
                self.tool = tool
                self.arguments = arguments
                self.calls = 0

            def complete_json(self, **_call):
                self.calls += 1
                if self.calls == 1:
                    return {
                        "action": "use_tools",
                        "information_sufficiency": self.sufficiency(),
                        "information_need": {
                            "question": "Can the missing evidence be retrieved?",
                            "target_axes": ["confidence"],
                            "expected_decision_impact": "Failure must remain a known gap.",
                        },
                        "tool_requests": [{"name": self.tool, "arguments": self.arguments}],
                    }
                return {
                    "action": "final",
                    "information_sufficiency": self.sufficiency(),
                    "score": {
                        "object_type": "unknown",
                        "is_product_or_repo": False,
                        "axes": {
                            "workflow_shift": 20,
                            "technical_substance": 20,
                            "product_market_fit": 20,
                            "momentum": 20,
                            "confidence": 35,
                            "risk_penalty": 0,
                            "derivative_news_penalty": 0,
                        },
                        "supporting_evidence": [],
                        "negative_evidence": [],
                        "known_gaps": ["The requested evidence could not be retrieved."],
                        "primary_reason": "Evidence unavailable",
                        "rationale_short": "The failure is an information gap, not negative evidence.",
                        "topic_tags": [],
                        "caveats": ["Evidence retrieval failed."],
                        "should_print": False,
                    },
                }

            @staticmethod
            def sufficiency():
                return {
                    "identity": "medium",
                    "workflow_shift": "weak",
                    "technical_substance": "weak",
                    "product_market_fit": "weak",
                    "momentum": "weak",
                }

        cases = {case.case_id: case for case in load_eval_dataset(DATASET).cases}
        scenarios = [
            ("readme-gated-workflow-engine", "timeout"),
            ("manifest-gated-mcp-runner", "unavailable"),
            ("independent-adoption-evidence-needed", "error"),
        ]
        for trial, (case_id, expected_error) in enumerate(scenarios, start=1):
            with self.subTest(error=expected_error):
                artifact = run_eval_case(
                    cases[case_id],
                    provider=FailureSeekingProvider(
                        "web_search", {"query": f"__fixture_{expected_error}__"}
                    ),
                    prompt_version="layer2-scoring-investigator-v2",
                    trial=trial,
                    include_brief=False,
                )
                self.assertEqual(artifact["tool_trace"][0]["result"]["error"], expected_error)
                self.assertTrue(artifact["result"]["known_gaps"])
                self.assertEqual(artifact["trace"][-1]["action"], "final")

        budget_artifact = run_eval_case(
            cases["readme-gated-workflow-engine"],
            provider=FailureSeekingProvider(
                "fetch_github_readme", {"repo_key": "example/workflow-engine"}
            ),
            prompt_version="layer2-scoring-investigator-v2",
            trial=4,
            include_brief=False,
            limits=InvestigatorLimits(max_tool_calls_per_candidate=0),
        )
        self.assertEqual(budget_artifact["tool_trace"][0]["status"], "budget_exceeded")
        self.assertTrue(budget_artifact["result"]["known_gaps"])

    def test_tool_grader_rejects_production_repeated_signature_status(self):
        from pipeline.decision.layer2_eval import grade_eval_artifact, load_eval_dataset

        case = next(
            case
            for case in load_eval_dataset(DATASET).cases
            if case.case_id == "readme-gated-workflow-engine"
        )
        grades = grade_eval_artifact(
            case,
            {
                "score": 80,
                "preflight_mode": "investigate",
                "route": "score_only",
                "tool_trace": [
                    {
                        "tool": "fetch_github_readme",
                        "eval_family": "github",
                        "status": "repeated_signature",
                        "result": {},
                    }
                ],
                "result": {"supporting_claims": [], "negative_claims": [], "known_gaps": []},
                "context_manifests": [],
                "observations": [],
                "trace": [{"action": "final"}],
                "turns": 1,
                "repair_count": 0,
                "final_output_valid": True,
                "telemetry": {
                    "input_tokens": None,
                    "output_tokens": None,
                    "cached_input_tokens": None,
                    "total_tokens": None,
                    "latency_ms": 0,
                    "cost": {"amount": None, "currency": "USD", "source": "missing"},
                },
                "brief": None,
            },
        )

        self.assertFalse(grades["tool_trajectory"]["passed"])
        self.assertEqual(
            grades["tool_trajectory"]["status_counts"]["repeated_signature"], 1
        )

    def test_grounding_grader_does_not_silently_pass_when_cited_text_is_missing(self):
        from pipeline.decision.layer2_eval import grade_eval_artifact, load_eval_dataset

        case = load_eval_dataset(DATASET).cases[3]
        grades = grade_eval_artifact(
            case,
            {
                "score": 30,
                "preflight_mode": "score_from_context",
                "route": "suppress_or_low",
                "tool_trace": [],
                "result": {
                    "supporting_claims": [
                        {
                            "claim": "The product changes a workflow.",
                            "evidence_refs": ["evidence:missing-text"],
                        }
                    ],
                    "negative_claims": [],
                    "known_gaps": [],
                },
                "context_manifests": [
                    {"included_evidence_ids": ["evidence:missing-text"]}
                ],
                "evidence_catalog": {},
                "observations": [],
                "trace": [{"action": "final"}],
                "turns": 1,
                "repair_count": 0,
                "final_output_valid": True,
                "telemetry": {
                    "input_tokens": None,
                    "output_tokens": None,
                    "cached_input_tokens": None,
                    "total_tokens": None,
                    "latency_ms": 0,
                    "cost": {"amount": None, "currency": "USD", "source": "missing"},
                },
                "brief": None,
            },
        )

        self.assertTrue(grades["evidence_references"]["passed"])
        self.assertFalse(grades["claim_grounding"]["passed"])
        self.assertIn("missing cited evidence text", grades["claim_grounding"]["lexical_failures"][0])


if __name__ == "__main__":
    unittest.main()
