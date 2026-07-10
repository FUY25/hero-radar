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
                    "ok_with_errors",
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
                            "routing": {
                                "max_edge_watch_scout": 50,
                                "max_scored_candidates": 0,
                                "max_total_scoring_candidates": 17,
                                "max_deepdives_per_run": 0,
                                "deepdive_min_l2_score": 70,
                                "brief_min_score": 72,
                                "brief_target_count": 7,
                                "brief_max_count": 9,
                                "score_only_min_score": 52,
                                "known_paradigm_keys": ["github:owner/known"],
                            },
                            "scoring_agent": {
                                "provider": "kimi",
                                "model": "score-model",
                                "prompt_id": "scorer-id",
                                "prompt_version": "scorer-v1",
                                "output_schema_version": "score-schema-v1",
                                "context_policy_version": "context-v1",
                                "timeout_seconds": 91,
                                "max_output_tokens": 1800,
                                "max_investigation_turns": 2,
                                "max_scoring_attempts": 4,
                                "enable_direct_final": True,
                                "context_budget": {
                                    "max_context_tokens": 28000,
                                    "safety_margin": 600,
                                    "identity_allocation": 700,
                                    "evidence_summary_allocation": 750,
                                    "top_evidence_allocation": 2100,
                                    "previous_turn_allocation": 650,
                                    "tool_observation_allocation": 2200,
                                    "recent_raw_tool_result_count": 2,
                                },
                                "tool_budget": {
                                    "max_calls_per_candidate": 8,
                                    "max_web_search_calls_per_candidate": 1,
                                    "max_github_file_calls_per_candidate": 3,
                                    "max_homepage_calls_per_candidate": 1,
                                },
                            },
                            "brief_writer": {
                                "enabled": True,
                                "provider": "kimi",
                                "model": "brief-model",
                                "prompt_id": "brief-id",
                                "prompt_version": "brief-v2",
                                "output_schema_version": "brief-schema-v1",
                                "timeout_seconds": 61,
                                "max_output_tokens": 1000,
                            },
                            "tool_runtime": {
                                "registry_version": "registry-v1",
                                "enable_kimi_web_search": False,
                                "web_search_timeout_seconds": 31,
                                "max_evidence_rows_per_fetch": 71,
                                "max_github_file_chars": 5001,
                                "max_homepage_chars": 5002,
                                "max_web_results": 4,
                            },
                            "edge_scout": {
                                "provider": "kimi",
                                "model": "scout-model",
                                "timeout_seconds": 41,
                            },
                            "legacy_deepdive": {
                                "provider": "kimi",
                                "model": "deepdive-model",
                                "timeout_seconds": 51,
                                "max_tool_calls_per_candidate": 13,
                                "max_web_search_calls_per_candidate": 2,
                                "max_repo_files_per_candidate": 6,
                                "max_pages_per_candidate": 4,
                            },
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
        self.assertEqual(layer2_cmd[layer2_cmd.index("--scoring-model") + 1], "score-model")
        self.assertEqual(layer2_cmd[layer2_cmd.index("--brief-model") + 1], "brief-model")
        self.assertEqual(
            layer2_cmd[layer2_cmd.index("--scoring-max-output-tokens") + 1],
            "1800",
        )
        self.assertEqual(
            layer2_cmd[layer2_cmd.index("--brief-max-output-tokens") + 1],
            "1000",
        )
        expected = {
            "--max-total-scoring-candidates": "17",
            "--brief-min-score": "72.0",
            "--brief-target-count": "7",
            "--brief-max-count": "9",
            "--score-only-min-score": "52.0",
            "--scoring-prompt-version": "scorer-v1",
            "--brief-prompt-version": "brief-v2",
            "--max-investigation-turns": "2",
            "--max-scoring-attempts": "4",
            "--max-context-tokens": "28000",
            "--context-safety-margin": "600",
            "--identity-token-allocation": "700",
            "--evidence-summary-token-allocation": "750",
            "--top-evidence-token-allocation": "2100",
            "--previous-turn-token-allocation": "650",
            "--tool-observation-token-allocation": "2200",
            "--recent-raw-tool-result-count": "2",
            "--max-evidence-rows-per-fetch": "71",
            "--max-github-file-chars": "5001",
            "--max-homepage-chars": "5002",
            "--max-web-results": "4",
            "--scout-timeout-seconds": "41",
            "--deepdive-timeout-seconds": "51",
            "--web-search-timeout-seconds": "31",
            "--legacy-max-tool-calls-per-candidate": "13",
            "--legacy-max-web-search-calls-per-candidate": "2",
            "--legacy-max-repo-files-per-candidate": "6",
            "--legacy-max-pages-per-candidate": "4",
        }
        for flag, value in expected.items():
            self.assertEqual(layer2_cmd[layer2_cmd.index(flag) + 1], value)
        self.assertIn("--enable-direct-final", layer2_cmd)
        self.assertEqual(
            layer2_cmd[layer2_cmd.index("--known-paradigm-key") + 1],
            "github:owner/known",
        )

    def test_daily_pipeline_rejects_flat_layer2_config(self):
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
                            "scoring_model": "obsolete-flat-model",
                        }
                    }
                )
            )

            with self.assertRaisesRegex(ValueError, "canonical nested"):
                run_daily(root=root, python="py", runner=FakeRunner())

    def test_daily_pipeline_maps_component_enabled_states(self):
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
                            "routing": {},
                            "scoring_agent": {},
                            "brief_writer": {"enabled": False},
                            "tool_runtime": {},
                            "edge_scout": {"enabled": True},
                            "legacy_deepdive": {"enabled": True},
                        }
                    }
                )
            )
            runner = FakeRunner()

            run_daily(root=root, python="py", runner=runner)

        layer2_cmd = runner.calls[2]["cmd"]
        self.assertIn("--enable-edge-scout", layer2_cmd)
        self.assertIn("--enable-legacy-deepdive", layer2_cmd)
        self.assertIn("--no-briefs", layer2_cmd)

    def test_daily_pipeline_passes_configured_parallelism_and_rate_limits(self):
        from pipeline.run_daily import run_daily

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "pipeline" / "config.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                json.dumps(
                    {
                        "decision": {
                            "io_concurrency": 7,
                            "io_rate_limit_per_second": 1.5,
                        },
                        "layer2": {
                            "enabled": True,
                            "routing": {},
                            "scoring_agent": {
                                "concurrency": 6,
                                "tool_budget": {
                                    "max_parallel_calls_per_turn": 3
                                },
                            },
                            "brief_writer": {"concurrency": 4},
                            "tool_runtime": {
                                "families": {
                                    "github": {
                                        "max_in_flight": 5,
                                        "starts_per_second": 1.5,
                                    },
                                    "homepage": {
                                        "max_in_flight": 4,
                                        "starts_per_second": 1.25,
                                    },
                                    "web_search": {
                                        "max_in_flight": 2,
                                        "starts_per_second": 0.75,
                                    },
                                }
                            },
                            "edge_scout": {},
                            "legacy_deepdive": {},
                        },
                    }
                )
            )
            runner = FakeRunner()

            summary = run_daily(
                root=root,
                python="py",
                run_id="decision_daily_parallel_config",
                now="2026-07-10T12:00:00Z",
                runner=runner,
            )

        self.assertTrue(summary["ok"])
        decision_cmd = runner.calls[1]["cmd"]
        self.assertEqual(decision_cmd[decision_cmd.index("--io-concurrency") + 1], "7")
        self.assertEqual(
            decision_cmd[decision_cmd.index("--io-rate-limit-per-second") + 1],
            "1.5",
        )
        layer2_cmd = runner.calls[2]["cmd"]
        expected = {
            "--scoring-concurrency": "6",
            "--brief-concurrency": "4",
            "--max-parallel-tool-calls-per-turn": "3",
            "--github-tool-concurrency": "5",
            "--homepage-tool-concurrency": "4",
            "--web-search-tool-concurrency": "2",
            "--github-tool-rate-limit-per-second": "1.5",
            "--homepage-tool-rate-limit-per-second": "1.25",
            "--web-search-tool-rate-limit-per-second": "0.75",
        }
        for flag, value in expected.items():
            self.assertEqual(layer2_cmd[layer2_cmd.index(flag) + 1], value)

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
