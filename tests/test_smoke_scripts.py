from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
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

    def test_llm_smoke_loads_deepseek_key_from_local_json_without_overwrite(self) -> None:
        from pipeline.decision.smoke_llm import load_json_secrets

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "secrets.local.json"
            path.write_text(
                json.dumps(
                    {
                        "deepseek": {
                            "api_key": "json-secret",
                            "model": "deepseek-v4-pro",
                            "base_url": "https://api.deepseek.com",
                        }
                    }
                )
            )
            original_key = os.environ.pop("DEEPSEEK_API_KEY", None)
            original_model = os.environ.pop("DEEPSEEK_MODEL", None)
            original_base_url = os.environ.pop("DEEPSEEK_BASE_URL", None)
            try:
                load_json_secrets(path)
                self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "json-secret")
                self.assertEqual(os.environ["DEEPSEEK_MODEL"], "deepseek-v4-pro")
                self.assertEqual(os.environ["DEEPSEEK_BASE_URL"], "https://api.deepseek.com")

                os.environ["DEEPSEEK_API_KEY"] = "existing-secret"
                load_json_secrets(path)
                self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "existing-secret")
            finally:
                for key in ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL"):
                    os.environ.pop(key, None)
                if original_key is not None:
                    os.environ["DEEPSEEK_API_KEY"] = original_key
                if original_model is not None:
                    os.environ["DEEPSEEK_MODEL"] = original_model
                if original_base_url is not None:
                    os.environ["DEEPSEEK_BASE_URL"] = original_base_url

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
