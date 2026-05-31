from __future__ import annotations

import subprocess
import sys
import unittest


class SmokeScriptsTest(unittest.TestCase):
    def test_smoke_scripts_can_run_from_file_path(self) -> None:
        for script in (
            "pipeline/decision/smoke_llm.py",
            "pipeline/decision/smoke_npm.py",
        ):
            with self.subTest(script=script):
                result = subprocess.run(
                    [sys.executable, script, "--help"],
                    capture_output=True,
                    check=False,
                    text=True,
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout)

    def test_llm_smoke_summary_reports_shape_without_secret_values(self) -> None:
        from pipeline.decision.smoke_llm import summarize_llm_result

        class Provider:
            provider_name = "deepseek"
            model = "deepseek-v4-flash"
            api_key = "secret-value"

        summary = summarize_llm_result(
            {"ok": True, "secret": "secret-value", "confidence": 0.8},
            Provider(),
        )

        self.assertEqual(summary["ok"], True)
        self.assertEqual(summary["provider"], "deepseek")
        self.assertEqual(summary["model"], "deepseek-v4-flash")
        self.assertEqual(summary["keys"], ["confidence", "ok", "secret"])
        self.assertNotIn("secret-value", repr(summary))

    def test_npm_smoke_summary_keeps_registry_payload_small(self) -> None:
        from pipeline.decision.smoke_npm import summarize_npm_result

        summary = summarize_npm_result(
            package="@scope/demo",
            period="last-day",
            metadata={
                "name": "@scope/demo",
                "repository": {"url": "git+https://github.com/Owner/Repo.git"},
            },
            downloads={"downloads": 12345, "package": "@scope/demo"},
        )

        self.assertEqual(
            summary,
            {
                "ok": True,
                "package": "@scope/demo",
                "period": "last-day",
                "downloads": 12345,
                "has_repository": True,
                "github_key": "github:owner/repo",
            },
        )


if __name__ == "__main__":
    unittest.main()
