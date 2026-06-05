import subprocess
import sys
import tempfile
import unittest
import json
import os
import sqlite3
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
        self.assertEqual(
            runner.calls[0]["cmd"],
            [
                "py",
                str(root / "pipeline" / "run_pipeline.py"),
                "--log-path",
                str(root / "data" / "logs" / "run_daily" / "decision_daily_test.sources.jsonl"),
            ],
        )
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
                "--npm-backfill-limit",
                "40",
                "--enrich-readme-limit",
                "100",
                "--log-path",
                str(root / "data" / "logs" / "run_daily" / "decision_daily_test.decision.jsonl"),
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
                npm_backfill_limit=0,
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
        self.assertNotIn("--npm-backfill-limit", cmd)

    def test_daily_pipeline_stops_after_failed_source_stage(self):
        from pipeline.run_daily import run_daily

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            runner = FakeRunner(returncodes=[7])

            summary = run_daily(root=root, python="py", runner=runner)

        self.assertFalse(summary["ok"])
        self.assertEqual(summary["returncode"], 7)
        self.assertEqual(len(runner.calls), 1)

    def test_daily_pipeline_writes_structured_run_log(self):
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

            log_path = Path(summary["log_path"])
            events = [json.loads(line) for line in log_path.read_text().splitlines()]

        self.assertEqual(log_path.name, "decision_daily_test.jsonl")
        self.assertEqual(events[0]["event"], "run_started")
        self.assertEqual(events[0]["run_id"], "decision_daily_test")
        self.assertEqual([event["event"] for event in events], [
            "run_started",
            "stage_started",
            "stage_completed",
            "stage_started",
            "stage_completed",
            "run_completed",
        ])
        self.assertEqual(events[1]["stage"], "sources")
        self.assertEqual(events[2]["returncode"], 0)
        self.assertIn("duration_seconds", events[2])
        self.assertEqual(events[3]["stage"], "decision")
        self.assertIn("--log-path", runner.calls[1]["cmd"])
        self.assertIn(str(log_path.parent / "decision_daily_test.decision.jsonl"), runner.calls[1]["cmd"])
        self.assertEqual(events[-1]["ok"], True)

    def test_daily_pipeline_logs_failed_stage_before_returning(self):
        from pipeline.run_daily import run_daily

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            runner = FakeRunner(returncodes=[7])

            summary = run_daily(
                root=root,
                python="py",
                run_id="decision_daily_failed",
                now="2026-05-31T12:00:00Z",
                runner=runner,
            )

            log_path = Path(summary["log_path"])
            events = [json.loads(line) for line in log_path.read_text().splitlines()]

        self.assertEqual([event["event"] for event in events], [
            "run_started",
            "stage_started",
            "stage_failed",
            "run_failed",
        ])
        self.assertEqual(events[2]["stage"], "sources")
        self.assertEqual(events[2]["returncode"], 7)
        self.assertEqual(events[-1]["returncode"], 7)

    def test_daily_pipeline_lock_prevents_overlapping_runs(self):
        from pipeline.run_daily import run_daily

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            lock_path = root / "data" / "run_daily.lock"
            lock_path.parent.mkdir(parents=True)
            lock_path.write_text("busy")

            with self.assertRaises(RuntimeError):
                run_daily(root=root, python="py", runner=FakeRunner())

    def test_run_daily_script_can_be_invoked_by_file_path(self):
        run_id = "test_direct_cli_run_daily"
        log_path = Path("data/logs/run_daily") / f"{run_id}.jsonl"
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "pipeline/run_daily.py",
                    "--run-id",
                    run_id,
                    "--now",
                    "2026-05-31T12:00:00Z",
                    "--skip-sources",
                    "--skip-decision",
                    "--no-layer2",
                    "--timeout",
                    "5",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
                env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
            )
        finally:
            log_path.unlink(missing_ok=True)

        self.assertEqual(result.returncode, 0, result.stderr)

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
                "--finalize-stale-running-before",
                "2026-05-31T12:00:00Z",
            ],
        )

    def test_resume_skips_completed_source_stage_from_log(self):
        from pipeline.run_daily import run_daily

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            log_dir = root / "data" / "logs" / "run_daily"
            log_dir.mkdir(parents=True)
            (log_dir / "decision_daily_resume.jsonl").write_text(
                json.dumps(
                    {
                        "event": "stage_completed",
                        "run_id": "decision_daily_resume",
                        "stage": "sources",
                        "returncode": 0,
                    }
                )
                + "\n"
            )
            runner = FakeRunner()

            summary = run_daily(
                root=root,
                python="py",
                run_id="decision_daily_resume",
                now="2026-05-31T12:00:00Z",
                resume=True,
                runner=runner,
            )

        self.assertTrue(summary["ok"])
        self.assertEqual(len(runner.calls), 1)
        self.assertIn("pipeline.decision.run_decision", runner.calls[0]["cmd"])

    def test_resume_skips_completed_decision_run_and_continues_to_layer2(self):
        from pipeline.decision.schema import init_decision_db
        from pipeline.run_daily import run_daily

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "data" / "hero_radar.sqlite"
            db_path.parent.mkdir(parents=True)
            conn = sqlite3.connect(db_path)
            init_decision_db(conn)
            conn.execute(
                "insert into decision_runs(run_id, source_snapshot_run_id, started_at, completed_at, status, config_hash, rule_version, note) values (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "decision_daily_resume",
                    "source-run",
                    "2026-05-31T12:00:00Z",
                    "2026-05-31T12:01:00Z",
                    "ok",
                    "config",
                    "rules",
                    "",
                ),
            )
            conn.commit()
            conn.close()
            runner = FakeRunner()

            summary = run_daily(
                root=root,
                python="py",
                run_id="decision_daily_resume",
                now="2026-05-31T12:00:00Z",
                skip_sources=True,
                run_layer2=True,
                resume=True,
                runner=runner,
            )

        self.assertTrue(summary["ok"])
        self.assertEqual(len(runner.calls), 1)
        self.assertIn("pipeline.decision.run_layer2_feed", runner.calls[0]["cmd"])

    def test_resume_skips_completed_layer2_run(self):
        from pipeline.decision.schema import init_decision_db
        from pipeline.run_daily import run_daily

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "data" / "hero_radar.sqlite"
            db_path.parent.mkdir(parents=True)
            conn = sqlite3.connect(db_path)
            init_decision_db(conn)
            conn.execute(
                "insert into decision_runs(run_id, source_snapshot_run_id, started_at, completed_at, status, config_hash, rule_version, note) values (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "decision_daily_resume",
                    "source-run",
                    "2026-05-31T12:00:00Z",
                    "2026-05-31T12:01:00Z",
                    "ok",
                    "config",
                    "rules",
                    "",
                ),
            )
            conn.execute(
                "insert into l2_feed_runs(feed_run_id, decision_run_id, started_at, completed_at, status, config_hash, model_profile_json, note) values (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "l2-resume",
                    "decision_daily_resume",
                    "2026-05-31T12:02:00Z",
                    "2026-05-31T12:03:00Z",
                    "ok",
                    "config",
                    "{}",
                    "{}",
                ),
            )
            conn.commit()
            conn.close()
            runner = FakeRunner()

            summary = run_daily(
                root=root,
                python="py",
                run_id="decision_daily_resume",
                now="2026-05-31T12:00:00Z",
                skip_sources=True,
                run_layer2=True,
                resume=True,
                runner=runner,
            )

        self.assertTrue(summary["ok"])
        self.assertEqual(runner.calls, [])

    def test_daily_pipeline_uses_layer2_config_defaults_without_explicit_flag(self):
        from pipeline.run_daily import run_daily

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "pipeline" / "config.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                json.dumps(
                    {
                        "layer2": {
                            "enabled": True,
                            "max_edge_watch_scout": 50,
                            "max_scored_candidates": 0,
                            "max_deepdives_per_run": 0,
                            "deepdive_min_l2_score": 70,
                            "scoring_model": "kimi-k2.5",
                            "enable_kimi_web_search": False,
                            "max_tool_calls_per_candidate": 8,
                            "max_web_search_calls_per_candidate": 1,
                            "max_repo_files_per_candidate": 3,
                            "max_pages_per_candidate": 1,
                        }
                    }
                )
            )
            runner = FakeRunner()

            summary = run_daily(
                root=root,
                python="py",
                run_id="decision_daily_configured",
                now="2026-05-31T12:00:00Z",
                runner=runner,
            )

        self.assertTrue(summary["ok"])
        self.assertEqual(len(runner.calls), 3)
        layer2_cmd = runner.calls[2]["cmd"]
        self.assertIn("pipeline.decision.run_layer2_feed", layer2_cmd)
        self.assertIn("--scoring-limit", layer2_cmd)
        self.assertEqual(layer2_cmd[layer2_cmd.index("--scoring-limit") + 1], "0")
        self.assertIn("--max-tool-calls-per-candidate", layer2_cmd)
        self.assertEqual(layer2_cmd[layer2_cmd.index("--max-tool-calls-per-candidate") + 1], "8")

    def test_explicit_no_layer2_overrides_enabled_config(self):
        from pipeline.run_daily import run_daily

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "pipeline" / "config.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(json.dumps({"layer2": {"enabled": True}}))
            runner = FakeRunner()

            summary = run_daily(
                root=root,
                python="py",
                run_id="decision_daily_no_layer2",
                now="2026-05-31T12:00:00Z",
                run_layer2=False,
                runner=runner,
            )

        self.assertTrue(summary["ok"])
        self.assertEqual(len(runner.calls), 2)

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
