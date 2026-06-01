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


if __name__ == "__main__":
    unittest.main()
