from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from pipeline.decision.schema import init_decision_db


class Layer2RunnerTest(unittest.TestCase):
    def make_db_with_potentials(self, db_path: Path, names: list[str]) -> None:
        self.make_db_with_candidates(
            db_path, [(name, "potential") for name in names]
        )

    def make_db_with_candidates(
        self, db_path: Path, candidates: list[tuple[str, str]]
    ) -> None:
        conn = sqlite3.connect(db_path)
        init_decision_db(conn)
        conn.execute(
            "insert into decision_runs(run_id, source_snapshot_run_id, started_at, completed_at, status, config_hash, rule_version, note) values (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "decision-run",
                "source-run",
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:01:00Z",
                "ok",
                "hash",
                "rules-v1",
                "",
            ),
        )
        for index, (name, level) in enumerate(candidates):
            entity_id = f"entity:{index}"
            conn.execute(
                "insert into entities(entity_id, canonical_entity, canonical_key, key_type, first_seen, aliases_json, source_item_ids_json) values (?, ?, ?, ?, ?, ?, ?)",
                (
                    entity_id,
                    name,
                    f"github:{name}",
                    "github",
                    "2026-06-01T00:00:00Z",
                    "[]",
                    "[]",
                ),
            )
            conn.execute(
                "insert into potential_candidates(entity_id, run_id, level, fired_families_json, first_trigger_at) values (?, ?, ?, ?, ?)",
                (
                    entity_id,
                    "decision-run",
                    level,
                    '["github"]',
                    "2026-06-01T00:00:00Z",
                ),
            )
        conn.commit()
        conn.close()

    def valid_score_response(
        self,
        reason: str = "Workflow Shift",
        *,
        should_print: bool = True,
        object_type: str = "repo",
        is_product_or_repo: bool = True,
        axes: dict | None = None,
    ) -> dict:
        active_axes = {
            "momentum": 80,
            "workflow_shift": 80,
            "technical_substance": 80,
            "product_market_fit": 80,
            "confidence": 80,
            "risk_penalty": 0,
            "derivative_news_penalty": 0,
        }
        if axes:
            active_axes.update(axes)
        return {
            "action": "final",
            "score": {
                "object_type": object_type,
                "is_product_or_repo": is_product_or_repo,
                "axes": active_axes,
                "supporting_evidence": ["README shows a concrete workflow."],
                "negative_evidence": [],
                "known_gaps": [],
                "primary_reason": reason,
                "topic_tags": ["agent workflow"],
                "rationale_short": "Worth reading.",
                "caveats": [],
                "should_print": should_print,
            },
        }

    def valid_brief_response(self) -> dict:
        return {
            "category": {"primary": "开发工具", "tags": ["agent", "repo"]},
            "headline": "owner/repo 值得今天重点看",
            "core_highlights": [
                "把原本分散的开发流程压到一个可执行工具里。",
                "README 给出了明确的使用入口和技术机制。",
            ],
            "use_cases": ["开发者把重复的代码检查、文件修改和命令执行串成一次性工作流。"],
            "caveat": "还需要验证真实使用留存。",
        }

    def test_default_feed_run_id_is_stable_prefix(self):
        from pipeline.decision.run_layer2_feed import default_feed_run_id

        self.assertEqual(
            default_feed_run_id("2026-05-31T12:34:56Z"),
            "l2_20260531T123456",
        )

    def test_pre_full_run_helpers_create_explicit_ids_and_backup_db(self):
        from pipeline.decision.run_layer2_feed import (
            backup_sqlite_db,
            bounded_layer2_run_id,
            route_react_smoke_run_id,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero_radar.sqlite"
            db_path.write_bytes(b"sqlite-bytes")

            backup_path = backup_sqlite_db(
                db_path=db_path, now="2026-06-04T12:34:56Z"
            )

            self.assertEqual(
                route_react_smoke_run_id("2026-06-04T12:34:56Z"),
                "l2_route_react_smoke_20260604T123456Z",
            )
            self.assertEqual(
                bounded_layer2_run_id(30, "2026-06-04T12:34:56Z"),
                "l2_bounded_30_20260604T123456Z",
            )
            self.assertEqual(backup_path.name, "hero_radar.sqlite.20260604T123456Z.bak")
            self.assertEqual(backup_path.read_bytes(), b"sqlite-bytes")
            self.assertEqual(db_path.read_bytes(), b"sqlite-bytes")

    def test_run_layer2_with_fake_provider_writes_feed_run(self):
        from pipeline.decision.llm_provider import FakeLLMProvider
        from pipeline.decision.run_layer2_feed import run_layer2_feed

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            conn = sqlite3.connect(db_path)
            init_decision_db(conn)
            conn.executescript(
                """
                create table items (
                    id integer primary key,
                    source text not null,
                    name text not null,
                    url text,
                    description text,
                    metadata_json text not null,
                    raw_json text not null
                );
                """
            )
            conn.execute(
                "insert into decision_runs(run_id, source_snapshot_run_id, started_at, completed_at, status, config_hash, rule_version, note) values (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "decision-run",
                    "source-run",
                    "2026-05-31T00:00:00Z",
                    "2026-05-31T00:01:00Z",
                    "ok",
                    "hash",
                    "rules-v1",
                    "",
                ),
            )
            conn.execute(
                "insert into entities(entity_id, canonical_entity, canonical_key, key_type, first_seen, aliases_json, source_item_ids_json) values (?, ?, ?, ?, ?, ?, ?)",
                (
                    "entity:repo",
                    "owner/repo",
                    "github:owner/repo",
                    "github",
                    "2026-05-31T00:00:00Z",
                    "[]",
                    "[]",
                ),
            )
            conn.execute(
                "insert into potential_candidates(entity_id, run_id, level, fired_families_json, first_trigger_at) values (?, ?, ?, ?, ?)",
                (
                    "entity:repo",
                    "decision-run",
                    "potential",
                    '["github"]',
                    "2026-05-31T00:00:00Z",
                ),
            )
            conn.commit()
            conn.close()

            provider = FakeLLMProvider(
                [
                    self.valid_score_response(),
                    self.valid_brief_response(),
                ]
            )
            summary = run_layer2_feed(
                db_path=db_path,
                decision_run_id="decision-run",
                feed_run_id="l2-test",
                now="2026-05-31T12:00:00Z",
                provider=provider,
                config={"brief_min_score": 0, "brief_target_count": 1},
            )

            self.assertTrue(summary["ok"])
            self.assertEqual(summary["feed_run_id"], "l2-test")
            self.assertEqual(summary["briefs"], 1)
            self.assertEqual(summary["deepdives"], 0)
            conn = sqlite3.connect(db_path)
            self.assertEqual(
                conn.execute("select count(*) from l2_feed_runs").fetchone()[0], 1
            )
            self.assertEqual(
                conn.execute("select count(*) from l2_scores").fetchone()[0], 1
            )
            self.assertEqual(
                conn.execute(
                    "select count(*) from l2_scoring_investigations"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    "select count(*) from l2_deepdive_briefs"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    "select count(*) from l2_feed_items where section = ?",
                    ("today_focus",),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    "select count(*) from l2_feed_items where section = ?",
                    ("scored",),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("select count(*) from deepdive_reports").fetchone()[0],
                0,
            )
            brief_json, status = conn.execute(
                "select brief_json, status from l2_deepdive_briefs"
            ).fetchone()
            self.assertEqual(status, "ok")
            self.assertIn("开发工具", brief_json)
            conn.close()

    def test_run_layer2_routes_focus_score_only_and_diagnostics(self):
        from pipeline.decision.llm_provider import FakeLLMProvider
        from pipeline.decision.run_layer2_feed import run_layer2_feed

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            self.make_db_with_potentials(
                db_path,
                [
                    "a-focus/repo",
                    "anthropics/claude-plugins-official",
                    "c-weak/repo",
                    "d-news/repo",
                ],
            )
            provider = FakeLLMProvider(
                [
                    self.valid_score_response("Focus"),
                    self.valid_score_response(
                        "Backlog",
                        axes={
                            "workflow_shift": 60,
                            "technical_substance": 60,
                            "product_market_fit": 60,
                            "momentum": 60,
                            "confidence": 60,
                        },
                    ),
                    self.valid_score_response(
                        "Weak",
                        should_print=False,
                        axes={
                            "workflow_shift": 28,
                            "technical_substance": 30,
                            "product_market_fit": 36,
                            "momentum": 32,
                            "confidence": 70,
                            "derivative_news_penalty": 8,
                        },
                    ),
                    self.valid_score_response(
                        "News",
                        object_type="news",
                        is_product_or_repo=False,
                        axes={
                            "workflow_shift": 90,
                            "technical_substance": 80,
                            "product_market_fit": 82,
                            "momentum": 90,
                            "confidence": 80,
                            "derivative_news_penalty": 18,
                        },
                    ),
                    self.valid_brief_response(),
                ]
            )

            summary = run_layer2_feed(
                db_path=db_path,
                decision_run_id="decision-run",
                feed_run_id="l2-routes",
                now="2026-06-01T12:00:00Z",
                provider=provider,
                config={
                    "max_scored_candidates": 4,
                    "brief_min_score": 70,
                    "score_only_min_score": 50,
                    "brief_target_count": 1,
                    "brief_max_count": 1,
                },
            )

            conn = sqlite3.connect(db_path)
            items = conn.execute(
                """
                select g.canonical_name, fi.section, fi.deepdive_status
                from l2_feed_items fi
                join l2_candidate_groups g
                  on g.feed_run_id = fi.feed_run_id and g.group_id = fi.group_id
                where fi.feed_run_id = ?
                order by fi.section, fi.rank
                """,
                ("l2-routes",),
            ).fetchall()
            run_note = json.loads(
                conn.execute(
                    "select note from l2_feed_runs where feed_run_id = ?",
                    ("l2-routes",),
                ).fetchone()[0]
            )
            route_rows = conn.execute(
                """
                select g.canonical_name, e.metadata_json
                from l2_stage_events e
                join l2_candidate_groups g
                  on g.feed_run_id = e.feed_run_id and g.group_id = e.group_id
                where e.feed_run_id = ? and e.stage = ? and e.status = ?
                order by g.canonical_name
                """,
                ("l2-routes", "route", "route_decision"),
            ).fetchall()
            conn.close()

        self.assertEqual(summary["scored"], 4)
        self.assertEqual(summary["briefs"], 1)
        self.assertEqual(
            items,
            [
                ("d-news/repo", "diagnostics", "suppress_or_low"),
                ("c-weak/repo", "diagnostics", "suppress_or_low"),
                ("anthropics/claude-plugins-official", "scored", "score_only"),
                ("a-focus/repo", "today_focus", "briefed"),
            ],
        )
        self.assertEqual(
            run_note["route_counts"],
            {
                "score_plus_deepdive": 1,
                "score_only": 1,
                "suppress_or_low": 2,
                "candidate_error": 0,
            },
        )
        self.assertEqual(
            {name: json.loads(metadata)["route"] for name, metadata in route_rows},
            {
                "a-focus/repo": "score_plus_deepdive",
                "anthropics/claude-plugins-official": "score_only",
                "c-weak/repo": "suppress_or_low",
                "d-news/repo": "suppress_or_low",
            },
        )
        route_metadata = {name: json.loads(metadata) for name, metadata in route_rows}
        self.assertEqual(
            route_metadata["anthropics/claude-plugins-official"]["major_company"],
            "Anthropic",
        )
        self.assertEqual(
            route_metadata["anthropics/claude-plugins-official"]["route_reason"],
            "major_company_score_only",
        )

    def test_run_layer2_keeps_selected_focus_item_when_brief_fails(self):
        from pipeline.decision.llm_provider import FakeLLMProvider
        from pipeline.decision.run_layer2_feed import run_layer2_feed

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            self.make_db_with_potentials(db_path, ["focus/repo"])
            provider = FakeLLMProvider(
                [
                    self.valid_score_response("Focus"),
                    {"category": {"primary": "开发工具"}, "headline": ""},
                ]
            )

            summary = run_layer2_feed(
                db_path=db_path,
                decision_run_id="decision-run",
                feed_run_id="l2-brief-error",
                now="2026-06-01T12:00:00Z",
                provider=provider,
                config={"brief_min_score": 70, "brief_target_count": 1},
            )

            conn = sqlite3.connect(db_path)
            item = conn.execute(
                """
                select section, deepdive_status
                from l2_feed_items
                where feed_run_id = ?
                """,
                ("l2-brief-error",),
            ).fetchone()
            brief_count = conn.execute(
                "select count(*) from l2_deepdive_briefs where feed_run_id = ?",
                ("l2-brief-error",),
            ).fetchone()[0]
            conn.close()

        self.assertTrue(summary["ok"])
        self.assertEqual(summary["status"], "ok_with_errors")
        self.assertEqual(summary["briefs"], 0)
        self.assertEqual(item, ("today_focus", "brief_error"))
        self.assertEqual(brief_count, 0)

    def test_run_layer2_preserves_today_focus_ranks_after_briefs(self):
        from pipeline.decision.llm_provider import FakeLLMProvider
        from pipeline.decision.run_layer2_feed import run_layer2_feed

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            self.make_db_with_potentials(db_path, ["top/repo", "second/repo"])
            provider = FakeLLMProvider(
                [
                    self.valid_score_response("Top"),
                    self.valid_score_response("Second"),
                    self.valid_brief_response(),
                    self.valid_brief_response(),
                ]
            )

            run_layer2_feed(
                db_path=db_path,
                decision_run_id="decision-run",
                feed_run_id="l2-rank-stable",
                now="2026-06-01T12:00:00Z",
                provider=provider,
                config={
                    "brief_min_score": 70,
                    "brief_target_count": 2,
                    "brief_max_count": 2,
                },
            )

            conn = sqlite3.connect(db_path)
            ranks = conn.execute(
                """
                select rank
                from l2_feed_items
                where feed_run_id = ? and section = ?
                order by rank
                """,
                ("l2-rank-stable", "today_focus"),
            ).fetchall()
            conn.close()

        self.assertEqual([row[0] for row in ranks], [1, 2])

    def test_run_layer2_continues_when_one_scoring_candidate_fails(self):
        from pipeline.decision.llm_provider import FakeLLMProvider
        from pipeline.decision.run_layer2_feed import run_layer2_feed

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            conn = sqlite3.connect(db_path)
            init_decision_db(conn)
            conn.execute(
                "insert into decision_runs(run_id, source_snapshot_run_id, started_at, completed_at, status, config_hash, rule_version, note) values (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "decision-run",
                    "source-run",
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:01:00Z",
                    "ok",
                    "hash",
                    "rules-v1",
                    "",
                ),
            )
            for entity_id, name in [
                ("entity:bad", "bad/repo"),
                ("entity:good", "good/repo"),
            ]:
                conn.execute(
                    "insert into entities(entity_id, canonical_entity, canonical_key, key_type, first_seen, aliases_json, source_item_ids_json) values (?, ?, ?, ?, ?, ?, ?)",
                    (
                        entity_id,
                        name,
                        f"github:{name}",
                        "github",
                        "2026-06-01T00:00:00Z",
                        "[]",
                        "[]",
                    ),
                )
                conn.execute(
                    "insert into potential_candidates(entity_id, run_id, level, fired_families_json, first_trigger_at) values (?, ?, ?, ?, ?)",
                    (
                        entity_id,
                        "decision-run",
                        "potential",
                        '["github"]',
                        "2026-06-01T00:00:00Z",
                    ),
                )
            conn.commit()
            conn.close()

            provider = FakeLLMProvider(
                [
                    {"action": "use_tools", "tool_requests": []},
                    self.valid_score_response(),
                ]
            )

            summary = run_layer2_feed(
                db_path=db_path,
                decision_run_id="decision-run",
                feed_run_id="l2-errors",
                now="2026-06-01T12:00:00Z",
                provider=provider,
                config={
                    "max_investigation_turns": 1,
                    "enable_deepdive_briefs": False,
                },
            )

            self.assertTrue(summary["ok"])
            self.assertEqual(summary["status"], "ok_with_errors")
            self.assertEqual(summary["scored"], 1)
            self.assertEqual(summary["errors"], 1)

            conn = sqlite3.connect(db_path)
            run_row = conn.execute(
                "select status, note from l2_feed_runs where feed_run_id = ?",
                ("l2-errors",),
            ).fetchone()
            statuses = [
                row[0]
                for row in conn.execute(
                    "select status from l2_stage_events where feed_run_id = ? order by id",
                    ("l2-errors",),
                ).fetchall()
            ]
            diagnostic_count = conn.execute(
                """
                select count(*)
                from l2_feed_items
                where feed_run_id = ? and section = ? and deepdive_status = ?
                """,
                ("l2-errors", "diagnostics", "candidate_error"),
            ).fetchone()[0]
            route_errors = conn.execute(
                """
                select count(*)
                from l2_stage_events
                where feed_run_id = ? and stage = ? and status = ?
                  and metadata_json like ?
                """,
                ("l2-errors", "route", "route_decision", "%candidate_error%"),
            ).fetchone()[0]
            conn.close()

            self.assertEqual(run_row[0], "ok_with_errors")
            self.assertIn("scoring_error", statuses)
            self.assertIn("scoring_ok", statuses)
            self.assertIn("error_counts", run_row[1])
            self.assertEqual(diagnostic_count, 1)
            self.assertEqual(route_errors, 1)

    def test_run_layer2_applies_total_scoring_cap(self):
        from pipeline.decision.llm_provider import FakeLLMProvider
        from pipeline.decision.run_layer2_feed import run_layer2_feed

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            self.make_db_with_potentials(db_path, ["one/repo", "two/repo"])
            provider = FakeLLMProvider(
                [
                    self.valid_score_response("One"),
                    self.valid_score_response("Two"),
                ]
            )

            summary = run_layer2_feed(
                db_path=db_path,
                decision_run_id="decision-run",
                feed_run_id="l2-total-cap",
                now="2026-06-01T12:00:00Z",
                provider=provider,
                config={
                    "max_scored_candidates": 2,
                    "max_total_scoring_candidates": 1,
                    "enable_deepdive_briefs": False,
                },
            )

            conn = sqlite3.connect(db_path)
            stage_rows = conn.execute(
                "select stage, status from l2_stage_events where feed_run_id = ? order by id",
                ("l2-total-cap",),
            ).fetchall()
            conn.close()

        self.assertEqual(summary["scored"], 1)
        self.assertIn(("scoring", "pending_budget"), stage_rows)

    def test_run_layer2_scores_with_configured_concurrency_from_factory(self):
        from pipeline.decision.run_layer2_feed import run_layer2_feed

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            self.make_db_with_potentials(
                db_path,
                [
                    "one/repo",
                    "two/repo",
                    "three/repo",
                    "four/repo",
                    "five/repo",
                    "six/repo",
                ],
            )
            lock = threading.Lock()
            active = 0
            max_active = 0
            response = self.valid_score_response("Concurrent")

            class ConcurrentProvider:
                provider_name = "fake"
                model = "fake-json"

                def complete_json(self, **kwargs):
                    nonlocal active, max_active
                    with lock:
                        active += 1
                        max_active = max(max_active, active)
                    try:
                        time.sleep(0.05)
                        return response
                    finally:
                        with lock:
                            active -= 1

            summary = run_layer2_feed(
                db_path=db_path,
                decision_run_id="decision-run",
                feed_run_id="l2-concurrent",
                now="2026-06-01T12:00:00Z",
                scoring_provider_factory=ConcurrentProvider,
                config={
                    "max_scored_candidates": 6,
                    "scoring_concurrency": 3,
                    "enable_deepdive_briefs": False,
                },
            )

        self.assertEqual(summary["scored"], 6)
        self.assertGreaterEqual(max_active, 2)

    def test_run_layer2_disables_edge_scout_by_default(self):
        from pipeline.decision.llm_provider import FakeLLMProvider
        from pipeline.decision.run_layer2_feed import run_layer2_feed

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            self.make_db_with_candidates(db_path, [("edge/repo", "edge_watch")])
            provider = FakeLLMProvider([])

            summary = run_layer2_feed(
                db_path=db_path,
                decision_run_id="decision-run",
                feed_run_id="l2-scout-disabled",
                now="2026-06-01T12:00:00Z",
                provider=provider,
                config={"enable_deepdive_briefs": False},
            )

            conn = sqlite3.connect(db_path)
            stage_rows = conn.execute(
                "select stage, status from l2_stage_events where feed_run_id = ? order by id",
                ("l2-scout-disabled",),
            ).fetchall()
            scout_rows = conn.execute(
                "select count(*) from l2_scout_results where feed_run_id = ?",
                ("l2-scout-disabled",),
            ).fetchone()[0]
            conn.close()

        self.assertEqual(provider.calls, [])
        self.assertEqual(summary["scored"], 0)
        self.assertEqual(summary["errors"], 0)
        self.assertIn(("scout", "scout_disabled"), stage_rows)
        self.assertEqual(scout_rows, 0)

    def test_run_layer2_runs_edge_scout_when_enabled(self):
        from pipeline.decision.llm_provider import FakeLLMProvider
        from pipeline.decision.layer2_grouping import build_candidate_groups
        from pipeline.decision.run_layer2_feed import run_layer2_feed

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            self.make_db_with_candidates(db_path, [("edge/repo", "edge_watch")])
            conn = sqlite3.connect(db_path)
            group_id = build_candidate_groups(conn, decision_run_id="decision-run")[
                0
            ].group_id
            conn.close()
            provider = FakeLLMProvider(
                [
                    {
                        "promotions": [
                            {
                                "group_id": group_id,
                                "reason_code": "possible_workflow_shift",
                                "reason": "Worth scoring.",
                            }
                        ]
                    },
                    self.valid_score_response("Edge Scout Promotion"),
                ]
            )

            summary = run_layer2_feed(
                db_path=db_path,
                decision_run_id="decision-run",
                feed_run_id="l2-scout-enabled",
                now="2026-06-01T12:00:00Z",
                provider=provider,
                config={
                    "enable_edge_scout": True,
                    "enable_deepdive_briefs": False,
                },
            )

        self.assertEqual(summary["scored"], 1)
        self.assertEqual(
            [call["task"] for call in provider.calls],
            ["layer2_edge_scout", "layer2_scoring_investigator_turn"],
        )

    def test_run_layer2_marks_stale_running_runs_before_starting(self):
        from pipeline.decision.llm_provider import FakeLLMProvider
        from pipeline.decision.run_layer2_feed import run_layer2_feed

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            self.make_db_with_potentials(db_path, ["one/repo"])
            conn = sqlite3.connect(db_path)
            conn.execute(
                "insert into l2_feed_runs(feed_run_id, decision_run_id, started_at, completed_at, status, config_hash, model_profile_json, note) values (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "l2-stale",
                    "decision-run",
                    "2026-06-01T00:00:00Z",
                    None,
                    "running",
                    "manual",
                    "{}",
                    "",
                ),
            )
            conn.commit()
            conn.close()

            provider = FakeLLMProvider([self.valid_score_response()])
            run_layer2_feed(
                db_path=db_path,
                decision_run_id="decision-run",
                feed_run_id="l2-new",
                now="2026-06-01T12:00:00Z",
                provider=provider,
                config={
                    "enable_deepdive_briefs": False,
                    "finalize_stale_running_before": "2026-06-01T01:00:00Z",
                },
            )

            conn = sqlite3.connect(db_path)
            old_status, old_note = conn.execute(
                "select status, note from l2_feed_runs where feed_run_id = ?",
                ("l2-stale",),
            ).fetchone()
            conn.close()

        self.assertEqual(old_status, "error")
        self.assertIn("stale running", old_note)

    def test_cli_no_deepdive_and_timeout_knobs_map_to_config(self):
        from pipeline.decision.run_layer2_feed import config_from_args, parse_args

        args = parse_args(
            [
                "--no-deepdive",
                "--scout-timeout-seconds",
                "7",
                "--max-total-scoring-candidates",
                "2",
                "--score-only-min-score",
                "55",
            ]
        )
        config = config_from_args(args)

        self.assertFalse(config["enable_edge_scout"])
        self.assertEqual(config["max_deepdives_per_run"], 0)
        self.assertTrue(config["enable_deepdive_briefs"])
        self.assertEqual(config["scout_timeout_seconds"], 7)
        self.assertEqual(config["max_total_scoring_candidates"], 2)
        self.assertEqual(config["score_only_min_score"], 55)

        enabled = config_from_args(parse_args(["--enable-edge-scout"]))
        self.assertTrue(enabled["enable_edge_scout"])
        default_config = config_from_args(parse_args([]))
        self.assertEqual(default_config["max_deepdives_per_run"], 0)
        self.assertEqual(default_config["brief_target_count"], 8)
        self.assertEqual(default_config["score_only_min_score"], 50)
        self.assertEqual(default_config["scoring_concurrency"], 5)
