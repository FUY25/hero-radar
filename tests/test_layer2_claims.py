from __future__ import annotations

import unittest


class Layer2ClaimsTest(unittest.TestCase):
    def test_normalizes_attributable_claims_and_projects_legacy_text(self):
        from pipeline.decision.layer2_claims import normalize_attributable_claims

        claims, text = normalize_attributable_claims(
            [
                {
                    "claim": "The README documents a validation harness.",
                    "evidence_refs": ["evidence:12", "tool:t1:0"],
                    "supports_axes": ["workflow_shift", "technical_substance"],
                    "claim_type": "observed",
                }
            ],
            valid_evidence_refs={"evidence:12", "tool:t1:0"},
        )

        self.assertEqual(text, ["The README documents a validation harness."])
        self.assertEqual(claims[0]["claim_type"], "observed")
        self.assertEqual(
            claims[0]["evidence_refs"], ["evidence:12", "tool:t1:0"]
        )

    def test_rejects_unknown_evidence_references(self):
        from pipeline.decision.layer2_claims import (
            EvidenceReferenceError,
            normalize_attributable_claims,
        )

        with self.assertRaisesRegex(EvidenceReferenceError, "evidence:invented"):
            normalize_attributable_claims(
                [
                    {
                        "claim": "Unsupported claim.",
                        "evidence_refs": ["evidence:invented"],
                        "supports_axes": ["workflow_shift"],
                        "claim_type": "inferred",
                    }
                ],
                valid_evidence_refs={"evidence:12"},
            )


if __name__ == "__main__":
    unittest.main()
