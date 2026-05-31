from __future__ import annotations

import json
import sqlite3
import unittest

from pipeline.decision.layer2_models import CandidateGroup
from pipeline.decision.llm_provider import FakeLLMProvider
from pipeline.decision.schema import init_decision_db


class Layer2DeepdiveTest(unittest.TestCase):
    def group(self, group_id: str, level: str) -> CandidateGroup:
        return CandidateGroup(
            group_id=group_id,
            canonical_entity_id=f"entity:{group_id}",
            canonical_name=group_id,
            canonical_key=f"name:{group_id}",
            canonical_link="",
            member_entity_ids=[f"entity:{group_id}"],
            level=level,
            source_families=["github"],
            evidence_hash=f"hash-{group_id}",
            context={"canonical_name": group_id, "readme_excerpt": "README context"},
        )

    def test_select_deepdives_caps_by_score(self):
        from pipeline.decision.layer2_deepdive import select_deepdives

        scored = [
            {"group": self.group("a", "potential"), "l2_score": 70},
            {"group": self.group("b", "high_potential"), "l2_score": 80},
            {"group": self.group("c", "edge_watch"), "l2_score": 90},
        ]

        selected = select_deepdives(scored, max_deepdives=2, min_l2_score=0)

        self.assertEqual([row["group"].group_id for row in selected], ["c", "b"])

    def test_run_deepdives_uses_bounded_plan_tools_and_synthesis(self):
        from pipeline.decision.layer2_deepdive import DeepdiveLimits, run_deepdives

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        provider = FakeLLMProvider(
            [
                {
                    "tool_requests": [
                        {
                            "name": "fetch_cached_readme",
                            "arguments": {"repo": "owner/repo"},
                        },
                        {
                            "name": "kimi_web_search",
                            "arguments": {"query": "owner/repo agent workflow"},
                        },
                    ]
                },
                {
                    "summary": "Project summary",
                    "why_now": "Moving today",
                    "what_changed": "New workflow",
                    "evidence": ["GitHub evidence"],
                    "adoption_path": "CLI users",
                    "risks": ["early"],
                    "open_questions": ["pricing"],
                    "recommended_action": "read",
                },
            ]
        )
        calls = []

        def readme_tool(arguments):
            calls.append(("fetch_cached_readme", arguments))
            return {"excerpt": "README says concrete agent workflow."}

        def search_tool(arguments):
            calls.append(("kimi_web_search", arguments))
            return {"results": [{"title": "Discussion", "url": "https://example.com"}]}

        group = self.group("a", "potential")
        run_deepdives(
            conn,
            feed_run_id="l2-run",
            scored=[{"group": group, "l2_score": 90}],
            provider=provider,
            max_deepdives=1,
            min_l2_score=0,
            tools={
                "fetch_cached_readme": readme_tool,
                "kimi_web_search": search_tool,
            },
            limits=DeepdiveLimits(max_tool_calls=2),
        )

        self.assertEqual(
            [name for name, _args in calls],
            ["fetch_cached_readme", "kimi_web_search"],
        )
        self.assertEqual(
            [call["task"] for call in provider.calls],
            ["layer2_deepdive_plan", "layer2_deepdive_synthesis"],
        )
        report = conn.execute(
            "select status, summary_json, tool_trace_json, provider from deepdive_reports"
        ).fetchone()
        self.assertEqual(report[0], "ok")
        self.assertIn("Project summary", report[1])
        trace = json.loads(report[2])
        self.assertEqual(len(trace), 2)
        self.assertEqual(trace[0]["tool"], "fetch_cached_readme")
        self.assertEqual(report[3], "fake")
        item = conn.execute(
            "select section, rank, deepdive_status from l2_feed_items"
        ).fetchone()
        self.assertEqual(item, ("today_focus", 1, "ok"))

    def test_default_tools_use_injected_web_search_client(self):
        from pipeline.decision.layer2_deepdive import default_deepdive_tools

        class SearchClient:
            def __init__(self):
                self.calls = []

            def search(self, *, query, max_results):
                self.calls.append({"query": query, "max_results": max_results})
                return {"results": [{"title": "Result"}]}

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        client = SearchClient()
        tools = default_deepdive_tools(
            conn,
            decision_run_id="decision-run",
            enable_kimi_web_search=True,
            web_search_client=client,
        )

        result = tools["kimi_web_search"]({"query": "owner/repo", "max_results": 3})

        self.assertEqual(result["results"][0]["title"], "Result")
        self.assertEqual(client.calls, [{"query": "owner/repo", "max_results": 3}])

    def test_deepdive_enforces_per_tool_family_budgets(self):
        from pipeline.decision.layer2_deepdive import DeepdiveLimits, run_deepdives

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        provider = FakeLLMProvider(
            [
                {
                    "tool_requests": [
                        {"name": "kimi_web_search", "arguments": {"query": "one"}},
                        {"name": "kimi_web_search", "arguments": {"query": "two"}},
                        {
                            "name": "fetch_github_file",
                            "arguments": {"repo": "owner/repo", "path": "README.md"},
                        },
                        {
                            "name": "fetch_github_file",
                            "arguments": {
                                "repo": "owner/repo",
                                "path": "package.json",
                            },
                        },
                    ]
                },
                {
                    "summary": "Project summary",
                    "why_now": "Moving today",
                    "what_changed": "New workflow",
                    "evidence": ["GitHub evidence"],
                    "adoption_path": "CLI users",
                    "risks": ["early"],
                    "open_questions": ["pricing"],
                    "recommended_action": "read",
                },
            ]
        )
        calls = []

        def record_tool(arguments):
            calls.append(arguments)
            return {"ok": True}

        run_deepdives(
            conn,
            feed_run_id="l2-run",
            scored=[{"group": self.group("budget", "potential"), "l2_score": 90}],
            provider=provider,
            max_deepdives=1,
            min_l2_score=0,
            tools={"kimi_web_search": record_tool, "fetch_github_file": record_tool},
            limits=DeepdiveLimits(
                max_tool_calls=10,
                max_web_search_calls=1,
                max_repo_file_calls=1,
            ),
        )

        self.assertEqual(len(calls), 2)
        trace = json.loads(
            conn.execute("select tool_trace_json from deepdive_reports").fetchone()[0]
        )
        self.assertEqual(
            [row["status"] for row in trace],
            ["ok", "budget_exceeded", "ok", "budget_exceeded"],
        )

    def test_default_tools_expose_minimum_real_deepdive_tools(self):
        from pipeline.decision.layer2_deepdive import default_deepdive_tools

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        tools = default_deepdive_tools(conn, decision_run_id="decision-run")

        self.assertEqual(
            sorted(tools),
            [
                "fetch_cached_readme",
                "fetch_github_file",
                "fetch_github_tree",
                "fetch_hn_thread",
                "fetch_homepage_or_docs",
                "fetch_package_manifest",
                "fetch_x_tweet_context",
                "kimi_web_search",
                "read_evidence_rows",
                "read_source_items",
            ],
        )
