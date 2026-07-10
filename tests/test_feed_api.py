from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock


class FeedApiTest(unittest.TestCase):
    def make_db(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        from pipeline.decision.schema import init_decision_db

        temp = tempfile.TemporaryDirectory()
        db_path = Path(temp.name) / "hero.sqlite"
        conn = sqlite3.connect(db_path)
        init_decision_db(conn)
        conn.execute(
            "insert into l2_feed_runs(feed_run_id, decision_run_id, started_at, completed_at, status, config_hash, model_profile_json, note) values (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "l2-run",
                "decision-run",
                "2026-05-31T00:00:00Z",
                "2026-05-31T00:01:00Z",
                "ok",
                "hash",
                json.dumps({"scout": "kimi-k2.5"}),
                "",
            ),
        )
        conn.execute(
            "insert into l2_candidate_groups(group_id, feed_run_id, canonical_entity_id, canonical_name, canonical_key, canonical_link, member_entity_ids_json, level, source_families_json, evidence_hash, grouping_reason_json, context_json) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "group:repo",
                "l2-run",
                "entity:repo",
                "owner/repo",
                "github:owner/repo",
                "https://github.com/owner/repo",
                '["entity:repo"]',
                "potential",
                '["github"]',
                "hash",
                "{}",
                json.dumps({"evidence_rows": []}),
            ),
        )
        conn.execute(
            "insert into l2_scores(feed_run_id, group_id, l2_score, axes_json, primary_reason, topic_tags_json, rationale_short, caveats_json, provider, model, prompt_version, cache_key) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "l2-run",
                "group:repo",
                88,
                json.dumps({"momentum": 80}),
                "Workflow Shift",
                '["agent workflow"]',
                "Worth reading.",
                "[]",
                "kimi",
                "kimi-k2.5",
                "v1",
                "cache",
            ),
        )
        conn.execute(
            "insert into deepdive_reports(feed_run_id, group_id, status, summary_json, tool_trace_json, provider, model, prompt_version, cache_key, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "l2-run",
                "group:repo",
                "ok",
                json.dumps({"summary": "Deep summary"}),
                "[]",
                "kimi",
                "kimi-k2.6",
                "v1",
                "cache",
                "2026-05-31T00:02:00Z",
            ),
        )
        conn.execute(
            "insert into l2_deepdive_briefs(feed_run_id, group_id, status, brief_json, language, provider, model, prompt_version, cache_key, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "l2-run",
                "group:repo",
                "ok",
                json.dumps(
                    {
                        "category": {
                            "primary": "开发工具",
                            "tags": ["agent", "repo"],
                        },
                        "headline": "owner/repo 值得今天重点看",
                        "core_highlights": ["把分散开发流程压到一个工具里。"],
                        "use_cases": ["开发者评估新的 agent workflow。"],
                        "caveat": "还需要验证真实使用留存。",
                    }
                ),
                "zh",
                "kimi",
                "kimi-k2.5",
                "v1",
                "brief-cache",
                "2026-05-31T00:02:00Z",
            ),
        )
        conn.execute(
            "insert into l2_feed_items(feed_run_id, group_id, section, rank, deepdive_status) values (?, ?, ?, ?, ?)",
            ("l2-run", "group:repo", "today_focus", 1, "briefed"),
        )
        conn.commit()
        conn.close()
        return temp, db_path

    def test_query_feed_payload_returns_today_focus(self):
        import pipeline.server as server

        temp, db_path = self.make_db()
        self.addCleanup(temp.cleanup)
        with mock.patch.object(server, "DB_PATH", db_path):
            payload = server.query_feed_payload()

        self.assertEqual(payload["feed_run_id"], "l2-run")
        self.assertEqual(payload["today_focus"][0]["group_id"], "group:repo")
        self.assertEqual(payload["today_focus"][0]["l2_score"], 88)
        self.assertEqual(
            payload["today_focus"][0]["deepdive"]["summary"], "Deep summary"
        )
        self.assertEqual(
            payload["today_focus"][0]["deepdive_brief"]["category"]["primary"],
            "开发工具",
        )
        self.assertEqual(
            payload["today_focus"][0]["deepdive_brief"]["headline"],
            "owner/repo 值得今天重点看",
        )

    def test_query_feed_payload_adds_structured_claims_with_text_projection(self):
        import pipeline.server as server

        temp, db_path = self.make_db()
        self.addCleanup(temp.cleanup)
        conn = sqlite3.connect(db_path)
        claims = [
            {
                "claim": "README documents a validation harness.",
                "evidence_refs": ["evidence:42"],
                "supports_axes": ["technical_substance"],
                "claim_type": "observed",
            }
        ]
        conn.execute(
            """
            update l2_scores
            set supporting_claims_json = ?, known_gaps_json = ?
            where feed_run_id = ? and group_id = ?
            """,
            (
                json.dumps(claims),
                json.dumps(["Adoption durability"]),
                "l2-run",
                "group:repo",
            ),
        )
        conn.commit()
        conn.close()

        with mock.patch.object(server, "DB_PATH", db_path):
            item = server.query_feed_payload()["today_focus"][0]

        self.assertEqual(item["supporting_claims"], claims)
        self.assertEqual(
            item["supporting_evidence"],
            ["README documents a validation harness."],
        )
        self.assertEqual(item["known_gaps"], ["Adoption durability"])

    def test_query_feed_payload_exposes_major_company_label(self):
        import pipeline.server as server

        temp, db_path = self.make_db()
        self.addCleanup(temp.cleanup)
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            update l2_candidate_groups
            set canonical_name = ?, canonical_key = ?, canonical_link = ?
            where feed_run_id = ? and group_id = ?
            """,
            (
                "anthropics/claude-plugins-official",
                "github:anthropics/claude-plugins-official",
                "https://github.com/anthropics/claude-plugins-official",
                "l2-run",
                "group:repo",
            ),
        )
        conn.commit()
        conn.close()

        with mock.patch.object(server, "DB_PATH", db_path):
            payload = server.query_feed_payload()

        self.assertEqual(payload["today_focus"][0]["major_company"], "Anthropic")

    def test_query_feed_payload_returns_diagnostics_section(self):
        import pipeline.server as server

        temp, db_path = self.make_db()
        self.addCleanup(temp.cleanup)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "insert into l2_feed_items(feed_run_id, group_id, section, rank, deepdive_status) values (?, ?, ?, ?, ?)",
            ("l2-run", "group:repo", "diagnostics", 1, "candidate_error"),
        )
        conn.commit()
        conn.close()

        with mock.patch.object(server, "DB_PATH", db_path):
            payload = server.query_feed_payload()

        self.assertEqual(payload["diagnostics"][0]["group_id"], "group:repo")
        self.assertEqual(payload["diagnostics"][0]["deepdive_status"], "candidate_error")

    def test_query_feed_payload_maps_legacy_suppressed_diagnostics_to_scored_list(self):
        import pipeline.server as server

        temp, db_path = self.make_db()
        self.addCleanup(temp.cleanup)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "insert into l2_candidate_groups(group_id, feed_run_id, canonical_entity_id, canonical_name, canonical_key, canonical_link, member_entity_ids_json, level, source_families_json, evidence_hash, grouping_reason_json, context_json) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "group:low",
                "l2-run",
                "entity:low",
                "low/repo",
                "github:low/repo",
                "https://github.com/low/repo",
                '["entity:low"]',
                "potential",
                '["github"]',
                "low-hash",
                "{}",
                json.dumps({"evidence_rows": []}),
            ),
        )
        conn.execute(
            "insert into l2_scores(feed_run_id, group_id, l2_score, axes_json, primary_reason, topic_tags_json, rationale_short, caveats_json, provider, model, prompt_version, cache_key) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "l2-run",
                "group:low",
                34,
                json.dumps({"momentum": 20}),
                "Low Signal",
                '["utility"]',
                "Low confidence utility.",
                "[]",
                "kimi",
                "kimi-k2.5",
                "v1",
                "low-cache",
            ),
        )
        conn.execute(
            "insert into l2_feed_items(feed_run_id, group_id, section, rank, deepdive_status) values (?, ?, ?, ?, ?)",
            ("l2-run", "group:repo", "scored", 1, "score_only"),
        )
        conn.execute(
            "insert into l2_feed_items(feed_run_id, group_id, section, rank, deepdive_status) values (?, ?, ?, ?, ?)",
            ("l2-run", "group:low", "diagnostics", 1, "suppress_or_low"),
        )
        conn.commit()
        conn.close()

        with mock.patch.object(server, "DB_PATH", db_path):
            payload = server.query_feed_payload()

        self.assertEqual(payload["diagnostics"], [])
        self.assertEqual(payload["scored_list"][0]["group_id"], "group:repo")
        self.assertEqual(payload["scored_list"][0]["deepdive_status"], "score_only")
        self.assertEqual(payload["scored_list"][1]["group_id"], "group:low")
        self.assertEqual(payload["scored_list"][1]["deepdive_status"], "suppress_or_low")

    def test_query_feed_payload_can_select_explicit_run(self):
        import pipeline.server as server

        temp, db_path = self.make_db()
        self.addCleanup(temp.cleanup)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "insert into l2_feed_runs(feed_run_id, decision_run_id, started_at, completed_at, status, config_hash, model_profile_json, note) values (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "l2-newer",
                "decision-run",
                "2026-06-01T00:00:00Z",
                "2026-06-01T00:01:00Z",
                "ok",
                "hash",
                "{}",
                "",
            ),
        )
        conn.commit()
        conn.close()

        with mock.patch.object(server, "DB_PATH", db_path):
            latest = server.query_feed_payload()
            explicit = server.query_feed_payload(feed_run_id="l2-run")

        self.assertEqual(latest["feed_run_id"], "l2-newer")
        self.assertEqual(explicit["feed_run_id"], "l2-run")

    def test_query_feed_payload_exposes_run_status_and_stage_telemetry(self):
        import pipeline.server as server

        temp, db_path = self.make_db()
        self.addCleanup(temp.cleanup)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "update l2_feed_runs set status = ?, note = ? where feed_run_id = ?",
            (
                "ok_with_errors",
                json.dumps(
                    {
                        "error_counts": {"scoring": 1},
                        "stage_counts": {"scoring_error": 1},
                    }
                ),
                "l2-run",
            ),
        )
        conn.execute(
            """
            insert into l2_stage_events(
              feed_run_id, group_id, stage, status, error_type, error, metadata_json, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "l2-run",
                "group:repo",
                "scoring",
                "scoring_error",
                "ValueError",
                "bad score",
                json.dumps({"attempt": 1}),
                "2026-05-31T00:00:30Z",
            ),
        )
        conn.commit()
        conn.close()

        with mock.patch.object(server, "DB_PATH", db_path):
            payload = server.query_feed_payload()

        self.assertEqual(payload["run_status"], "ok_with_errors")
        self.assertEqual(payload["telemetry"]["error_counts"]["scoring"], 1)
        self.assertEqual(payload["stage_events"][0]["status"], "scoring_error")
        self.assertEqual(payload["stage_events"][0]["metadata"], {"attempt": 1})

    def test_record_feed_feedback_upserts_vote(self):
        import pipeline.server as server

        temp, db_path = self.make_db()
        self.addCleanup(temp.cleanup)
        with mock.patch.object(server, "DB_PATH", db_path):
            server.record_feed_feedback(
                {"feed_run_id": "l2-run", "group_id": "group:repo", "vote": "up"}
            )
            server.record_feed_feedback(
                {"feed_run_id": "l2-run", "group_id": "group:repo", "vote": "down"}
            )

        conn = sqlite3.connect(db_path)
        row = conn.execute("select vote from feed_feedback").fetchone()
        conn.close()
        self.assertEqual(row[0], "down")

    def test_root_serves_react_dist_when_available(self):
        import pipeline.server as server

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dist = root / "dist"
            dist.mkdir()
            (dist / "index.html").write_text("<html>React Feed App</html>")
            legacy = root / "dashboard.html"
            legacy.write_text("<html>Legacy Dashboard</html>")
            with (
                mock.patch.object(server, "WEB_DIST_PATH", dist),
                mock.patch.object(server, "DASHBOARD_PATH", legacy),
            ):
                httpd = server.ThreadingHTTPServer(
                    ("127.0.0.1", 0),
                    server.HeroRadarHandler,
                )
                self.addCleanup(httpd.server_close)
                thread = threading.Thread(target=httpd.handle_request)
                thread.start()
                url = (
                    f"http://127.0.0.1:{httpd.server_port}/"
                    "?section=feed&feed=daily"
                )
                body = urllib.request.urlopen(url, timeout=5).read().decode("utf-8")
                thread.join(timeout=5)

        self.assertIn("React Feed App", body)
        self.assertNotIn("Legacy Dashboard", body)
