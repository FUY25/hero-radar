from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


DATASET = Path("evals/layer2/datasets/scoring_cases.v1.jsonl")


def _valid_low_final_response():
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


class Layer2ComponentEvalTest(unittest.TestCase):
    def test_run_summary_ignores_zero_call_placeholder_currency(self):
        from pipeline.decision.layer2_eval_reporting import _run_telemetry_summary

        summary = _run_telemetry_summary(
            [
                {
                    "telemetry": {
                        "logical_call_count": 1,
                        "usage_complete": True,
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "total_tokens": 15,
                        "latency_ms": 10,
                        "cost": {
                            "amount": 0.01,
                            "known_partial_amount": 0.01,
                            "currency": "CNY",
                            "complete": True,
                        },
                    }
                },
                {
                    "telemetry": {
                        "logical_call_count": 0,
                        "usage_complete": True,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                        "latency_ms": 0,
                        "cost": {
                            "amount": 0.0,
                            "known_partial_amount": 0.0,
                            "currency": "USD",
                            "complete": True,
                        },
                    }
                },
            ]
        )

        self.assertEqual(summary["cost_currency"], "CNY")
        self.assertEqual(summary["cost"], 0.01)

    def test_v2_eval_config_matches_the_production_prompt_and_direct_final_profile(self):
        from pipeline.decision.layer2_eval import V2EvalConfig

        config = V2EvalConfig()

        self.assertEqual(
            config.prompt_version,
            "layer2-scoring-investigator-v2",
        )
        self.assertFalse(config.direct_final_enabled)
        self.assertEqual(config.trials, 3)

    def test_versioned_dataset_has_twenty_strict_cases_and_keeps_gold_out_of_model_input(self):
        from pipeline.decision.layer2_eval import load_eval_dataset

        dataset = load_eval_dataset(DATASET)

        self.assertEqual(dataset.version, "layer2-scoring-cases-v1")
        self.assertEqual(len(dataset.cases), 20)
        self.assertEqual(len({case.case_id for case in dataset.cases}), 20)
        self.assertEqual(
            sum(case.gold["preflight_mode"] == "investigate" for case in dataset.cases),
            19,
        )
        self.assertEqual(
            sum(case.gold["preflight_mode"] == "cannot_score" for case in dataset.cases),
            1,
        )
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

    def test_web_search_replay_accepts_candidate_bound_query_without_exact_string_match(self):
        from pipeline.decision.layer2_eval import ReplayToolRegistry, load_eval_dataset

        case = next(
            case
            for case in load_eval_dataset(DATASET).cases
            if case.case_id == "independent-adoption-evidence-needed"
        )
        replay = ReplayToolRegistry(case)

        result = replay.tools["web_search"](
            {"query": "independent adoption evidence for approval queue agent workflows"}
        )
        unrelated = replay.tools["web_search"](
            {"query": "weather forecast and sports scores"}
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["results"][0]["title"], "Independent workflow review")
        self.assertEqual(unrelated["status"], "unavailable")
        self.assertEqual(unrelated["error"], "recording_not_found")

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
        self.assertGreaterEqual(artifact["telemetry"]["latency_ms"], 0)
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
        self.assertEqual(artifact["preflight_mode"], "investigate")
        self.assertFalse(provider.calls[0]["input_payload"]["task"]["must_finalize"])

    def test_v2_runner_uses_three_isolated_trials_and_writes_inspectable_artifacts(self):
        from pipeline.decision.layer2_eval import (
            Layer2EvalDataset,
            V2EvalConfig,
            load_eval_dataset,
            run_v2_evaluation,
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

        def provider_factory(_trial, _case):
            provider = LowScoreProvider()
            created_providers.append(provider)
            return provider

        generic = load_eval_dataset(DATASET).cases[3]
        dataset = Layer2EvalDataset(
            version="layer2-scoring-cases-v1", cases=(generic,)
        )
        with tempfile.TemporaryDirectory() as directory:
            output = run_v2_evaluation(
                dataset,
                provider_factory=provider_factory,
                output_dir=Path(directory),
                config=V2EvalConfig(trials=3, include_briefs=False),
            )

            results = [json.loads(line) for line in output.results_jsonl.read_text().splitlines()]
            report = output.report_markdown.read_text(encoding="utf-8")
            self.assertEqual(len(results), 3)
            self.assertEqual(len({id(provider) for provider in created_providers}), 3)
            self.assertEqual({row["trial"] for row in results}, {1, 2, 3})
            self.assertEqual(
                {row["prompt_version"] for row in results},
                {"layer2-scoring-investigator-v2"},
            )
            self.assertEqual(
                {row["case_input_fingerprint"] for row in results},
                {results[0]["case_input_fingerprint"]},
            )
            self.assertEqual(
                {row["cache_isolation"]["provider_cache"] for row in results},
                {"disabled"},
            )
            self.assertEqual(len({row["run_id"] for row in results}), 1)
            self.assertIn("V2 case and trial results", report)
            self.assertNotIn("v1 / v2", report.lower())
            self.assertIn("missing", report)
            self.assertIn("Route", report)
            aggregate = json.loads(output.aggregate_json.read_text())
            metadata = json.loads(output.run_metadata_json.read_text())
            self.assertIn("by_case", aggregate)
            self.assertIn("by_grader", aggregate)
            self.assertIn("by_tool_family", aggregate)
            self.assertIn("by_failure_type", aggregate)
            self.assertEqual(metadata["provider_execution"], "test_provider")
            self.assertEqual(metadata["run_scope"], "test")
            self.assertFalse(metadata["release_eligible"])
            self.assertTrue(output.aggregate_json.exists())
            self.assertTrue(output.run_metadata_json.exists())

        class UndeclaredCacheProvider(LowScoreProvider):
            eval_cache_mode = ""

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "eval_cache_mode"):
                run_v2_evaluation(
                    dataset,
                    provider_factory=lambda *_args: UndeclaredCacheProvider(),
                    output_dir=Path(directory),
                    config=V2EvalConfig(trials=3, include_briefs=False),
                )

    def test_v2_runner_persists_case_failure_and_continues_remaining_cases(self):
        from pipeline.decision.layer2_eval import (
            Layer2EvalDataset,
            V2EvalConfig,
            load_eval_dataset,
            run_v2_evaluation,
        )

        class FailingProvider:
            provider_name = "offline-behavior"
            model = "recorded-model"
            actual_temperature = 0
            max_output_tokens = 3000
            response_format = {"type": "json_object"}
            eval_cache_mode = "disabled"
            collect_usage_on_error = True

            def complete_json(self, **_call):
                self.last_usage = {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                }
                self.last_cost = {
                    "amount": 0.001,
                    "currency": "USD",
                    "source": "configured_rate_estimate",
                }
                raise RuntimeError("simulated invalid final output")

        class ValidLowProvider(FailingProvider):
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

        cases = load_eval_dataset(DATASET).cases[3:5]
        dataset = Layer2EvalDataset(
            version="layer2-scoring-cases-v1",
            cases=tuple(cases),
        )

        def provider_factory(_trial, case):
            return FailingProvider() if case.case_id == cases[0].case_id else ValidLowProvider()

        with tempfile.TemporaryDirectory() as directory:
            output = run_v2_evaluation(
                dataset,
                provider_factory=provider_factory,
                output_dir=Path(directory),
                config=V2EvalConfig(trials=1, include_briefs=False),
            )
            results = [
                json.loads(line)
                for line in output.results_jsonl.read_text(encoding="utf-8").splitlines()
            ]
            aggregate = json.loads(output.aggregate_json.read_text(encoding="utf-8"))

        self.assertEqual(len(results), 2)
        failed, completed = results
        self.assertFalse(failed["final_output_valid"])
        self.assertFalse(failed["passed"])
        self.assertEqual(failed["error"]["stage"], "scoring")
        self.assertEqual(failed["error"]["type"], "RuntimeError")
        self.assertEqual(failed["telemetry"]["total_tokens"], 120)
        self.assertEqual(failed["telemetry"]["cost"]["amount"], 0.001)
        self.assertTrue(completed["final_output_valid"])
        self.assertIsNone(completed["error"])
        self.assertFalse(aggregate["all_passed"])
        self.assertEqual(aggregate["execution_failures"], 1)

    def test_v2_runner_checkpoints_before_interrupt_and_resumes_missing_slots(self):
        from pipeline.decision.layer2_eval import (
            Layer2EvalDataset,
            V2EvalConfig,
            load_eval_dataset,
            run_v2_evaluation,
        )

        class Provider:
            provider_name = "offline-behavior"
            model = "recorded-model"
            actual_temperature = 0
            max_output_tokens = 3000
            response_format = {"type": "json_object"}
            eval_cache_mode = "disabled"

            def complete_json(self, **_call):
                return _valid_low_final_response()

        case = load_eval_dataset(DATASET).cases[3]
        dataset = Layer2EvalDataset(
            version="layer2-scoring-cases-v1",
            cases=(case,),
        )
        starts = 0

        def interrupted_factory(_trial, _case):
            nonlocal starts
            starts += 1
            if starts == 2:
                raise KeyboardInterrupt("simulated operator interrupt")
            return Provider()

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            with self.assertRaises(KeyboardInterrupt):
                run_v2_evaluation(
                    dataset,
                    provider_factory=interrupted_factory,
                    output_dir=output_dir,
                    config=V2EvalConfig(trials=2, include_briefs=False),
                )
            checkpoint = [
                json.loads(line)
                for line in (output_dir / "results.v1.jsonl").read_text().splitlines()
            ]
            running_metadata = json.loads(
                (output_dir / "run-metadata.v1.json").read_text()
            )
            self.assertEqual(len(checkpoint), 1)
            self.assertEqual(running_metadata["status"], "running")
            self.assertEqual(running_metadata["completed_slots"], 1)

            resumed_calls = []
            output = run_v2_evaluation(
                dataset,
                provider_factory=lambda trial, _case: (
                    resumed_calls.append(trial) or Provider()
                ),
                output_dir=output_dir,
                config=V2EvalConfig(trials=2, include_briefs=False),
                resume=True,
            )
            results = [
                json.loads(line)
                for line in output.results_jsonl.read_text().splitlines()
            ]
            completed_metadata = json.loads(output.run_metadata_json.read_text())

        self.assertEqual(resumed_calls, [2])
        self.assertEqual(len(results), 2)
        self.assertEqual(completed_metadata["status"], "complete")
        self.assertEqual(completed_metadata["completed_slots"], 2)

    def test_v2_retry_keeps_append_only_attempt_spend_and_latest_slot_result(self):
        from pipeline.decision.layer2_eval import (
            Layer2EvalDataset,
            V2EvalConfig,
            load_eval_dataset,
            run_v2_evaluation,
        )

        class Provider:
            provider_name = "offline-behavior"
            model = "recorded-model"
            actual_temperature = 0
            max_output_tokens = 3000
            response_format = {"type": "json_object"}
            eval_cache_mode = "disabled"
            collect_usage_on_error = True

            def __init__(self, fail):
                self.fail = fail
                self.last_usage = None
                self.last_cost = None
                self.thinking_type = "enabled" if fail else "disabled"
                self.request_options = {
                    "thinking": {"type": self.thinking_type}
                }

            def complete_json(self, **_call):
                self.last_usage = {
                    "prompt_tokens": 100 if self.fail else 10,
                    "completion_tokens": 20 if self.fail else 2,
                    "total_tokens": 120 if self.fail else 12,
                }
                self.last_cost = {
                    "amount": 0.01 if self.fail else 0.001,
                    "currency": "USD",
                    "source": "configured_rate_estimate",
                }
                if self.fail:
                    raise RuntimeError("retryable slot failure")
                return _valid_low_final_response()

        case = load_eval_dataset(DATASET).cases[3]
        dataset = Layer2EvalDataset("layer2-scoring-cases-v1", (case,))
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            run_v2_evaluation(
                dataset,
                provider_factory=lambda *_args: Provider(True),
                output_dir=output_dir,
                config=V2EvalConfig(trials=1, include_briefs=False),
            )
            with self.assertRaisesRegex(ValueError, "settings must be identical"):
                run_v2_evaluation(
                    dataset,
                    provider_factory=lambda *_args: Provider(False),
                    output_dir=output_dir,
                    config=V2EvalConfig(trials=1, include_briefs=False),
                    resume=True,
                    retry_execution_errors=True,
                )
            output = run_v2_evaluation(
                dataset,
                provider_factory=lambda *_args: Provider(False),
                output_dir=output_dir,
                config=V2EvalConfig(trials=1, include_briefs=False),
                resume=True,
                retry_execution_errors=True,
                allow_provider_profile_change=True,
            )
            attempts = [
                json.loads(line)
                for line in output.attempts_jsonl.read_text().splitlines()
            ]
            results = [
                json.loads(line)
                for line in output.results_jsonl.read_text().splitlines()
            ]
            aggregate = json.loads(output.aggregate_json.read_text())
            metadata = json.loads(output.run_metadata_json.read_text())

        self.assertEqual([row["execution_attempt"] for row in attempts], [1, 2])
        self.assertEqual([row["execution_status"] for row in attempts], ["error", "ok"])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["execution_attempt"], 2)
        self.assertEqual(aggregate["summary"]["attempt_count"], 2)
        self.assertEqual(aggregate["summary"]["historical_execution_failures"], 1)
        self.assertEqual(aggregate["summary"]["total_tokens"], 132)
        self.assertEqual(aggregate["summary"]["cost"], 0.011)
        self.assertEqual(len(metadata["provider_profile_revisions"]), 2)

    def test_resume_fails_closed_on_corrupt_latest_slot_artifact(self):
        from pipeline.decision.layer2_eval import (
            Layer2EvalDataset,
            V2EvalConfig,
            load_eval_dataset,
            run_v2_evaluation,
        )

        class Provider:
            provider_name = "offline-behavior"
            model = "recorded-model"
            actual_temperature = 0
            max_output_tokens = 3000
            response_format = {"type": "json_object"}
            eval_cache_mode = "disabled"

            def complete_json(self, **_call):
                return _valid_low_final_response()

        case = load_eval_dataset(DATASET).cases[3]
        dataset = Layer2EvalDataset("layer2-scoring-cases-v1", (case,))
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            output = run_v2_evaluation(
                dataset,
                provider_factory=lambda *_args: Provider(),
                output_dir=output_dir,
                config=V2EvalConfig(trials=1, include_briefs=False),
            )
            row = json.loads(output.results_jsonl.read_text())
            row["prompt_version"] = "corrupted-prompt"
            output.results_jsonl.write_text(json.dumps(row) + "\n")

            with self.assertRaisesRegex(ValueError, "mismatched prompt_version"):
                run_v2_evaluation(
                    dataset,
                    provider_factory=lambda *_args: Provider(),
                    output_dir=output_dir,
                    config=V2EvalConfig(trials=1, include_briefs=False),
                    resume=True,
                )

    def test_cannot_score_case_records_measured_zero_model_calls(self):
        from pipeline.decision.layer2_eval import load_eval_dataset, run_eval_case

        class NeverCalledProvider:
            provider_name = "offline-behavior"
            model = "recorded-model"

            def complete_json(self, **_call):
                raise AssertionError("cannot_score must not call the provider")

        case = next(
            case
            for case in load_eval_dataset(DATASET).cases
            if case.case_id == "unresolved-project-atlas"
        )
        artifact = run_eval_case(
            case,
            provider=NeverCalledProvider(),
            prompt_version="layer2-scoring-investigator-v2",
            trial=1,
            include_brief=False,
        )

        self.assertEqual(artifact["preflight_mode"], "cannot_score")
        self.assertEqual(artifact["telemetry"]["logical_call_count"], 0)
        self.assertEqual(artifact["telemetry"]["total_tokens"], 0)
        self.assertEqual(artifact["telemetry"]["cost"]["amount"], 0.0)
        self.assertEqual(
            artifact["telemetry"]["cost"]["source"],
            "measured_no_model_calls",
        )

    def test_blind_brief_packet_uses_only_opaque_id_and_content(self):
        from types import SimpleNamespace

        from pipeline.decision.layer2_eval_reporting import write_blind_briefs

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = SimpleNamespace(
                blind_briefs_jsonl=root / "briefs.jsonl",
                blind_mapping_json=root / "mapping.json",
            )
            write_blind_briefs(
                paths,
                [
                    {
                        "run_id": "run-1",
                        "case_id": "secret-case",
                        "trial": 2,
                        "prompt_version": "layer2-scoring-investigator-v2",
                        "request_fingerprints": ["fingerprint"],
                        "brief": {"output_valid": True, "output": {"headline": "中文标题"}},
                    }
                ],
            )
            packet = json.loads(paths.blind_briefs_jsonl.read_text())
            mapping = json.loads(paths.blind_mapping_json.read_text())

        self.assertEqual(set(packet), {"blind_id", "brief"})
        self.assertTrue(packet["blind_id"].startswith("brief-"))
        self.assertNotIn("secret-case", packet["blind_id"])
        self.assertEqual(mapping[packet["blind_id"]]["case_id"], "secret-case")

    def test_cli_has_distinct_validation_and_missing_provider_exit_codes(self):
        from pipeline.decision.run_layer2_component_eval import (
            EXIT_CONFIG,
            EXIT_OK,
            main,
        )

        self.assertEqual(main(["--dataset", str(DATASET), "--validate-only"]), EXIT_OK)
        self.assertEqual(main(["--dataset", str(DATASET)]), EXIT_CONFIG)

    def test_cli_rejects_non_positive_or_non_finite_live_numeric_settings(self):
        from pipeline.decision.run_layer2_component_eval import EXIT_CONFIG, main

        self.assertEqual(
            main(["--dataset", str(DATASET), "--starts-per-second", "0", "--validate-only"]),
            EXIT_CONFIG,
        )
        self.assertEqual(
            main(
                [
                    "--dataset",
                    str(DATASET),
                    "--input-cost-per-million",
                    "nan",
                    "--output-cost-per-million",
                    "1",
                    "--validate-only",
                ]
            ),
            EXIT_CONFIG,
        )

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
