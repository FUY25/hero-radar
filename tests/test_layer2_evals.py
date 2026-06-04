from __future__ import annotations

import unittest
from unittest import mock


class Layer2EvalTest(unittest.TestCase):
    def test_eval_fixture_scores_project_above_news(self) -> None:
        from pipeline.decision.run_layer2_evals import rank_eval_cases

        cases = [
            {"name": "Generic AI funding news", "l2_score": 42, "expected": "news"},
            {"name": "Repo-native agent workflow", "l2_score": 84, "expected": "project"},
        ]

        result = rank_eval_cases(cases)

        self.assertEqual(result["top"]["expected"], "project")
        self.assertTrue(result["ok"])

    def test_default_eval_cases_cover_scout_scoring_and_deepdive(self) -> None:
        from pipeline.decision.run_layer2_evals import default_eval_cases, rank_eval_cases

        cases = default_eval_cases()
        stages = {case["stage"] for case in cases}
        result = rank_eval_cases(cases)

        self.assertTrue({"scout", "scoring", "deepdive"}.issubset(stages))
        self.assertTrue(result["ok"])
        self.assertEqual(result["top"]["expected"], "project")
        self.assertGreaterEqual(result["metrics"]["project_cases"], 2)

    def test_default_eval_cases_cover_wide_scout_gate(self) -> None:
        from pipeline.decision.run_layer2_evals import (
            default_wide_scout_eval_cases,
            evaluate_wide_scout_cases,
        )

        result = evaluate_wide_scout_cases(default_wide_scout_eval_cases())

        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["metrics"]["positive_cases"], 3)
        self.assertGreaterEqual(result["metrics"]["negative_cases"], 4)
        self.assertEqual(result["mismatches"], [])

    def test_openclaw_eval_context_emphasizes_validation_evidence(self) -> None:
        from pipeline.decision.run_layer2_evals import default_wide_scout_eval_cases

        openclaw = next(
            case for case in default_wide_scout_eval_cases() if case["name"] == "OpenClaw"
        )
        context = openclaw["candidate"]["one_liner"]

        self.assertIn("validation evidence", context)
        self.assertIn("release evidence", context)

    def test_scout_prompt_is_wide_triage_not_scorer(self) -> None:
        from pipeline.decision.layer2_scout import SCOUT_SYSTEM_PROMPT

        self.assertIn("fast wide triage gate", SCOUT_SYSTEM_PROMPT)
        self.assertIn("Return only the candidates", SCOUT_SYSTEM_PROMPT)

    def test_run_handshake_uses_provider_handshake_without_completion(self) -> None:
        from pipeline.decision.run_layer2_evals import run_handshake

        class Provider:
            def handshake(self):
                return {
                    "ok": True,
                    "base_url_host": "api.moonshot.cn",
                    "key_configured": True,
                    "models_count": 9,
                }

        result = run_handshake(provider=Provider())

        self.assertTrue(result["ok"])
        self.assertEqual(result["models_count"], 9)

    def test_run_smoke_uses_provider_configuration_instead_of_env_gate(self) -> None:
        from pipeline.decision.run_layer2_evals import run_smoke

        class Provider:
            provider_name = "kimi"
            model = "kimi-k2.5"
            api_key = "configured"

            def complete_json(self, **kwargs):
                return {"ok": True, "score": 88}

        with mock.patch.dict("os.environ", {}, clear=True):
            result = run_smoke(provider=Provider())

        self.assertFalse(result["skipped"])
        self.assertEqual(result["shape"], ["ok", "score"])

    def test_run_wide_scout_kimi_eval_uses_provider_and_compares_expected(self) -> None:
        from pipeline.decision.run_layer2_evals import run_wide_scout_kimi_eval

        class Provider:
            provider_name = "kimi"
            model = "kimi-k2.5"
            api_key = "configured"

            def __init__(self) -> None:
                self.calls = []

            def complete_json(self, **kwargs):
                self.calls.append(kwargs)
                return {
                    "promotions": [
                        {
                            "group_id": "group:pass",
                            "reason_code": "possible_workflow_shift",
                            "reason": "New interaction model.",
                        }
                    ]
                }

        provider = Provider()
        cases = [
            {
                "name": "Pass",
                "expected_include": True,
                "candidate": {"group_id": "group:pass", "name": "Pass"},
            },
            {
                "name": "Fail",
                "expected_include": False,
                "candidate": {"group_id": "group:fail", "name": "Fail"},
            },
        ]

        result = run_wide_scout_kimi_eval(provider=provider, cases=cases, batch_size=2)

        self.assertTrue(result["ok"])
        self.assertFalse(result["skipped"])
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(provider.calls[0]["task"], "layer2_wide_scout_eval")
        self.assertEqual(
            provider.calls[0]["input_payload"]["decision_rule"],
            "return only candidates that may be worth a later scoring call",
        )
        self.assertEqual(len(provider.calls[0]["input_payload"]["candidates"]), 2)

    def test_run_wide_scout_kimi_eval_skips_without_key(self) -> None:
        from pipeline.decision.run_layer2_evals import run_wide_scout_kimi_eval

        class Provider:
            api_key = ""

        result = run_wide_scout_kimi_eval(provider=Provider(), cases=[])

        self.assertFalse(result["ok"])
        self.assertTrue(result["skipped"])

    def test_run_wide_scout_kimi_eval_defaults_to_30_case_batches(self) -> None:
        from pipeline.decision.run_layer2_evals import run_wide_scout_kimi_eval

        class Provider:
            provider_name = "kimi"
            model = "kimi-k2.5"
            api_key = "configured"

            def __init__(self) -> None:
                self.calls = []

            def complete_json(self, **kwargs):
                self.calls.append(kwargs)
                return {"promotions": []}

        provider = Provider()
        cases = [
            {
                "name": "One",
                "expected_include": False,
                "candidate": {"group_id": "group:one", "name": "One"},
            },
            {
                "name": "Two",
                "expected_include": False,
                "candidate": {"group_id": "group:two", "name": "Two"},
            },
        ]

        result = run_wide_scout_kimi_eval(provider=provider, cases=cases)

        self.assertTrue(result["ok"])
        self.assertEqual(len(provider.calls), 1)

    def test_run_wide_scout_kimi_eval_reports_provider_error(self) -> None:
        from pipeline.decision.run_layer2_evals import run_wide_scout_kimi_eval

        class Provider:
            provider_name = "kimi"
            model = "kimi-k2.5"
            api_key = "configured"

            def complete_json(self, **kwargs):
                raise TimeoutError("read timed out")

        result = run_wide_scout_kimi_eval(
            provider=Provider(),
            cases=[
                {
                    "name": "One",
                    "expected_include": True,
                    "candidate": {
                        "group_id": "group:one",
                        "name": "One",
                    },
                }
            ],
        )

        self.assertFalse(result["ok"])
        self.assertFalse(result["skipped"])
        self.assertEqual(result["error"], "TimeoutError")

    def test_default_eval_cases_cover_scoring_investigator_alignment(self) -> None:
        from pipeline.decision.run_layer2_evals import (
            default_scoring_investigator_eval_cases,
            evaluate_scoring_investigator_cases,
        )

        result = evaluate_scoring_investigator_cases(
            default_scoring_investigator_eval_cases()
        )
        names_by_expectation = {
            row["name"]: row["expected_band"] for row in result["cases"]
        }

        self.assertTrue(result["ok"])
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(names_by_expectation["OpenClaw"], "high")
        self.assertEqual(names_by_expectation["Hermes Agent"], "high")
        self.assertEqual(names_by_expectation["HeyClicky"], "high")
        self.assertEqual(names_by_expectation["Generic AI chatbot"], "low")
        self.assertEqual(names_by_expectation["Funding acquisition news"], "low")
        self.assertEqual(names_by_expectation["Standalone model release"], "low")
        self.assertEqual(names_by_expectation["Tutorial resource list"], "low")
        self.assertGreaterEqual(result["metrics"]["high_expected"], 3)
        self.assertGreaterEqual(result["metrics"]["low_expected"], 5)

    def test_openclaw_scoring_eval_context_has_alias_and_cross_source_evidence(
        self,
    ) -> None:
        import json

        from pipeline.decision.run_layer2_evals import (
            default_scoring_investigator_eval_cases,
        )

        openclaw = next(
            case
            for case in default_scoring_investigator_eval_cases()
            if case["name"] == "OpenClaw"
        )
        context = json.dumps(openclaw["candidate"], sort_keys=True)

        self.assertIn("clawdbot", context)
        self.assertIn("redirect", context)
        self.assertIn("Product Hunt", context)
        self.assertIn("npm", context)
        self.assertIn("HN", context)

    def test_scoring_smoke_context_has_required_positive_evidence(self) -> None:
        import json

        from pipeline.decision.run_layer2_evals import (
            default_scoring_investigator_eval_cases,
        )

        cases = {
            case["name"]: json.dumps(case["candidate"], sort_keys=True)
            for case in default_scoring_investigator_eval_cases()
        }

        hermes = cases["Hermes Agent"].lower()
        self.assertIn("persistent memory", hermes)
        self.assertIn("skill creation", hermes)
        self.assertIn("self-improving workspace", hermes)
        self.assertIn("curator", hermes)
        self.assertIn("workflow evidence", hermes)

        heyclicky = cases["HeyClicky"].lower()
        self.assertIn("cursor-adjacent", heyclicky)
        self.assertIn("screen-aware", heyclicky)
        self.assertIn("voice", heyclicky)
        self.assertIn("desktop", heyclicky)
        self.assertIn("workflow evidence", heyclicky)

        gray_zone = cases["Screen-aware spreadsheet operator"].lower()
        self.assertIn("explicit workflow unlock", gray_zone)
        self.assertIn("selected cells", gray_zone)
        self.assertIn("multi-step cleanup", gray_zone)
        self.assertIn("user confirmation", gray_zone)

    def test_gray_zone_utility_needs_explicit_workflow_unlock(self) -> None:
        from pipeline.decision.run_layer2_evals import (
            default_scoring_investigator_eval_cases,
            evaluate_scoring_investigator_cases,
        )

        result = evaluate_scoring_investigator_cases(
            default_scoring_investigator_eval_cases()
        )
        scores = {row["name"]: row["l2_score"] for row in result["cases"]}

        self.assertLess(scores["Ordinary dashboard utility"], 60)
        self.assertGreaterEqual(scores["Screen-aware spreadsheet operator"], 60)
        self.assertGreater(
            scores["Screen-aware spreadsheet operator"],
            scores["Ordinary dashboard utility"] + 15,
        )

    def test_run_scoring_investigator_kimi_eval_uses_provider(self) -> None:
        from pipeline.decision.run_layer2_evals import run_scoring_investigator_kimi_eval

        class Provider:
            provider_name = "kimi"
            model = "kimi-k2.5"
            api_key = "configured"

            def __init__(self) -> None:
                self.calls = []

            def complete_json(self, **kwargs):
                self.calls.append(kwargs)
                name = kwargs["input_payload"]["candidate"]["name"]
                if name == "OpenClaw":
                    axes = {
                        "workflow_shift": 88,
                        "technical_substance": 88,
                        "product_market_fit": 84,
                        "momentum": 72,
                        "confidence": 84,
                        "risk_penalty": 4,
                        "derivative_news_penalty": 0,
                    }
                    should_print = True
                else:
                    axes = {
                        "workflow_shift": 32,
                        "technical_substance": 28,
                        "product_market_fit": 42,
                        "momentum": 35,
                        "confidence": 76,
                        "risk_penalty": 2,
                        "derivative_news_penalty": 8,
                    }
                    should_print = False
                return {
                    "action": "final",
                    "score": {
                        "object_type": "repo" if should_print else "product",
                        "is_product_or_repo": True,
                        "axes": axes,
                        "supporting_evidence": ["Eval evidence"],
                        "negative_evidence": [],
                        "known_gaps": [],
                        "primary_reason": "Eval",
                        "rationale_short": "Eval rationale",
                        "topic_tags": ["eval"],
                        "caveats": [],
                        "should_print": should_print,
                    },
                }

        cases = [
            {
                "name": "OpenClaw",
                "expected_band": "high",
                "candidate": {"name": "OpenClaw", "context": "Local agent repo"},
            },
            {
                "name": "Generic AI chatbot",
                "expected_band": "low",
                "candidate": {
                    "name": "Generic AI chatbot",
                    "context": "Ordinary chatbot wrapper",
                },
            },
        ]
        provider = Provider()

        result = run_scoring_investigator_kimi_eval(
            provider=provider, cases=cases, limit=2
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result["skipped"])
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(provider.calls[0]["task"], "layer2_scoring_investigator_eval")
        self.assertIn(
            "Layer 2 Scoring Investigator", provider.calls[0]["system_prompt"]
        )
        self.assertNotIn("expected_band_for_eval", provider.calls[0]["input_payload"])
        self.assertIn(
            "Use 0-100 numeric axis values",
            provider.calls[0]["input_payload"]["instruction"],
        )
        self.assertIn(
            "risk_penalty above 8",
            provider.calls[0]["input_payload"]["instruction"],
        )
        self.assertIn(
            "derivative_news_penalty only",
            provider.calls[0]["input_payload"]["instruction"],
        )

    def test_run_scoring_investigator_kimi_eval_skips_without_key(self) -> None:
        from pipeline.decision.run_layer2_evals import run_scoring_investigator_kimi_eval

        class Provider:
            api_key = ""

        result = run_scoring_investigator_kimi_eval(provider=Provider(), cases=[])

        self.assertFalse(result["ok"])
        self.assertTrue(result["skipped"])

    def test_run_scoring_investigator_kimi_eval_defaults_to_small_high_low_smoke(
        self,
    ) -> None:
        from pipeline.decision.run_layer2_evals import run_scoring_investigator_kimi_eval

        class Provider:
            provider_name = "kimi"
            model = "kimi-k2.5"
            api_key = "configured"

            def __init__(self) -> None:
                self.names = []

            def complete_json(self, **kwargs):
                name = kwargs["input_payload"]["candidate"]["name"]
                self.names.append(name)
                is_high = name in {"OpenClaw", "Hermes Agent", "HeyClicky"}
                is_medium = name == "Screen-aware spreadsheet operator"
                return {
                    "action": "final",
                    "score": {
                        "object_type": (
                            "repo"
                            if name in {"OpenClaw", "Hermes Agent"}
                            else "product"
                            if name != "Standalone model release"
                            else "model_release"
                        ),
                        "is_product_or_repo": name != "Standalone model release",
                        "axes": {
                            "workflow_shift": 88 if is_high else 84 if is_medium else 30,
                            "technical_substance": 86 if is_high else 65 if is_medium else 25,
                            "product_market_fit": 82 if is_high else 78 if is_medium else 40,
                            "momentum": 72 if is_high else 60 if is_medium else 35,
                            "confidence": 82 if is_high else 80 if is_medium else 74,
                            "risk_penalty": 4 if is_high else 5 if is_medium else 2,
                            "derivative_news_penalty": 0 if is_high or is_medium else 8,
                        },
                        "supporting_evidence": ["Eval evidence"],
                        "negative_evidence": [],
                        "known_gaps": [],
                        "primary_reason": "Eval",
                        "rationale_short": "Eval rationale",
                        "topic_tags": ["eval"],
                        "caveats": [],
                        "should_print": is_high or is_medium,
                    },
                }

        provider = Provider()

        result = run_scoring_investigator_kimi_eval(provider=provider)

        self.assertTrue(result["ok"])
        self.assertEqual(
            provider.names,
            [
                "OpenClaw",
                "Hermes Agent",
                "HeyClicky",
                "Generic AI chatbot",
                "Standalone model release",
                "Screen-aware spreadsheet operator",
            ],
        )


if __name__ == "__main__":
    unittest.main()
