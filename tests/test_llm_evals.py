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
                "hn_hot_news_with_company_url_noise",
                "hn_show_hn_product_domain_potential",
            },
        )

    def test_x_eval_cases_cover_linked_potential_and_fuzzy_noise(self) -> None:
        from pipeline.decision.llm_evals import validate_eval_coverage, x_eval_cases

        cases = x_eval_cases()
        names = {case["name"] for case in cases}

        self.assertIn("x_linked_two_credible_potential", names)
        self.assertIn("x_fuzzy_no_citations_not_potential", names)
        self.assertIn("x_generic_known_term_none", names)
        self.assertIn("x_single_credible_fuzzy_product_watch", names)
        self.assertIn("x_two_credible_high_clamps_to_potential", names)
        validate_eval_coverage(cases, required_names=names)

    def test_eval_cases_encode_safety_expectations(self) -> None:
        from pipeline.decision.llm_evals import hn_eval_cases, x_eval_cases

        hn_by_name = {case["name"]: case for case in hn_eval_cases()}
        x_by_name = {case["name"]: case for case in x_eval_cases()}

        self.assertFalse(hn_by_name["hn_project_with_github"]["expected"]["noise"])
        self.assertTrue(hn_by_name["hn_news_noise"]["expected"]["noise"])
        self.assertTrue(hn_by_name["hn_topic_noise"]["expected"]["noise"])
        self.assertTrue(hn_by_name["hn_hot_news_with_company_url_noise"]["expected"]["noise"])
        self.assertFalse(hn_by_name["hn_show_hn_product_domain_potential"]["expected"]["noise"])
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
        self.assertEqual(
            x_by_name["x_single_credible_fuzzy_product_watch"]["expected"]["max_tier"],
            "watch",
        )
        self.assertEqual(
            x_by_name["x_two_credible_high_clamps_to_potential"]["expected"]["max_tier"],
            "potential",
        )

    def test_x_stage1_eval_cases_encode_product_signal_shape(self) -> None:
        from pipeline.decision.llm_evals import x_eval_cases

        stage1_cases = [case for case in x_eval_cases() if case.get("fake_stage1_response")]

        self.assertGreaterEqual(len(stage1_cases), 2)
        for case in stage1_cases:
            for item in case["fake_stage1_response"]["triage"]:
                self.assertIn("product_names", item, case["name"])
                self.assertIn("product_links", item, case["name"])

    def test_hn_alias_eval_cases_include_verifiable_links(self) -> None:
        from pipeline.decision.llm_evals import hn_eval_cases

        for case in hn_eval_cases():
            expected = case["expected"]
            if not expected.get("requires_alias"):
                continue
            haystack = " ".join(str(value) for value in case["input"].values()).lower()
            if expected.get("deterministic_link_host"):
                self.assertIn(
                    expected["deterministic_link_host"],
                    haystack,
                    f"{case['name']} requires an alias but has no verifiable host",
                )
                continue
            self.assertIn(
                f"{expected['deterministic_link_type']}.com",
                haystack,
                f"{case['name']} requires an alias but has no verifiable link",
            )

    def test_validate_eval_coverage_rejects_missing_case(self) -> None:
        from pipeline.decision.llm_evals import validate_eval_coverage

        with self.assertRaises(AssertionError):
            validate_eval_coverage([], required_names={"missing"})


if __name__ == "__main__":
    unittest.main()
