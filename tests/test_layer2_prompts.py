from __future__ import annotations

import unittest


class Layer2PromptTest(unittest.TestCase):
    def test_scoring_v2_is_composed_from_stable_named_policy_sections(self):
        from pipeline.decision.layer2_prompts import (
            SCORING_PROMPT_SECTIONS,
            assemble_scoring_investigator_prompt_v2,
        )

        self.assertEqual(
            list(SCORING_PROMPT_SECTIONS),
            [
                "role_and_decision",
                "evidence_and_trust",
                "scoring_rubric",
                "tool_selection",
                "stopping_policy",
                "output_contract",
            ],
        )
        prompt = assemble_scoring_investigator_prompt_v2()
        self.assertIn("untrusted external evidence", prompt)
        self.assertIn("Momentum must not substitute", prompt)
        self.assertIn("evidence_ref", prompt)
        self.assertIn("must_finalize", prompt)
        self.assertNotIn("at most 3", prompt)
        self.assertNotIn("repo_key", prompt)
        self.assertNotIn("README.md", prompt)


if __name__ == "__main__":
    unittest.main()
