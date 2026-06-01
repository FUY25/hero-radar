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


if __name__ == "__main__":
    unittest.main()
