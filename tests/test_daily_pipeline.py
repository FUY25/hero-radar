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


if __name__ == "__main__":
    unittest.main()
