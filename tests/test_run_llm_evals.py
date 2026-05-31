from __future__ import annotations

import unittest

from pipeline.decision.llm_provider import FakeLLMProvider


class RunLlmEvalsTest(unittest.TestCase):
    def test_fake_hn_eval_runner_compares_expected_projectness_and_noise(self) -> None:
        from pipeline.decision.llm_evals import hn_eval_cases
        from pipeline.decision.run_llm_evals import run_hn_eval_cases, summarize_results

        provider = FakeLLMProvider(
            [case["fake_provider_response"] for case in hn_eval_cases()]
        )

        results = run_hn_eval_cases(provider, hn_eval_cases(), limit=3)
        summary = summarize_results(results)

        self.assertEqual(summary, {"total": 3, "passed": 3, "failed": 0})
        self.assertEqual([result["case"] for result in results], [case["name"] for case in hn_eval_cases()])

    def test_x_eval_runner_applies_acceptance_safety_to_stage2_outputs(self) -> None:
        from pipeline.decision.llm_evals import x_eval_cases
        from pipeline.decision.run_llm_evals import run_x_eval_cases, summarize_results

        provider = FakeLLMProvider([case["fake_stage2_response"] for case in x_eval_cases()])

        results = run_x_eval_cases(provider, x_eval_cases(), limit=3)
        summary = summarize_results(results)

        self.assertEqual(summary, {"total": 3, "passed": 3, "failed": 0})
        self.assertEqual(results[1]["actual"]["accepted_x_tier"], "none")
        self.assertEqual(results[2]["actual"]["accepted_x_tier"], "none")

    def test_eval_summary_records_failures_without_prompt_or_secret_values(self) -> None:
        from pipeline.decision.llm_evals import hn_eval_cases
        from pipeline.decision.run_llm_evals import run_hn_eval_cases, summarize_results

        bad_response = {
            **hn_eval_cases()[0]["fake_provider_response"],
            "projectness": "news_article",
            "summary": "secret-value",
        }
        provider = FakeLLMProvider([bad_response])

        results = run_hn_eval_cases(provider, hn_eval_cases(), limit=1)
        summary = summarize_results(results)

        self.assertEqual(summary, {"total": 1, "passed": 0, "failed": 1})
        self.assertNotIn("secret-value", repr(results))


if __name__ == "__main__":
    unittest.main()
