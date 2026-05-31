import subprocess
import tempfile
import unittest
from pathlib import Path


class FakeRunner:
    def __init__(self, returncodes=None):
        self.calls = []
        self.returncodes = list(returncodes or [])

    def __call__(self, cmd, *, cwd, capture_output, text, timeout, check):
        self.calls.append(
            {
                "cmd": cmd,
                "cwd": cwd,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        return subprocess.CompletedProcess(
            cmd,
            self.returncodes.pop(0) if self.returncodes else 0,
            stdout="ok",
            stderr="",
        )


class DailyPipelineTest(unittest.TestCase):
    def test_daily_pipeline_runs_sources_then_decision_with_bounded_defaults(self):
        from pipeline.run_daily import run_daily

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            runner = FakeRunner()

            summary = run_daily(
                root=root,
                python="py",
                run_id="decision_daily_test",
                now="2026-05-31T12:00:00Z",
                runner=runner,
            )

        self.assertTrue(summary["ok"])
        self.assertEqual(len(runner.calls), 2)
        self.assertEqual(runner.calls[0]["cmd"], ["py", str(root / "pipeline" / "run_pipeline.py")])
        self.assertEqual(
            runner.calls[1]["cmd"],
            [
                "py",
                "-m",
                "pipeline.decision.run_decision",
                "--run-id",
                "decision_daily_test",
                "--now",
                "2026-05-31T12:00:00Z",
                "--backfill",
                "--classify-hn-limit",
                "200",
                "--classify-x-limit",
                "300",
                "--llm-concurrency",
                "4",
                "--resolver-search-limit",
                "100",
                "--resolver-research-limit",
                "50",
                "--resolver-research-rounds",
                "3",
                "--enrich-readme-limit",
                "100",
            ],
        )

    def test_daily_pipeline_can_run_hn_only_decision_without_sources_x_or_backfill(self):
        from pipeline.run_daily import run_daily

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            runner = FakeRunner()

            summary = run_daily(
                root=root,
                python="py",
                run_id="decision_hn_only",
                now="2026-05-31T12:00:00Z",
                skip_sources=True,
                backfill=False,
                classify_hn_limit=400,
                classify_x_limit=0,
                resolver_search_limit=80,
                resolver_research_limit=0,
                enrich_readme_limit=0,
                runner=runner,
            )

        self.assertTrue(summary["ok"])
        self.assertEqual(len(runner.calls), 1)
        cmd = runner.calls[0]["cmd"]
        self.assertIn("--classify-hn-limit", cmd)
        self.assertIn("400", cmd)
        self.assertNotIn("--classify-x-limit", cmd)
        self.assertNotIn("--backfill", cmd)

    def test_daily_pipeline_stops_after_failed_source_stage(self):
        from pipeline.run_daily import run_daily

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            runner = FakeRunner(returncodes=[7])

            summary = run_daily(root=root, python="py", runner=runner)

        self.assertFalse(summary["ok"])
        self.assertEqual(summary["returncode"], 7)
        self.assertEqual(len(runner.calls), 1)

    def test_daily_pipeline_lock_prevents_overlapping_runs(self):
        from pipeline.run_daily import run_daily

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            lock_path = root / "data" / "run_daily.lock"
            lock_path.parent.mkdir(parents=True)
            lock_path.write_text("busy")

            with self.assertRaises(RuntimeError):
                run_daily(root=root, python="py", runner=FakeRunner())

    def test_daily_pipeline_can_run_layer2_after_decision(self):
        from pipeline.run_daily import run_daily

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            runner = FakeRunner()

            summary = run_daily(
                root=root,
                python="py",
                run_id="decision_daily_test",
                now="2026-05-31T12:00:00Z",
                run_layer2=True,
                layer2_scout_limit=10,
                layer2_scoring_limit=20,
                layer2_deepdive_limit=2,
                runner=runner,
            )

        self.assertTrue(summary["ok"])
        self.assertEqual(len(runner.calls), 3)
        self.assertEqual(
            runner.calls[2]["cmd"],
            [
                "py",
                "-m",
                "pipeline.decision.run_layer2_feed",
                "--decision-run-id",
                "decision_daily_test",
                "--now",
                "2026-05-31T12:00:00Z",
                "--edge-scout-limit",
                "10",
                "--scoring-limit",
                "20",
                "--deepdive-limit",
                "2",
            ],
        )

    def test_daily_pipeline_passes_layer2_web_search_and_tool_budgets(self):
        from pipeline.run_daily import run_daily

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            runner = FakeRunner()

            run_daily(
                root=root,
                python="py",
                run_id="decision_daily_test",
                now="2026-05-31T12:00:00Z",
                run_layer2=True,
                layer2_enable_kimi_web_search=True,
                layer2_max_tool_calls=20,
                layer2_max_web_search_calls=3,
                layer2_max_repo_files=8,
                layer2_max_pages=6,
                runner=runner,
            )

        cmd = runner.calls[2]["cmd"]
        self.assertIn("--enable-kimi-web-search", cmd)
        self.assertIn("--max-tool-calls-per-candidate", cmd)
        self.assertIn("20", cmd)
        self.assertIn("--max-web-search-calls-per-candidate", cmd)
        self.assertIn("3", cmd)
        self.assertIn("--max-repo-files-per-candidate", cmd)
        self.assertIn("8", cmd)
        self.assertIn("--max-pages-per-candidate", cmd)
        self.assertIn("6", cmd)


if __name__ == "__main__":
    unittest.main()
