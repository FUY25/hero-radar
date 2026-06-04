from __future__ import annotations

import json
import sqlite3
import unittest

from pipeline.decision.layer2_models import CandidateGroup
from pipeline.decision.llm_provider import FakeLLMProvider
from pipeline.decision.schema import init_decision_db


class CountingTool:
    def __init__(self, response: dict):
        self.response = response
        self.calls: list[dict] = []

    def __call__(self, arguments: dict) -> dict:
        self.calls.append(arguments)
        return self.response


def make_group(level: str = "potential") -> CandidateGroup:
    return CandidateGroup(
        group_id="group:repo",
        canonical_entity_id="entity:repo",
        canonical_name="owner/repo",
        canonical_key="github:owner/repo",
        canonical_link="https://github.com/owner/repo",
        member_entity_ids=["entity:repo"],
        level=level,
        source_families=["github"],
        evidence_hash="evidence-hash",
        context={
            "members": [
                {
                    "entity_id": "entity:repo",
                    "context_preview": "Repo description",
                    "evidence_bullets": [{"label": "GH +321 stars / 24h"}],
                }
            ]
        },
    )


def final_response(**axis_overrides):
    axes = {
        "workflow_shift": 82,
        "technical_substance": 88,
        "product_market_fit": 76,
        "momentum": 83,
        "confidence": 81,
        "risk_penalty": 5,
        "derivative_news_penalty": 0,
    }
    axes.update(axis_overrides)
    return {
        "action": "final",
        "score": {
            "object_type": "repo",
            "is_product_or_repo": True,
            "axes": axes,
            "supporting_evidence": ["README shows a validation harness."],
            "negative_evidence": [],
            "known_gaps": [],
            "primary_reason": "Validation harness",
            "rationale_short": "The repo has workflow and technical substance.",
            "topic_tags": ["agent tooling"],
            "caveats": [],
            "should_print": True,
        },
        "brief": {
            "should_print": True,
            "headline": "值得看",
        },
    }


class Layer2ScoringInvestigatorTest(unittest.TestCase):
    def make_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        self.addCleanup(conn.close)
        return conn

    def test_investigator_uses_tool_then_persists_score_and_trace(self):
        from pipeline.decision.layer2_scoring_investigator import (
            score_with_investigator,
        )

        conn = self.make_conn()
        provider = FakeLLMProvider(
            [
                {
                    "action": "use_tools",
                    "information_need": "Need README evidence.",
                    "tool_requests": [
                        {
                            "name": "fetch_github_readme",
                            "arguments": {"repo_key": "owner/repo"},
                        }
                    ],
                },
                final_response(),
            ]
        )
        readme_tool = CountingTool({"status": "ok", "excerpt": "README evidence"})

        results = score_with_investigator(
            conn,
            feed_run_id="l2-run",
            groups=[make_group()],
            provider=provider,
            tools={"fetch_github_readme": readme_tool},
        )

        self.assertEqual(readme_tool.calls, [{"repo_key": "owner/repo"}])
        self.assertEqual(results[0]["primary_reason"], "Validation harness")
        self.assertGreater(results[0]["l2_score"], 70)
        self.assertEqual(
            [call["task"] for call in provider.calls],
            [
                "layer2_scoring_investigator_turn",
                "layer2_scoring_investigator_turn",
            ],
        )
        score_row = conn.execute(
            "select l2_score, primary_reason, prompt_version from l2_scores"
        ).fetchone()
        self.assertEqual(score_row[1], "Validation harness")
        self.assertEqual(score_row[2], "layer2-scoring-investigator-v1")
        trace_row = conn.execute(
            "select status, trace_json, tool_trace_json from l2_scoring_investigations"
        ).fetchone()
        self.assertEqual(trace_row[0], "ok")
        self.assertIn("Need README evidence", trace_row[1])
        self.assertEqual(json.loads(trace_row[2])[0]["status"], "ok")

    def test_investigator_enforces_web_search_budget(self):
        from pipeline.decision.layer2_scoring_investigator import (
            InvestigatorLimits,
            score_with_investigator,
        )

        conn = self.make_conn()
        provider = FakeLLMProvider(
            [
                {
                    "action": "use_tools",
                    "information_need": "Need web evidence.",
                    "tool_requests": [
                        {"name": "web_search", "arguments": {"query": "one"}},
                        {"name": "web_search", "arguments": {"query": "two"}},
                    ],
                },
                final_response(),
            ]
        )
        web_tool = CountingTool({"status": "ok", "results": []})

        score_with_investigator(
            conn,
            feed_run_id="l2-run",
            groups=[make_group()],
            provider=provider,
            tools={"web_search": web_tool},
            limits=InvestigatorLimits(max_web_search_calls_per_candidate=1),
        )

        self.assertEqual(web_tool.calls, [{"query": "one"}])
        tool_trace = json.loads(
            conn.execute(
                "select tool_trace_json from l2_scoring_investigations"
            ).fetchone()[0]
        )
        self.assertEqual(tool_trace[1]["status"], "budget_exceeded")

    def test_investigator_repairs_invalid_final_score_once(self):
        from pipeline.decision.layer2_scoring_investigator import (
            score_with_investigator,
        )

        conn = self.make_conn()
        provider = FakeLLMProvider(
            [
                {"action": "final", "score": {"primary_reason": "bad"}},
                final_response(),
            ]
        )

        results = score_with_investigator(
            conn,
            feed_run_id="l2-run",
            groups=[make_group()],
            provider=provider,
            tools={},
        )

        self.assertEqual(results[0]["primary_reason"], "Validation harness")
        self.assertEqual(
            [call["task"] for call in provider.calls],
            [
                "layer2_scoring_investigator_turn",
                "layer2_scoring_investigator_repair",
            ],
        )

    def test_score_caps_weak_core_axes_and_news_objects(self):
        from pipeline.decision.layer2_scoring_investigator import (
            aggregate_investigator_score,
        )

        weak_core = aggregate_investigator_score(
            {
                "workflow_shift": 60,
                "technical_substance": 55,
                "product_market_fit": 60,
                "momentum": 100,
                "confidence": 100,
                "risk_penalty": 0,
                "derivative_news_penalty": 0,
            },
            object_type="repo",
            is_product_or_repo=True,
        )
        news = aggregate_investigator_score(
            {
                "workflow_shift": 95,
                "technical_substance": 80,
                "product_market_fit": 75,
                "momentum": 100,
                "confidence": 90,
                "risk_penalty": 0,
                "derivative_news_penalty": 0,
            },
            object_type="news",
            is_product_or_repo=False,
        )

        self.assertEqual(weak_core, 69)
        self.assertEqual(news, 55)


if __name__ == "__main__":
    unittest.main()
