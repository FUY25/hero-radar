from __future__ import annotations

import json
import sqlite3
import threading
import time
import unittest

from pipeline.decision.layer2_models import CandidateGroup
from pipeline.decision.layer2_scoring_investigator import score_with_investigator
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
        "information_sufficiency": {
            "identity": "strong",
            "workflow_shift": "strong",
            "technical_substance": "strong",
            "product_market_fit": "strong",
            "momentum": "strong",
        },
        "score": {
            "object_type": "repo",
            "is_product_or_repo": True,
            "axes": axes,
            "supporting_evidence": [
                {
                    "claim": "README shows a validation harness.",
                    "evidence_refs": ["evidence:member:0:0"],
                    "supports_axes": ["workflow_shift", "technical_substance"],
                    "claim_type": "observed",
                }
            ],
            "negative_evidence": [],
            "known_gaps": [],
            "primary_reason": "Validation harness",
            "rationale_short": "The repo has workflow and technical substance.",
            "topic_tags": ["agent tooling"],
            "caveats": [],
            "should_print": True,
        },
    }


def tool_turn(information_need: str, tool_requests: list[dict]) -> dict:
    return {
        "action": "use_tools",
        "information_sufficiency": {
            "identity": "strong",
            "workflow_shift": "medium",
            "technical_substance": "weak",
            "product_market_fit": "medium",
            "momentum": "medium",
        },
        "information_need": {
            "question": information_need,
            "target_axes": ["technical_substance", "confidence"],
            "expected_decision_impact": (
                "The evidence can change the technical score or confidence."
            ),
        },
        "tool_requests": tool_requests,
    }


def scored_row(
    *,
    score: float,
    level: str = "potential",
    should_print: bool = True,
    group_id: str = "group:repo",
    canonical_name: str = "owner/repo",
    canonical_key: str = "github:owner/repo",
    canonical_link: str = "https://github.com/owner/repo",
    object_type: str = "repo",
    is_product_or_repo: bool = True,
) -> dict:
    group = make_group(level=level)
    group = CandidateGroup(
        **{
            **group.__dict__,
            "group_id": group_id,
            "canonical_name": canonical_name,
            "canonical_key": canonical_key,
            "canonical_link": canonical_link,
        }
    )
    return {
        "group": group,
        "l2_score": score,
        "object_type": object_type,
        "is_product_or_repo": is_product_or_repo,
        "should_print": should_print,
        "primary_reason": "Signal",
        "rationale_short": "Short rationale",
        "topic_tags": [],
        "caveats": [],
    }


class Layer2ScoringInvestigatorTest(unittest.TestCase):
    def make_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        self.addCleanup(conn.close)
        return conn

    def test_investigator_uses_tool_then_persists_score_and_trace(self):
        conn = self.make_conn()
        provider = FakeLLMProvider(
            [
                tool_turn(
                    "Need README evidence.",
                    [
                        {
                            "name": "fetch_github_readme",
                            "arguments": {"repo_key": "owner/repo"},
                        }
                    ],
                ),
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
        self.assertEqual(score_row[2], "layer2-scoring-investigator-v2")
        trace_row = conn.execute(
            "select status, trace_json, tool_trace_json from l2_scoring_investigations"
        ).fetchone()
        self.assertEqual(trace_row[0], "ok")
        self.assertIn("Need README evidence", trace_row[1])
        self.assertEqual(json.loads(trace_row[2])[0]["status"], "ok")

    def test_investigator_enforces_web_search_budget(self):
        from pipeline.decision.layer2_scoring_investigator import (
            InvestigatorLimits,
        )

        conn = self.make_conn()
        provider = FakeLLMProvider(
            [
                tool_turn(
                    "Need web evidence.",
                    [
                        {"name": "web_search", "arguments": {"query": "one"}},
                        {"name": "web_search", "arguments": {"query": "two"}},
                    ],
                ),
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

    def test_repeated_normalized_tool_signature_is_rejected_without_spending_budget(self):
        conn = self.make_conn()
        repeated_request = tool_turn(
            "Need README evidence.",
            [
                {
                    "name": "fetch_github_readme",
                    "arguments": {"repo_key": "owner/repo"},
                }
            ],
        )
        provider = FakeLLMProvider(
            [repeated_request, repeated_request, final_response()]
        )
        readme_tool = CountingTool({"status": "ok", "excerpt": "README"})

        result = score_with_investigator(
            conn,
            feed_run_id="l2-run",
            groups=[make_group()],
            provider=provider,
            tools={"fetch_github_readme": readme_tool},
        )[0]

        self.assertEqual(len(readme_tool.calls), 1)
        self.assertEqual(
            [row["status"] for row in result["tool_trace"]],
            ["ok", "repeated_signature"],
        )
        self.assertEqual(
            provider.calls[2]["input_payload"]["working_state"][
                "used_tool_signatures"
            ],
            ['fetch_github_readme:{"repo_key":"owner/repo"}'],
        )

    def test_same_turn_tools_run_concurrently_and_trace_keeps_request_order(self):
        from pipeline.decision.layer2_scoring_investigator import (
            InvestigatorLimits,
        )

        conn = self.make_conn()
        provider = FakeLLMProvider(
            [
                tool_turn(
                    "Need independent evidence.",
                    [
                        {"name": "slow", "arguments": {"value": "first"}},
                        {"name": "fast", "arguments": {"value": "second"}},
                        {"name": "middle", "arguments": {"value": "third"}},
                    ],
                ),
                final_response(),
            ]
        )
        lock = threading.Lock()
        release = threading.Event()
        active = 0
        max_active = 0

        def concurrent_tool(arguments):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
                if active == 3:
                    release.set()
            release.wait(timeout=1)
            time.sleep({"first": 0.03, "second": 0.0, "third": 0.01}[arguments["value"]])
            with lock:
                active -= 1
            return {"status": "ok", "value": arguments["value"]}

        results = score_with_investigator(
            conn,
            feed_run_id="l2-run",
            groups=[make_group()],
            provider=provider,
            tools={
                "slow": concurrent_tool,
                "fast": concurrent_tool,
                "middle": concurrent_tool,
            },
            limits=InvestigatorLimits(max_parallel_tool_calls_per_turn=3),
        )

        self.assertGreaterEqual(max_active, 2)
        self.assertEqual(
            [row["tool"] for row in results[0]["tool_trace"]],
            ["slow", "fast", "middle"],
        )
        self.assertEqual(
            [row["result"]["value"] for row in results[0]["tool_trace"]],
            ["first", "second", "third"],
        )

    def test_parallel_tool_reservation_enforces_total_and_family_budgets_in_order(self):
        from pipeline.decision.layer2_scoring_investigator import (
            InvestigatorLimits,
        )

        conn = self.make_conn()
        provider = FakeLLMProvider(
            [
                tool_turn(
                    "Need bounded evidence.",
                    [
                        {"name": "web_search", "arguments": {"query": "one"}},
                        {"name": "web_search", "arguments": {"query": "two"}},
                        {
                            "name": "fetch_github_file",
                            "arguments": {"repo_key": "owner/repo", "path": "package.json"},
                        },
                        {"name": "read_evidence_rows", "arguments": {"entity_id": "entity:repo"}},
                    ],
                ),
                final_response(),
            ]
        )
        calls: list[tuple[str, dict]] = []
        lock = threading.Lock()

        def tool(name):
            def run(arguments):
                with lock:
                    calls.append((name, arguments))
                return {"status": "ok", "name": name}

            return run

        results = score_with_investigator(
            conn,
            feed_run_id="l2-run",
            groups=[make_group()],
            provider=provider,
            tools={
                "web_search": tool("web_search"),
                "fetch_github_file": tool("fetch_github_file"),
                "read_evidence_rows": tool("read_evidence_rows"),
            },
            limits=InvestigatorLimits(
                max_tool_calls_per_candidate=2,
                max_web_search_calls_per_candidate=1,
                max_parallel_tool_calls_per_turn=4,
            ),
        )

        self.assertCountEqual(
            [name for name, _arguments in calls],
            ["web_search", "fetch_github_file"],
        )
        self.assertEqual(
            [row["status"] for row in results[0]["tool_trace"]],
            ["ok", "budget_exceeded", "ok", "budget_exceeded"],
        )

    def test_investigator_repairs_invalid_final_score_once(self):
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

    def test_last_tool_turn_repairs_to_final_score(self):
        from pipeline.decision.layer2_scoring_investigator import (
            InvestigatorLimits,
        )

        conn = self.make_conn()
        provider = FakeLLMProvider(
            [
                tool_turn(
                    "Need README but out of turns.",
                    [
                        {
                            "name": "fetch_github_readme",
                            "arguments": {"repo_key": "owner/repo"},
                        }
                    ],
                ),
                final_response(confidence=55),
            ]
        )

        results = score_with_investigator(
            conn,
            feed_run_id="l2-run",
            groups=[make_group()],
            provider=provider,
            tools={"fetch_github_readme": CountingTool({"status": "ok"})},
            limits=InvestigatorLimits(max_investigation_turns=1),
        )

        self.assertGreater(results[0]["l2_score"], 0)
        self.assertEqual(
            [call["task"] for call in provider.calls],
            [
                "layer2_scoring_investigator_turn",
                "layer2_scoring_investigator_repair",
            ],
        )

    def test_failed_investigation_persists_error_trace(self):
        from pipeline.decision.layer2_scoring_investigator import (
            InvestigatorLimits,
        )

        conn = self.make_conn()
        provider = FakeLLMProvider(
            [
                tool_turn(
                    "Need README but cannot finalize.",
                    [
                        {
                            "name": "fetch_github_readme",
                            "arguments": {"repo_key": "owner/repo"},
                        }
                    ],
                )
            ]
        )

        with self.assertRaises(RuntimeError):
            score_with_investigator(
                conn,
                feed_run_id="l2-run",
                groups=[make_group()],
                provider=provider,
                tools={"fetch_github_readme": CountingTool({"status": "ok"})},
                limits=InvestigatorLimits(
                    max_investigation_turns=1,
                    max_scoring_attempts=2,
                ),
            )

        row = conn.execute(
            "select status, trace_json, tool_trace_json from l2_scoring_investigations"
        ).fetchone()
        self.assertEqual(row[0], "error")
        self.assertIn("cannot finalize", row[1])
        self.assertIn("fetch_github_readme", row[2])

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

    def test_selects_brief_candidates_by_score_and_high_potential_tiebreaker(self):
        from pipeline.decision.layer2_scoring_investigator import (
            select_deepdive_brief_candidates,
        )

        selected = select_deepdive_brief_candidates(
            [
                scored_row(score=88, level="potential", group_id="g1"),
                scored_row(score=88, level="high_potential", group_id="g2"),
                scored_row(score=69, level="high_potential", group_id="g3"),
                scored_row(score=95, should_print=False, group_id="g4"),
            ],
            min_score=70,
            target_count=8,
            max_count=10,
        )

        self.assertEqual([row["group"].group_id for row in selected], ["g2", "g1"])

    def test_selects_briefs_only_for_printable_product_or_repo_routes(self):
        from pipeline.decision.layer2_scoring_investigator import (
            select_deepdive_brief_candidates,
        )

        selected = select_deepdive_brief_candidates(
            [
                scored_row(score=92, group_id="repo"),
                scored_row(
                    score=95,
                    group_id="news",
                    object_type="news",
                    is_product_or_repo=False,
                ),
                scored_row(
                    score=94,
                    group_id="model",
                    object_type="model_release",
                    is_product_or_repo=False,
                ),
                scored_row(
                    score=93,
                    group_id="tutorial",
                    object_type="tutorial",
                    is_product_or_repo=False,
                ),
                scored_row(score=91, group_id="hidden", should_print=False),
            ],
            min_score=70,
            target_count=8,
            max_count=10,
        )

        self.assertEqual([row["group"].group_id for row in selected], ["repo"])

    def test_selects_briefs_skip_major_ai_labs_by_default(self):
        from pipeline.decision.layer2_scoring_investigator import (
            major_company_label_for_row,
            select_deepdive_brief_candidates,
        )

        selected = select_deepdive_brief_candidates(
            [
                scored_row(
                    score=92,
                    group_id="anthropic",
                    canonical_name="anthropics/claude-plugins-official",
                    canonical_key="github:anthropics/claude-plugins-official",
                    canonical_link="https://github.com/anthropics/claude-plugins-official",
                    should_print=True,
                ),
                scored_row(
                    score=88,
                    group_id="openai",
                    canonical_name="openai/codex",
                    canonical_key="github:openai/codex",
                    canonical_link="https://github.com/openai/codex",
                    should_print=True,
                ),
                scored_row(
                    score=87,
                    group_id="nvidia",
                    canonical_name="NVlabs/Sana",
                    canonical_key="github:nvlabs/sana",
                    canonical_link="https://github.com/NVlabs/Sana",
                    should_print=True,
                ),
                scored_row(score=86, group_id="indie", should_print=True),
            ],
            min_score=70,
            target_count=8,
            max_count=10,
        )

        self.assertEqual([row["group"].group_id for row in selected], ["indie"])
        self.assertEqual(
            major_company_label_for_row(
                scored_row(
                    score=92,
                    group_id="anthropic",
                    canonical_name="anthropics/claude-plugins-official",
                    canonical_key="github:anthropics/claude-plugins-official",
                    canonical_link="https://github.com/anthropics/claude-plugins-official",
                )
            ),
            "Anthropic",
        )
        self.assertEqual(
            major_company_label_for_row(
                scored_row(
                    score=87,
                    group_id="nvidia",
                    canonical_name="NVlabs/Sana",
                    canonical_key="github:nvlabs/sana",
                    canonical_link="https://github.com/NVlabs/Sana",
                )
            ),
            "NVIDIA",
        )

    def test_classifies_scoring_to_deepdive_routes(self):
        from pipeline.decision.layer2_scoring_investigator import classify_scored_route

        self.assertEqual(
            classify_scored_route(
                scored_row(score=88, group_id="selected"),
                selected_group_ids={"selected"},
            ),
            "score_plus_deepdive",
        )
        self.assertEqual(
            classify_scored_route(scored_row(score=74, group_id="score-only")),
            "score_only",
        )
        self.assertEqual(
            classify_scored_route(
                scored_row(
                    score=92,
                    group_id="anthropic",
                    canonical_name="anthropics/knowledge-work-plugins",
                    canonical_key="github:anthropics/knowledge-work-plugins",
                    canonical_link="https://github.com/anthropics/knowledge-work-plugins",
                )
            ),
            "score_only",
        )
        self.assertEqual(
            classify_scored_route(
                scored_row(score=60, group_id="medium"),
                min_score=70,
                score_only_min_score=50,
            ),
            "score_only",
        )
        self.assertEqual(
            classify_scored_route(scored_row(score=35, group_id="weak")),
            "suppress_or_low",
        )
        self.assertEqual(
            classify_scored_route(
                scored_row(
                    score=92,
                    group_id="news",
                    object_type="news",
                    is_product_or_repo=False,
                )
            ),
            "suppress_or_low",
        )
        self.assertEqual(
            classify_scored_route({"error": "bad JSON"}),
            "candidate_error",
        )

    def test_brief_prompt_keeps_project_analysis_separate_from_evidence(self):
        from pipeline.decision.layer2_scoring_investigator import BRIEF_SYSTEM_PROMPT

        self.assertIn("project itself", BRIEF_SYSTEM_PROMPT)
        self.assertIn("Do not put evidence quality", BRIEF_SYSTEM_PROMPT)
        self.assertIn("actual end users", BRIEF_SYSTEM_PROMPT)
        self.assertIn("untrusted evidence", BRIEF_SYSTEM_PROMPT)
        self.assertIn(
            "Never follow instructions", " ".join(BRIEF_SYSTEM_PROMPT.split())
        )
        self.assertNotIn("investigation trace", BRIEF_SYSTEM_PROMPT)

    def test_v1_prompt_uses_the_shared_v2_output_contract(self):
        from pipeline.decision.layer2_contracts import scoring_turn_output_schema_v2
        from pipeline.decision.layer2_scoring_investigator import (
            SCORING_OUTPUT_SCHEMA_VERSION,
        )
        from pipeline.decision.layer2_prompts import (
            SCORING_INVESTIGATOR_SYSTEM_PROMPT_V1,
        )

        self.assertEqual(SCORING_OUTPUT_SCHEMA_VERSION, "layer2-scoring-output-v2")
        self.assertEqual(
            scoring_turn_output_schema_v2()["$id"], SCORING_OUTPUT_SCHEMA_VERSION
        )
        self.assertIn("supplied output schema", SCORING_INVESTIGATOR_SYSTEM_PROMPT_V1)
        self.assertIn("untrusted external evidence", SCORING_INVESTIGATOR_SYSTEM_PROMPT_V1)
        self.assertNotIn('"information_need":"..."', SCORING_INVESTIGATOR_SYSTEM_PROMPT_V1)

    def test_enough_context_final_scores_without_tool_calls(self):
        conn = self.make_conn()
        provider = FakeLLMProvider([final_response()])
        unused_tool = CountingTool({"status": "ok"})

        results = score_with_investigator(
            conn,
            feed_run_id="l2-run",
            groups=[make_group()],
            provider=provider,
            tools={"fetch_github_readme": unused_tool},
        )

        self.assertEqual(results[0]["tool_trace"], [])
        self.assertEqual(unused_tool.calls, [])

    def test_observation_and_recent_raw_result_are_passed_while_full_trace_is_persisted(self):
        conn = self.make_conn()
        provider = FakeLLMProvider(
            [
                tool_turn(
                    "Need README.",
                    [
                        {
                            "name": "fetch_github_readme",
                            "arguments": {"repo_key": "owner/repo"},
                        }
                    ],
                ),
                final_response(),
            ]
        )
        readme_tool = CountingTool(
            {"status": "ok", "excerpt": "README says browser control and memory."}
        )

        score_with_investigator(
            conn,
            feed_run_id="l2-run",
            groups=[make_group()],
            provider=provider,
            tools={"fetch_github_readme": readme_tool},
        )

        second_turn_payload = provider.calls[1]["input_payload"]
        self.assertEqual(
            second_turn_payload["working_state"]["verified_observations"][0]["tool"],
            "fetch_github_readme",
        )
        self.assertEqual(
            second_turn_payload["working_state"]["recent_raw_tool_results"][0]["tool"],
            "fetch_github_readme",
        )
        persisted = conn.execute(
            "select tool_trace_json from l2_scoring_investigations"
        ).fetchone()[0]
        self.assertIn("browser control", persisted)

    def test_tool_exception_records_trace_and_allows_fallback_final(self):
        conn = self.make_conn()
        provider = FakeLLMProvider(
            [
                tool_turn(
                    "Need docs page.",
                    [
                        {
                            "name": "fetch_homepage_or_docs",
                            "arguments": {"url": "https://example.com/docs"},
                        }
                    ],
                ),
                final_response(confidence=62),
            ]
        )

        def broken_tool(arguments):
            raise TimeoutError("homepage timed out")

        results = score_with_investigator(
            conn,
            feed_run_id="l2-run",
            groups=[make_group()],
            provider=provider,
            tools={"fetch_homepage_or_docs": broken_tool},
        )

        self.assertEqual(results[0]["tool_trace"][0]["status"], "error")
        self.assertEqual(results[0]["tool_trace"][0]["error_type"], "TimeoutError")
        self.assertGreater(results[0]["l2_score"], 0)

    def test_all_primitive_observations_and_one_recent_raw_result_flow_to_next_turn(self):
        conn = self.make_conn()
        provider = FakeLLMProvider(
            [
                tool_turn(
                    "Need narrow missing context.",
                    [
                        {
                            "name": "read_evidence_rows",
                            "arguments": {"entity_id": "entity:repo"},
                        },
                        {
                            "name": "fetch_github_file",
                            "arguments": {
                                "repo_key": "owner/repo",
                                "path": "package.json",
                            },
                        },
                        {
                            "name": "fetch_homepage_or_docs",
                            "arguments": {"url": "https://example.com/docs"},
                        },
                        {
                            "name": "web_search",
                            "arguments": {"query": "owner repo memory agent"},
                        },
                    ],
                ),
                final_response(confidence=77),
            ]
        )
        tools = {
            "read_evidence_rows": CountingTool(
                {"status": "ok", "rows": [{"metric_name": "stars_today"}]}
            ),
            "fetch_github_file": CountingTool(
                {"status": "ok", "excerpt": "package exposes CLI entrypoint"}
            ),
            "fetch_homepage_or_docs": CountingTool(
                {"status": "ok", "excerpt": "docs describe persistent memory"}
            ),
            "web_search": CountingTool(
                {"status": "ok", "results": [{"title": "Launch discussion"}]}
            ),
        }

        score_with_investigator(
            conn,
            feed_run_id="l2-run",
            groups=[make_group()],
            provider=provider,
            tools=tools,
        )

        working_state = provider.calls[1]["input_payload"]["working_state"]
        observations = working_state["verified_observations"]
        self.assertEqual(
            [row["tool"] for row in observations],
            [
                "read_evidence_rows",
                "fetch_github_file",
                "fetch_homepage_or_docs",
                "web_search",
            ],
        )
        self.assertIn("stars_today", observations[0]["excerpt"])
        self.assertIn("CLI entrypoint", observations[1]["excerpt"])
        self.assertIn("persistent memory", observations[2]["excerpt"])
        self.assertIn("Launch discussion", observations[3]["excerpt"])
        recent_raw = working_state["recent_raw_tool_results"]
        self.assertEqual(len(recent_raw), 1)
        self.assertEqual(recent_raw[0]["tool"], "web_search")

    def test_unavailable_tool_records_trace_and_allows_fallback_final(self):
        conn = self.make_conn()
        provider = FakeLLMProvider(
            [
                tool_turn(
                    "Need package metadata.",
                    [
                        {
                            "name": "fetch_github_file",
                            "arguments": {
                                "repo_key": "owner/repo",
                                "path": "package.json",
                            },
                        }
                    ],
                ),
                final_response(confidence=60),
            ]
        )

        results = score_with_investigator(
            conn,
            feed_run_id="l2-run",
            groups=[make_group()],
            provider=provider,
            tools={},
        )

        self.assertEqual(results[0]["tool_trace"][0]["status"], "unavailable")
        self.assertEqual(
            provider.calls[1]["input_payload"]["working_state"][
                "verified_observations"
            ][0]["status"],
            "unavailable",
        )

    def test_tool_trace_redacts_secret_like_values(self):
        conn = self.make_conn()
        provider = FakeLLMProvider(
            [
                tool_turn(
                    "Need docs page.",
                    [
                        {
                            "name": "fetch_homepage_or_docs",
                            "arguments": {
                                "url": "https://example.com/docs?api_key=secret-token"
                            },
                        }
                    ],
                ),
                final_response(confidence=58),
            ]
        )
        secret_tool = CountingTool(
            {
                "status": "ok",
                "excerpt": "Authorization: Bearer secret-token; sk-secret-token",
            }
        )

        score_with_investigator(
            conn,
            feed_run_id="l2-run",
            groups=[make_group()],
            provider=provider,
            tools={"fetch_homepage_or_docs": secret_tool},
        )

        persisted = conn.execute(
            "select tool_trace_json, raw_tool_results_json "
            "from l2_scoring_investigations"
        ).fetchone()
        for trace_json in persisted:
            self.assertNotIn("secret-token", trace_json)
            self.assertIn("[redacted]", trace_json)


if __name__ == "__main__":
    unittest.main()
