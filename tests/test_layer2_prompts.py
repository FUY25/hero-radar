from __future__ import annotations

import json
import unittest
from pathlib import Path


class Layer2ScoringPromptTest(unittest.TestCase):
    def test_scoring_v2_is_composed_from_stable_named_policy_sections(self) -> None:
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

    def test_prompt_registry_selects_exact_versions_and_rejects_unknown(self) -> None:
        from pipeline.decision.layer2_prompts import (
            SCORING_INVESTIGATOR_SYSTEM_PROMPT_V1,
            SCORING_INVESTIGATOR_SYSTEM_PROMPT_V2,
            SCORING_PROMPT_REGISTRY,
            scoring_prompt_for_version,
        )

        self.assertEqual(
            SCORING_PROMPT_REGISTRY,
            {
                "layer2-scoring-investigator-v1": (
                    SCORING_INVESTIGATOR_SYSTEM_PROMPT_V1
                ),
                "layer2-scoring-investigator-v2": (
                    SCORING_INVESTIGATOR_SYSTEM_PROMPT_V2
                ),
            },
        )
        self.assertEqual(
            scoring_prompt_for_version("layer2-scoring-investigator-v1"),
            SCORING_INVESTIGATOR_SYSTEM_PROMPT_V1,
        )
        self.assertEqual(
            scoring_prompt_for_version("layer2-scoring-investigator-v2"),
            SCORING_INVESTIGATOR_SYSTEM_PROMPT_V2,
        )
        for unsupported in [
            "layer2-scoring-investigator-v3",
            "layer2-scoring-investigator-v22",
            "foo",
            "",
        ]:
            with self.subTest(prompt_version=unsupported):
                with self.assertRaisesRegex(
                    ValueError,
                    f"unsupported scoring prompt version: {unsupported}",
                ):
                    scoring_prompt_for_version(unsupported)

    def test_scoring_runtime_rejects_unknown_prompt_version_before_model_call(
        self,
    ) -> None:
        import sqlite3

        from pipeline.decision.layer2_scoring_investigator import (
            score_with_investigator,
        )
        from pipeline.decision.llm_provider import FakeLLMProvider

        provider = FakeLLMProvider([])
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)

        with self.assertRaisesRegex(
            ValueError,
            "unsupported scoring prompt version: layer2-scoring-investigator-v22",
        ):
            score_with_investigator(
                conn,
                feed_run_id="l2-run",
                groups=[],
                provider=provider,
                tools={},
                prompt_version="layer2-scoring-investigator-v22",
            )

        self.assertEqual(provider.calls, [])

    def test_scoring_runtime_does_not_expose_unversioned_prompt_alias(self) -> None:
        from pipeline.decision import layer2_prompts
        from pipeline.decision import layer2_scoring_investigator as investigator

        self.assertFalse(
            hasattr(layer2_prompts, "SCORING_INVESTIGATOR_SYSTEM_PROMPT")
        )
        self.assertFalse(
            hasattr(investigator, "SCORING_INVESTIGATOR_SYSTEM_PROMPT")
        )

    def test_real_eval_runtime_rejects_unknown_prompt_version(self) -> None:
        from pipeline.decision.run_layer2_evals import (
            run_scoring_investigator_kimi_eval,
        )

        class Provider:
            api_key = "configured"

        with self.assertRaisesRegex(
            ValueError,
            "unsupported scoring prompt version: foo",
        ):
            run_scoring_investigator_kimi_eval(
                provider=Provider(),
                cases=[],
                prompt_version="foo",
            )

    def test_v2_prompt_treats_tool_failures_as_missing_information_and_stops_by_route(
        self,
    ) -> None:
        from pipeline.decision.layer2_prompts import (
            SCORING_INVESTIGATOR_SYSTEM_PROMPT_V2,
        )

        prompt = " ".join(SCORING_INVESTIGATOR_SYSTEM_PROMPT_V2.split())
        self.assertIn(
            "A rejected, unavailable, rate-limited, timed-out, or failed tool "
            "call is not negative evidence about the candidate. It only limits "
            "information availability unless the returned evidence directly "
            "establishes a candidate fact.",
            prompt,
        )
        self.assertIn(
            "Every supporting or negative claim in a final score must cite one "
            "or more values listed in valid_evidence_refs.",
            prompt,
        )
        self.assertIn(
            "Finalize when candidate identity is sufficient and every "
            "decision-relevant axis is either supported by attributable evidence "
            "or represented as an explicit gap.",
            prompt,
        )
        self.assertIn(
            "Do not spend remaining budget only to reduce uncertainty when the "
            "likely route would not change.",
            prompt,
        )
        self.assertIn(
            "Return action=use_tools only when tool-selection policy is satisfied; "
            "otherwise return action=final.",
            prompt,
        )
        self.assertIn(
            "Do not include Markdown, analysis, commentary, or fields not allowed "
            "by the schema.",
            prompt,
        )
        self.assertNotIn("hidden instructions", prompt)

    def test_v2_is_the_runtime_and_repository_default(self) -> None:
        from pipeline.decision.layer2_scoring_investigator import (
            DEFAULT_INVESTIGATOR_PROMPT_VERSION,
        )

        config = json.loads(
            (Path(__file__).resolve().parents[1] / "pipeline" / "config.json")
            .read_text(encoding="utf-8")
        )

        self.assertEqual(
            DEFAULT_INVESTIGATOR_PROMPT_VERSION,
            "layer2-scoring-investigator-v2",
        )
        self.assertEqual(
            config["layer2"]["scoring_agent"]["prompt_version"],
            "layer2-scoring-investigator-v2",
        )


if __name__ == "__main__":
    unittest.main()
