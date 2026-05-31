from __future__ import annotations

import unittest


class LlmClassifierEvalTest(unittest.TestCase):
    def test_hn_eval_cases_cover_project_and_noise(self) -> None:
        from pipeline.decision.llm_evals import hn_eval_cases, validate_eval_coverage

        cases = hn_eval_cases()
        labels = {case["expected"]["projectness"] for case in cases}

        self.assertIn("project", labels)
        self.assertIn("news_article", labels)
        self.assertIn("topic_discussion", labels)
        validate_eval_coverage(
            cases,
            required_names={
                "hn_project_with_github",
                "hn_news_noise",
                "hn_topic_noise",
            },
        )

    def test_x_eval_cases_cover_linked_potential_and_fuzzy_noise(self) -> None:
        from pipeline.decision.llm_evals import validate_eval_coverage, x_eval_cases

        cases = x_eval_cases()
        names = {case["name"] for case in cases}

        self.assertIn("x_linked_two_credible_potential", names)
        self.assertIn("x_fuzzy_no_citations_not_potential", names)
        self.assertIn("x_generic_known_term_none", names)
        validate_eval_coverage(cases, required_names=names)

    def test_eval_cases_encode_safety_expectations(self) -> None:
        from pipeline.decision.llm_evals import hn_eval_cases, x_eval_cases

        hn_by_name = {case["name"]: case for case in hn_eval_cases()}
        x_by_name = {case["name"]: case for case in x_eval_cases()}

        self.assertFalse(hn_by_name["hn_project_with_github"]["expected"]["noise"])
        self.assertTrue(hn_by_name["hn_news_noise"]["expected"]["noise"])
        self.assertTrue(hn_by_name["hn_topic_noise"]["expected"]["noise"])
        self.assertEqual(
            x_by_name["x_linked_two_credible_potential"]["expected"]["x_tier"],
            "potential",
        )
        self.assertEqual(
            x_by_name["x_fuzzy_no_citations_not_potential"]["expected"]["max_tier"],
            "watch",
        )
        self.assertEqual(
            x_by_name["x_generic_known_term_none"]["expected"]["x_tier"],
            "none",
        )

    def test_validate_eval_coverage_rejects_missing_case(self) -> None:
        from pipeline.decision.llm_evals import validate_eval_coverage

        with self.assertRaises(AssertionError):
            validate_eval_coverage([], required_names={"missing"})


if __name__ == "__main__":
    unittest.main()
