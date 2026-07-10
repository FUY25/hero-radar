from __future__ import annotations

import unittest


class Layer2ContractsTest(unittest.TestCase):
    def test_scoring_output_v2_is_strict_and_requires_attributable_claims(self):
        from pipeline.decision.layer2_contracts import scoring_turn_output_schema_v2

        schema = scoring_turn_output_schema_v2()
        self.assertEqual(schema["$id"], "layer2-scoring-output-v2")
        self.assertFalse(schema["additionalProperties"])
        score = schema["$defs"]["score"]
        self.assertFalse(score["additionalProperties"])
        claim = schema["$defs"]["claim"]
        self.assertEqual(
            claim["required"],
            ["claim", "evidence_refs", "supports_axes", "claim_type"],
        )
        self.assertFalse(claim["additionalProperties"])
        self.assertEqual(claim["properties"]["evidence_refs"]["minItems"], 1)
        self.assertEqual(
            score["properties"]["axes"]["properties"]["risk_penalty"]["maximum"],
            25,
        )
        self.assertEqual(len(schema["oneOf"]), 2)

    def test_brief_output_schema_is_strict_and_bounded(self):
        from pipeline.decision.layer2_contracts import brief_writer_output_schema_v1

        schema = brief_writer_output_schema_v1()
        self.assertEqual(schema["$id"], "layer2-brief-output-v1")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            schema["required"],
            ["category", "headline", "core_highlights", "use_cases"],
        )
        self.assertEqual(schema["properties"]["core_highlights"]["maxItems"], 3)
        self.assertEqual(schema["properties"]["use_cases"]["maxItems"], 4)


if __name__ == "__main__":
    unittest.main()
