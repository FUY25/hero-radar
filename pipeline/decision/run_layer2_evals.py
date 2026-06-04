from __future__ import annotations

import argparse
import json
from statistics import mean
from typing import Any

from pipeline.decision.kimi_provider import KimiProvider
from pipeline.decision.layer2_scoring_investigator import (
    DEFAULT_INVESTIGATOR_PROMPT_VERSION,
    SCORING_INVESTIGATOR_SYSTEM_PROMPT,
    aggregate_investigator_score,
)
from pipeline.decision.layer2_scout import (
    DEFAULT_SCOUT_PROMPT_VERSION,
    SCOUT_SYSTEM_PROMPT,
    normalize_scout_decision,
    normalize_wide_scout_promotion,
)


def default_eval_cases() -> list[dict[str, Any]]:
    return [
        {
            "name": "Generic AI funding news",
            "stage": "scoring",
            "l2_score": 42,
            "expected": "news",
            "reason": "Company news without repo-native or workflow evidence should not top the feed.",
        },
        {
            "name": "Edge Watch library with weak proof",
            "stage": "scout",
            "l2_score": 48,
            "expected": "watch",
            "reason": "A single weak source can stay in watch, but should not outrank project signals.",
        },
        {
            "name": "Repo-native agent workflow",
            "stage": "scoring",
            "l2_score": 84,
            "expected": "project",
            "reason": "Repo, README, and discussion evidence form a strong project signal.",
        },
        {
            "name": "Deepdive-worthy multi-source repo",
            "stage": "deepdive",
            "l2_score": 91,
            "expected": "project",
            "reason": "High score plus cross-source evidence should be selected for deepdive.",
        },
    ]


def default_scout_v2_eval_cases() -> list[dict[str, Any]]:
    return [
        {
            "name": "HeyClicky",
            "expected_include": True,
            "candidate": {
                "group_id": "group:heyclicky",
                "candidate": {
                    "name": "HeyClicky",
                    "canonical_link": "https://www.heyclicky.com/",
                    "level": "edge_watch",
                    "has_readme": False,
                    "project_context": [
                        (
                            "Mac-native assistant that sits next to the cursor, sees "
                            "the current screen, listens to voice, teaches users in "
                            "creative/dev apps, and can spin up background agents."
                        )
                    ],
                    "qualitative_summaries": [
                        "Screen-aware voice assistant for Mac workflows."
                    ],
                },
                "source_context": [
                    {
                        "source": "homepage",
                        "title": "AI buddy that lives on your Mac",
                        "url": "https://www.heyclicky.com/",
                    }
                ],
            },
            "decision": {
                "group_id": "group:heyclicky",
                "is_concrete_product": True,
                "object_type": "product",
                "workflow_shift": "strong",
                "technical_substance": "medium",
                "product_market_fit": "medium",
                "confidence": 0.82,
                "reason": (
                    "Mac-native screen, cursor, and voice assistant changes the "
                    "interaction model around desktop work."
                ),
            },
        },
        {
            "name": "OpenClaw",
            "expected_include": True,
            "candidate": {
                "group_id": "group:openclaw",
                "candidate": {
                    "name": "OpenClaw",
                    "canonical_link": "https://github.com/openclaw/openclaw",
                    "level": "edge_watch",
                    "has_readme": True,
                    "project_context": [
                        (
                            "Open-source local personal AI assistant with system "
                            "access, browser control, memory, skills, plugins, "
                            "and multi-channel gateways. The repo emphasizes "
                            "release evidence and validation evidence: durable CI, "
                            "performance, memory, install, and reliability checks "
                            "that users can inspect for each release."
                        )
                    ],
                    "qualitative_summaries": [
                        (
                            "Repo with release evidence and validation evidence for "
                            "local AI assistant workflows."
                        )
                    ],
                },
                "source_context": [
                    {
                        "source": "github",
                        "title": "openclaw/openclaw",
                        "url": "https://github.com/openclaw/openclaw",
                    }
                ],
            },
            "decision": {
                "group_id": "group:openclaw",
                "is_concrete_product": True,
                "object_type": "repo",
                "workflow_shift": "strong",
                "technical_substance": "medium",
                "product_market_fit": "strong",
                "confidence": 0.84,
                "reason": (
                    "Local personal AI assistant with system access, browser "
                    "control, memory, skills, and plugins."
                ),
            },
        },
        {
            "name": "Hermes Agent",
            "expected_include": True,
            "candidate": {
                "group_id": "group:hermes",
                "candidate": {
                    "name": "Hermes Agent",
                    "canonical_link": "https://github.com/NousResearch/hermes-agent",
                    "level": "edge_watch",
                    "has_readme": True,
                    "project_context": [
                        (
                            "Self-improving AI agent framework that runs in terminal, "
                            "messaging platforms, and IDEs; creates reusable skills, "
                            "keeps persistent memory, supports scheduled automations, "
                            "and works across sessions."
                        )
                    ],
                    "qualitative_summaries": [
                        "Agent grows through persistent memory and skill creation."
                    ],
                },
                "source_context": [
                    {
                        "source": "github",
                        "title": "NousResearch/hermes-agent",
                        "url": "https://github.com/NousResearch/hermes-agent",
                    }
                ],
            },
            "decision": {
                "group_id": "group:hermes",
                "is_concrete_product": True,
                "object_type": "repo",
                "workflow_shift": "medium",
                "technical_substance": "strong",
                "product_market_fit": "strong",
                "confidence": 0.83,
                "reason": (
                    "Self-improving agent with persistent memory, skill creation, "
                    "cross-session recall, automations, and multi-platform delivery."
                ),
            },
        },
        {
            "name": "Generic AI chatbot",
            "expected_include": False,
            "candidate": {
                "group_id": "group:generic-chatbot",
                "candidate": {
                    "name": "Generic AI chatbot",
                    "canonical_link": "https://example.com/generic-chatbot",
                    "level": "edge_watch",
                    "has_readme": False,
                    "project_context": [
                        (
                            "General-purpose chat assistant landing page for asking "
                            "questions, summarizing documents, and chatting with files."
                        )
                    ],
                    "qualitative_summaries": ["Generic chatbot without a workflow wedge."],
                },
                "source_context": [],
            },
            "decision": {
                "group_id": "group:generic-chatbot",
                "is_concrete_product": True,
                "object_type": "product",
                "workflow_shift": "weak",
                "technical_substance": "weak",
                "product_market_fit": "medium",
                "confidence": 0.74,
                "reason": "General chatbot with no distinct workflow or product wedge.",
            },
        },
        {
            "name": "AI company model news",
            "expected_include": False,
            "candidate": {
                "group_id": "group:model-news",
                "candidate": {
                    "name": "AI company model news",
                    "canonical_link": "https://example.com/model-news",
                    "level": "edge_watch",
                    "has_readme": False,
                    "project_context": [
                        (
                            "News article about a lab releasing a new model and "
                            "benchmark numbers, without a product, repo, or workflow."
                        )
                    ],
                    "qualitative_summaries": ["Model release news item."],
                },
                "source_context": [],
            },
            "decision": {
                "group_id": "group:model-news",
                "is_concrete_product": False,
                "object_type": "news",
                "workflow_shift": "strong",
                "technical_substance": "strong",
                "product_market_fit": "strong",
                "confidence": 0.8,
                "reason": "News article about a model release, not a product artifact.",
            },
        },
        {
            "name": "Tutorial resource list",
            "expected_include": False,
            "candidate": {
                "group_id": "group:tutorial",
                "candidate": {
                    "name": "Tutorial resource list",
                    "canonical_link": "https://example.com/tutorial",
                    "level": "edge_watch",
                    "has_readme": False,
                    "project_context": [
                        (
                            "Blog post collecting prompts, tips, and resources for "
                            "using existing coding agents."
                        )
                    ],
                    "qualitative_summaries": ["Educational resource without artifact."],
                },
                "source_context": [],
            },
            "decision": {
                "group_id": "group:tutorial",
                "is_concrete_product": False,
                "object_type": "tutorial",
                "workflow_shift": "medium",
                "technical_substance": "strong",
                "product_market_fit": "medium",
                "confidence": 0.79,
                "reason": "Educational resource list without a product artifact.",
            },
        },
        {
            "name": "Standalone model release",
            "expected_include": False,
            "candidate": {
                "group_id": "group:model",
                "candidate": {
                    "name": "Standalone model release",
                    "canonical_link": "https://example.com/model",
                    "level": "edge_watch",
                    "has_readme": False,
                    "project_context": [
                        (
                            "Weights and benchmark page for a model release, with no "
                            "product wrapper or concrete workflow."
                        )
                    ],
                    "qualitative_summaries": ["Standalone model artifact."],
                },
                "source_context": [],
            },
            "decision": {
                "group_id": "group:model",
                "is_concrete_product": False,
                "object_type": "model",
                "workflow_shift": "medium",
                "technical_substance": "strong",
                "product_market_fit": "medium",
                "confidence": 0.78,
                "reason": "Standalone model release without a workflow wrapper.",
            },
        },
        {
            "name": "Medium-only repo",
            "expected_include": False,
            "candidate": {
                "group_id": "group:medium-only",
                "candidate": {
                    "name": "Medium-only repo",
                    "canonical_link": "https://github.com/example/slack-ai-bot",
                    "level": "edge_watch",
                    "has_readme": True,
                    "project_context": [
                        (
                            "Small repo wrapping a standard LLM API into a Slack bot "
                            "for team Q&A, with familiar commands and setup."
                        )
                    ],
                    "qualitative_summaries": [
                        "Concrete but familiar chatbot integration."
                    ],
                },
                "source_context": [
                    {
                        "source": "github",
                        "title": "example/slack-ai-bot",
                        "url": "https://github.com/example/slack-ai-bot",
                    }
                ],
            },
            "decision": {
                "group_id": "group:medium-only",
                "is_concrete_product": True,
                "object_type": "repo",
                "workflow_shift": "medium",
                "technical_substance": "medium",
                "product_market_fit": "medium",
                "confidence": 0.81,
                "reason": "Concrete project but no strong novelty axis.",
            },
        },
    ]


def evaluate_scout_v2_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    for case in cases:
        normalized = normalize_scout_decision(case["decision"])
        expected = bool(case.get("expected_include"))
        actual = bool(normalized["include_in_l2_scoring"])
        row = {
            "name": case.get("name", ""),
            "expected_include": expected,
            "actual_include": actual,
            "object_type": normalized["object_type"],
            "workflow_shift": normalized["workflow_shift"],
            "technical_substance": normalized["technical_substance"],
            "product_market_fit": normalized["product_market_fit"],
            "reason": normalized["reason"],
        }
        evaluated.append(row)
        if actual != expected:
            mismatches.append(row)
    metrics = {
        "total": len(evaluated),
        "positive_cases": sum(1 for row in evaluated if row["expected_include"]),
        "negative_cases": sum(1 for row in evaluated if not row["expected_include"]),
        "medium_only_failures": sum(
            1 for row in evaluated if _is_medium_only_expected_failure(row)
        ),
    }
    return {
        "ok": not mismatches and metrics["positive_cases"] > 0,
        "cases": evaluated,
        "mismatches": mismatches,
        "metrics": metrics,
    }


def default_scoring_investigator_eval_cases() -> list[dict[str, Any]]:
    return [
        _scoring_eval_case(
            name="OpenClaw",
            expected_band="high",
            candidate={
                "name": "OpenClaw",
                "canonical_link": "https://github.com/openclaw/openclaw",
                "aliases": [
                    "clawdbot/clawdbot",
                    "clawdbot",
                    "ClawdBot",
                    "openclaw",
                    "OpenClaw",
                    "npm:clawdbot",
                    "npm:openclaw",
                ],
                "summary": (
                    "Open-source local personal AI assistant with system access, "
                    "browser control, memory, skills, plugins, and release "
                    "validation evidence users can inspect."
                ),
                "readme_context": (
                    "Local/multi-channel personal AI assistant: system access, "
                    "browser control, memory, skills, plugins, and gateways for "
                    "desktop and communication workflows."
                ),
                "evidence_rows": [
                    {
                        "family": "github",
                        "metric": "repo_redirect",
                        "value": "clawdbot/clawdbot -> openclaw/openclaw; repo id 1103012935",
                        "label": "alias proof via GitHub redirect",
                        "source_url": "https://api.github.com/repos/clawdbot/clawdbot",
                    },
                    {
                        "family": "github",
                        "metric": "stargazer_page_span",
                        "value": "page 310 ~= positions 30901-31000 around 2026-01-26T10:28Z..10:33Z",
                        "label": "T0 star acceleration around 30.5k stars",
                    },
                    {
                        "family": "npm",
                        "metric": "clawdbot_daily_downloads",
                        "value": "2026-01-24 16242; 01-25 70768; 01-26 106024; 01-27 139824",
                        "label": "strong package adoption under old alias",
                    },
                    {
                        "family": "hn",
                        "metric": "strict_story_count",
                        "value": "about 42 mixed-case clawdbot stories in 2026-01-25..2026-01-27 window",
                        "label": "HN discussion under old alias",
                    },
                    {
                        "family": "product_hunt",
                        "metric": "launch_rank",
                        "value": "daily rank 2; weekly rank 3; 835 votes; 53 comments",
                        "label": "Product Hunt OpenClaw launch corroboration",
                    },
                    {
                        "family": "hugging_face",
                        "metric": "same_day_resources",
                        "value": "KALLLA/clawdbot model and multiple clawdbot spaces appeared near T0",
                        "label": "ecosystem echo",
                    },
                ],
                "source_context": [
                    {
                        "source": "github",
                        "title": "openclaw/openclaw",
                        "url": "https://github.com/openclaw/openclaw",
                    },
                    {
                        "source": "hn",
                        "title": "Clawdbot - open source personal AI assistant",
                        "url": "https://news.ycombinator.com/item?id=46760237",
                    },
                    {
                        "source": "product_hunt",
                        "title": "OpenClaw launch",
                        "url": "https://www.producthunt.com/products/clawdbot-2/launches/openclaw",
                    },
                ],
            },
            object_type="repo",
            is_product_or_repo=True,
            axes={
                "workflow_shift": 88,
                "technical_substance": 88,
                "product_market_fit": 84,
                "momentum": 72,
                "confidence": 84,
                "risk_penalty": 4,
                "derivative_news_penalty": 0,
            },
            should_print=True,
            primary_reason="Local personal AI assistant with inspectable release proof.",
        ),
        _scoring_eval_case(
            name="Hermes Agent",
            expected_band="high",
            candidate={
                "name": "Hermes Agent",
                "canonical_link": "https://github.com/NousResearch/hermes-agent",
                "summary": (
                    "Self-improving agent framework with persistent memory, "
                    "skill creation, cross-session recall, automations, and "
                    "terminal, messaging, and IDE surfaces."
                ),
            },
            object_type="repo",
            is_product_or_repo=True,
            axes={
                "workflow_shift": 84,
                "technical_substance": 90,
                "product_market_fit": 82,
                "momentum": 70,
                "confidence": 83,
                "risk_penalty": 4,
                "derivative_news_penalty": 0,
            },
            should_print=True,
            primary_reason="Agent that compounds through memory and reusable skills.",
        ),
        _scoring_eval_case(
            name="HeyClicky",
            expected_band="high",
            candidate={
                "name": "HeyClicky",
                "canonical_link": "https://www.heyclicky.com/",
                "summary": (
                    "Mac-native screen-aware assistant near the cursor that "
                    "listens to voice, observes the active app, teaches creative "
                    "and developer workflows, and can dispatch background agents."
                ),
            },
            object_type="product",
            is_product_or_repo=True,
            axes={
                "workflow_shift": 92,
                "technical_substance": 74,
                "product_market_fit": 86,
                "momentum": 66,
                "confidence": 82,
                "risk_penalty": 4,
                "derivative_news_penalty": 0,
            },
            should_print=True,
            primary_reason="Screen and cursor aware desktop interaction shift.",
        ),
        _scoring_eval_case(
            name="Generic AI chatbot",
            expected_band="low",
            candidate={
                "name": "Generic AI chatbot",
                "canonical_link": "https://example.com/generic-chatbot",
                "summary": (
                    "General assistant for chatting with files, summarizing "
                    "documents, and answering questions without a distinct wedge."
                ),
            },
            object_type="product",
            is_product_or_repo=True,
            axes={
                "workflow_shift": 32,
                "technical_substance": 28,
                "product_market_fit": 42,
                "momentum": 35,
                "confidence": 76,
                "risk_penalty": 2,
                "derivative_news_penalty": 8,
            },
            should_print=False,
            primary_reason="Generic chatbot wrapper without a new workflow.",
        ),
        _scoring_eval_case(
            name="Funding acquisition news",
            expected_band="low",
            candidate={
                "name": "Funding acquisition news",
                "canonical_link": "https://example.com/funding-news",
                "summary": (
                    "Article about a startup raise and acquisition rumors, with "
                    "no repo, product workflow, or technical mechanism evidence."
                ),
            },
            object_type="news",
            is_product_or_repo=False,
            axes={
                "workflow_shift": 40,
                "technical_substance": 30,
                "product_market_fit": 45,
                "momentum": 75,
                "confidence": 82,
                "risk_penalty": 3,
                "derivative_news_penalty": 18,
            },
            should_print=False,
            primary_reason="Company news without artifact or workflow proof.",
        ),
        _scoring_eval_case(
            name="Standalone model release",
            expected_band="low",
            candidate={
                "name": "Standalone model release",
                "canonical_link": "https://example.com/model-release",
                "summary": (
                    "Weights and benchmark page for a new model release, without "
                    "a workflow wrapper, product, or repo-native user path."
                ),
            },
            object_type="model_release",
            is_product_or_repo=False,
            axes={
                "workflow_shift": 25,
                "technical_substance": 78,
                "product_market_fit": 35,
                "momentum": 80,
                "confidence": 82,
                "risk_penalty": 3,
                "derivative_news_penalty": 22,
            },
            should_print=False,
            primary_reason="Standalone model artifact without workflow wrapper.",
        ),
        _scoring_eval_case(
            name="Tutorial resource list",
            expected_band="low",
            candidate={
                "name": "Tutorial resource list",
                "canonical_link": "https://example.com/tutorial",
                "summary": (
                    "Blog post collecting prompts, links, and tips for existing "
                    "coding agents, but no product or repo artifact."
                ),
            },
            object_type="tutorial",
            is_product_or_repo=False,
            axes={
                "workflow_shift": 20,
                "technical_substance": 40,
                "product_market_fit": 25,
                "momentum": 50,
                "confidence": 78,
                "risk_penalty": 2,
                "derivative_news_penalty": 12,
            },
            should_print=False,
            primary_reason="Educational resource without product artifact.",
        ),
        _scoring_eval_case(
            name="Ordinary dashboard utility",
            expected_band="low",
            candidate={
                "name": "Ordinary dashboard utility",
                "canonical_link": "https://example.com/dashboard",
                "summary": (
                    "Standard dashboard/editor/calculator with AI labels but no "
                    "evidence of a new user workflow or technical mechanism."
                ),
            },
            object_type="product",
            is_product_or_repo=True,
            axes={
                "workflow_shift": 36,
                "technical_substance": 35,
                "product_market_fit": 48,
                "momentum": 44,
                "confidence": 76,
                "risk_penalty": 3,
                "derivative_news_penalty": 5,
            },
            should_print=False,
            primary_reason="Ordinary utility without explicit workflow unlock.",
        ),
        _scoring_eval_case(
            name="Screen-aware spreadsheet operator",
            expected_band="medium",
            candidate={
                "name": "Screen-aware spreadsheet operator",
                "canonical_link": "https://example.com/sheet-operator",
                "summary": (
                    "Gray-zone utility that watches the current spreadsheet, "
                    "understands selected cells, plans multi-step cleanup, and "
                    "executes formulas and edits with user confirmation."
                ),
            },
            object_type="product",
            is_product_or_repo=True,
            axes={
                "workflow_shift": 84,
                "technical_substance": 65,
                "product_market_fit": 78,
                "momentum": 60,
                "confidence": 80,
                "risk_penalty": 5,
                "derivative_news_penalty": 0,
            },
            should_print=True,
            primary_reason="Explicit workflow unlock in a familiar utility category.",
        ),
    ]


def _default_scoring_investigator_smoke_cases() -> list[dict[str, Any]]:
    cases_by_name = {
        str(case.get("name") or ""): case
        for case in default_scoring_investigator_eval_cases()
    }
    return [
        cases_by_name["OpenClaw"],
        cases_by_name["Hermes Agent"],
        cases_by_name["HeyClicky"],
        cases_by_name["Generic AI chatbot"],
        cases_by_name["Standalone model release"],
        cases_by_name["Screen-aware spreadsheet operator"],
    ]


def evaluate_scoring_investigator_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    for case in cases:
        row = _evaluate_scoring_case(case, response=case.get("response"))
        evaluated.append(row)
        if not row["matches_expected"]:
            mismatches.append(row)
    metrics = {
        "total": len(evaluated),
        "high_expected": sum(
            1 for row in evaluated if row["expected_band"] == "high"
        ),
        "medium_expected": sum(
            1 for row in evaluated if row["expected_band"] == "medium"
        ),
        "low_expected": sum(1 for row in evaluated if row["expected_band"] == "low"),
        "mismatch_count": len(mismatches),
    }
    return {
        "ok": not mismatches and metrics["high_expected"] > 0,
        "cases": evaluated,
        "mismatches": mismatches,
        "metrics": metrics,
    }


def default_wide_scout_eval_cases() -> list[dict[str, Any]]:
    return [
        {
            "name": case["name"],
            "expected_include": bool(case["expected_include"]),
            "candidate": _wide_eval_candidate(case),
        }
        for case in default_scout_v2_eval_cases()
    ]


def evaluate_wide_scout_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    promotions = [
        {
            "group_id": case["candidate"]["group_id"],
            "reason_code": "expected_promote",
            "reason": "Expected wide scout promotion.",
        }
        for case in cases
        if case.get("expected_include")
    ]
    return _evaluate_wide_promotions(cases=cases, promotions=promotions)


def run_wide_scout_kimi_eval(
    *,
    provider: Any | None = None,
    model: str = "kimi-k2.5",
    cases: list[dict[str, Any]] | None = None,
    batch_size: int = 30,
) -> dict[str, Any]:
    active_provider = provider or KimiProvider(model=model, timeout=90, max_retries=0)
    if not getattr(active_provider, "api_key", ""):
        return {"ok": False, "skipped": True, "reason": "Kimi key not configured"}
    active_cases = cases or default_wide_scout_eval_cases()
    evaluated: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    for batch in _chunks(active_cases, max(1, int(batch_size or 1))):
        candidates = [case["candidate"] for case in batch]
        try:
            response = active_provider.complete_json(
                task="layer2_wide_scout_eval",
                prompt_version=DEFAULT_SCOUT_PROMPT_VERSION,
                system_prompt=SCOUT_SYSTEM_PROMPT,
                input_payload={
                    "candidates": candidates,
                    "decision_rule": (
                        "return only candidates that may be worth a later scoring call"
                    ),
                    "instruction": (
                        "Return a JSON object with promotions array only. Omit filters."
                    ),
                },
            )
        except Exception as exc:
            return {
                "ok": False,
                "skipped": False,
                "error": type(exc).__name__,
                "reason": str(exc)[:300],
                "cases": evaluated,
                "mismatches": mismatches,
                "metrics": {
                    "total": len(evaluated),
                    "positive_cases": sum(
                        1 for row in evaluated if row["expected_include"]
                    ),
                    "negative_cases": sum(
                        1 for row in evaluated if not row["expected_include"]
                    ),
                    "mismatch_count": len(mismatches),
                },
            }
        batch_result = _evaluate_wide_promotions(
            cases=batch, promotions=response.get("promotions")
        )
        evaluated.extend(batch_result["cases"])
        mismatches.extend(batch_result["mismatches"])
    metrics = {
        "total": len(evaluated),
        "positive_cases": sum(1 for row in evaluated if row["expected_include"]),
        "negative_cases": sum(1 for row in evaluated if not row["expected_include"]),
        "mismatch_count": len(mismatches),
    }
    return {
        "ok": not mismatches,
        "skipped": False,
        "cases": evaluated,
        "mismatches": mismatches,
        "metrics": metrics,
    }


def run_scoring_investigator_kimi_eval(
    *,
    provider: Any | None = None,
    model: str = "kimi-k2.5",
    cases: list[dict[str, Any]] | None = None,
    limit: int = 6,
) -> dict[str, Any]:
    active_provider = provider or KimiProvider(model=model, timeout=90, max_retries=0)
    if not getattr(active_provider, "api_key", ""):
        return {"ok": False, "skipped": True, "reason": "Kimi key not configured"}
    active_cases = cases if cases is not None else _default_scoring_investigator_smoke_cases()
    active_limit = max(1, int(limit or 1))
    evaluated: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    system_prompt = (
        f"{SCORING_INVESTIGATOR_SYSTEM_PROMPT}\n\n"
        "For this eval smoke, no tools are available. Return action=final only."
    )
    for case in active_cases[:active_limit]:
        try:
            response = active_provider.complete_json(
                task="layer2_scoring_investigator_eval",
                prompt_version=DEFAULT_INVESTIGATOR_PROMPT_VERSION,
                system_prompt=system_prompt,
                input_payload={
                    "candidate": case.get("candidate") or {},
                    "instruction": (
                        "Score from supplied eval context only. Return action=final; "
                        "do not call tools. Use 0-100 numeric axis values, not a "
                        "0-10 scale: 70-100 means strong, 40-69 means medium, "
                        "0-39 means weak. Penalize generic wrappers, pure news, "
                        "standalone model releases, tutorials, and ordinary tools "
                        "without explicit workflow unlock. Treat candidate "
                        "evidence_rows, source_context, aliases, and readme_context "
                        "as available eval evidence; do not mark gaps solely because "
                        "live browsing is disabled."
                    ),
                    "schema": _scoring_eval_schema(),
                    "rubric": {
                        "high": (
                            "Real workflow unlock, technical substance or product "
                            "wedge, and enough confidence to print."
                        ),
                        "low": (
                            "Pure news, standalone model release, tutorial/list, "
                            "generic chatbot, or ordinary tool without workflow unlock."
                        ),
                    },
                },
            )
        except Exception as exc:
            return {
                "ok": False,
                "skipped": False,
                "error": type(exc).__name__,
                "reason": str(exc)[:300],
                "cases": evaluated,
                "mismatches": mismatches,
                "metrics": _scoring_eval_metrics(evaluated, mismatches),
            }
        row = _evaluate_scoring_case(case, response=response)
        evaluated.append(row)
        if not row["matches_expected"]:
            mismatches.append(row)
    return {
        "ok": not mismatches,
        "skipped": False,
        "cases": evaluated,
        "mismatches": mismatches,
        "metrics": _scoring_eval_metrics(evaluated, mismatches),
    }


def run_scout_v2_kimi_eval(**kwargs: Any) -> dict[str, Any]:
    return run_wide_scout_kimi_eval(**kwargs)


def _scoring_eval_case(
    *,
    name: str,
    expected_band: str,
    candidate: dict[str, Any],
    object_type: str,
    is_product_or_repo: bool,
    axes: dict[str, Any],
    should_print: bool,
    primary_reason: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "expected_band": expected_band,
        "candidate": candidate,
        "response": {
            "action": "final",
            "score": {
                "object_type": object_type,
                "is_product_or_repo": is_product_or_repo,
                "axes": axes,
                "supporting_evidence": [str(candidate.get("summary") or "")],
                "negative_evidence": [],
                "known_gaps": [],
                "primary_reason": primary_reason,
                "rationale_short": str(candidate.get("summary") or ""),
                "topic_tags": [],
                "caveats": [],
                "should_print": should_print,
            },
        },
    }


def _evaluate_scoring_case(
    case: dict[str, Any], *, response: Any
) -> dict[str, Any]:
    name = str(case.get("name") or "")
    expected_band = str(case.get("expected_band") or "")
    try:
        normalized = _normalize_scoring_eval_response(response)
        l2_score = normalized["l2_score"]
        should_print = normalized["should_print"]
        actual_band = _score_band(l2_score)
        matches_expected = _scoring_expectation_matches(
            expected_band=expected_band,
            l2_score=l2_score,
            should_print=should_print,
        )
        return {
            "name": name,
            "expected_band": expected_band,
            "actual_band": actual_band,
            "l2_score": l2_score,
            "should_print": should_print,
            "axes": normalized["axes"],
            "object_type": normalized["object_type"],
            "is_product_or_repo": normalized["is_product_or_repo"],
            "primary_reason": normalized["primary_reason"],
            "matches_expected": matches_expected,
        }
    except Exception as exc:
        return {
            "name": name,
            "expected_band": expected_band,
            "actual_band": "invalid",
            "l2_score": 0.0,
            "should_print": False,
            "axes": {},
            "object_type": "invalid",
            "is_product_or_repo": False,
            "primary_reason": "",
            "matches_expected": False,
            "error": type(exc).__name__,
            "reason": str(exc)[:300],
        }


def _normalize_scoring_eval_response(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise ValueError("scoring eval response must be an object")
    if str(response.get("action") or "") != "final":
        raise ValueError("scoring eval response must use action=final")
    score = response.get("score")
    if not isinstance(score, dict):
        raise ValueError("scoring eval response missing score")
    axes = score.get("axes")
    if not isinstance(axes, dict):
        raise ValueError("scoring eval response missing axes")
    object_type = str(score.get("object_type") or "unknown")[:40]
    is_product_or_repo = bool(score.get("is_product_or_repo", False))
    l2_score = aggregate_investigator_score(
        axes,
        object_type=object_type,
        is_product_or_repo=is_product_or_repo,
    )
    return {
        "object_type": object_type,
        "is_product_or_repo": is_product_or_repo,
        "axes": axes,
        "l2_score": l2_score,
        "should_print": bool(score.get("should_print", False)),
        "primary_reason": str(score.get("primary_reason") or "")[:120],
    }


def _score_band(score: float) -> str:
    if score >= 70:
        return "high"
    if score >= 60:
        return "medium"
    return "low"


def _scoring_expectation_matches(
    *, expected_band: str, l2_score: float, should_print: bool
) -> bool:
    if expected_band == "high":
        return l2_score >= 70 and should_print
    if expected_band == "medium":
        return 60 <= l2_score < 75 and should_print
    if expected_band == "low":
        return l2_score < 60 and not should_print
    return False


def _scoring_eval_metrics(
    evaluated: list[dict[str, Any]], mismatches: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "total": len(evaluated),
        "high_expected": sum(
            1 for row in evaluated if row["expected_band"] == "high"
        ),
        "medium_expected": sum(
            1 for row in evaluated if row["expected_band"] == "medium"
        ),
        "low_expected": sum(1 for row in evaluated if row["expected_band"] == "low"),
        "mismatch_count": len(mismatches),
    }


def _scoring_eval_schema() -> dict[str, Any]:
    return {
        "action": "final",
        "score": {
            "object_type": "product|repo|model_release|tutorial|news|unknown",
            "is_product_or_repo": "boolean",
            "axes": {
                "workflow_shift": "0..100",
                "technical_substance": "0..100",
                "product_market_fit": "0..100",
                "momentum": "0..100",
                "confidence": "0..100",
                "risk_penalty": "0..25",
                "derivative_news_penalty": "0..25",
            },
            "supporting_evidence": ["string"],
            "negative_evidence": ["string"],
            "known_gaps": ["string"],
            "primary_reason": "string",
            "rationale_short": "string",
            "topic_tags": ["string"],
            "caveats": ["string"],
            "should_print": "boolean",
        },
    }


def _wide_eval_candidate(case: dict[str, Any]) -> dict[str, Any]:
    source = case.get("candidate") or {}
    candidate = source.get("candidate") if isinstance(source, dict) else {}
    if not isinstance(candidate, dict):
        candidate = {}
    source_context = source.get("source_context") if isinstance(source, dict) else []
    source_context = source_context if isinstance(source_context, list) else []
    one_liner_parts = []
    for value in candidate.get("project_context") or []:
        if str(value).strip():
            one_liner_parts.append(str(value).strip())
    for value in candidate.get("qualitative_summaries") or []:
        if str(value).strip():
            one_liner_parts.append(str(value).strip())
    return {
        "group_id": str(source.get("group_id") or case["decision"]["group_id"]),
        "name": str(candidate.get("name") or case["name"]),
        "link": str(candidate.get("canonical_link") or ""),
        "object_hint": _object_hint_from_link(
            str(candidate.get("canonical_link") or "")
        ),
        "one_liner": " ".join(one_liner_parts)[:300],
        "source_titles": [
            str(item.get("title") or "")
            for item in source_context
            if isinstance(item, dict) and str(item.get("title") or "").strip()
        ][:3],
        "source_types": [
            str(item.get("source") or "")
            for item in source_context
            if isinstance(item, dict) and str(item.get("source") or "").strip()
        ][:5],
    }


def _evaluate_wide_promotions(
    *, cases: list[dict[str, Any]], promotions: Any
) -> dict[str, Any]:
    if not isinstance(promotions, list):
        raise ValueError("wide scout eval response missing promotions array")
    promoted_by_id: dict[str, dict[str, Any]] = {}
    expected_ids = {str(case["candidate"]["group_id"]) for case in cases}
    for item in promotions:
        if not isinstance(item, dict):
            continue
        normalized = normalize_wide_scout_promotion(item)
        if normalized["group_id"] in expected_ids:
            promoted_by_id[normalized["group_id"]] = normalized
    evaluated: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    for case in cases:
        group_id = str(case["candidate"]["group_id"])
        promotion = promoted_by_id.get(group_id)
        expected = bool(case.get("expected_include"))
        actual = promotion is not None
        row = {
            "name": case["name"],
            "expected_include": expected,
            "actual_include": actual,
            "reason_code": (
                promotion["risk"].replace("reason_code=", "")
                if promotion
                else "not_selected"
            ),
            "reason": promotion["reason"] if promotion else "Not selected by wide scout.",
        }
        evaluated.append(row)
        if actual != expected:
            mismatches.append(row)
    metrics = {
        "total": len(evaluated),
        "positive_cases": sum(1 for row in evaluated if row["expected_include"]),
        "negative_cases": sum(1 for row in evaluated if not row["expected_include"]),
        "mismatch_count": len(mismatches),
    }
    return {"ok": not mismatches, "cases": evaluated, "mismatches": mismatches, "metrics": metrics}


def _object_hint_from_link(link: str) -> str:
    if "github.com/" in link:
        return "github"
    if "npmjs.com/" in link:
        return "npm"
    if link.startswith("http://") or link.startswith("https://"):
        return "domain"
    return "unknown"


def rank_eval_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    ranked = sorted(cases, key=lambda row: -float(row.get("l2_score", 0)))
    top = ranked[0] if ranked else {}
    project_scores = [
        float(row.get("l2_score", 0))
        for row in ranked
        if row.get("expected") == "project"
    ]
    non_project_scores = [
        float(row.get("l2_score", 0))
        for row in ranked
        if row.get("expected") != "project"
    ]
    best_non_project = max(non_project_scores) if non_project_scores else 0.0
    metrics = {
        "total": len(ranked),
        "project_cases": len(project_scores),
        "non_project_cases": len(non_project_scores),
        "mean_project_score": mean(project_scores) if project_scores else 0.0,
        "mean_non_project_score": mean(non_project_scores) if non_project_scores else 0.0,
        "project_margin_over_best_non_project": (
            max(project_scores) - best_non_project if project_scores else 0.0
        ),
    }
    ok = (
        bool(top)
        and top.get("expected") == "project"
        and metrics["project_margin_over_best_non_project"] > 0
    )
    return {"ok": ok, "top": top, "ranked": ranked, "metrics": metrics}


def run_smoke(
    model: str = "kimi-k2.5", *, provider: Any | None = None
) -> dict[str, Any]:
    active_provider = provider or KimiProvider(model=model, timeout=45, max_retries=1)
    if not getattr(active_provider, "api_key", ""):
        return {"ok": False, "skipped": True, "reason": "Kimi key not configured"}
    response = active_provider.complete_json(
        task="layer2_eval_smoke",
        prompt_version="layer2-eval-smoke-v1",
        system_prompt="Return strict JSON with ok boolean and score number.",
        input_payload={
            "candidate": {
                "name": "Repo-native agent workflow",
                "evidence": ["GitHub repo with README", "HN product discussion"],
            }
        },
    )
    return {
        "ok": bool(response.get("ok", True)),
        "skipped": False,
        "shape": sorted(response.keys()),
    }


def run_handshake(
    *, provider: Any | None = None, model: str = "kimi-k2.5"
) -> dict[str, Any]:
    active_provider = provider or KimiProvider(model=model, timeout=20, max_retries=0)
    return active_provider.handshake()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Layer 2 evals")
    parser.add_argument("--handshake", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--scoring-investigator-eval", action="store_true")
    parser.add_argument("--scoring-investigator-kimi-eval", action="store_true")
    parser.add_argument("--scout-v2-kimi-eval", action="store_true")
    parser.add_argument("--wide-scout-kimi-eval", action="store_true")
    parser.add_argument("--model", default="kimi-k2.5")
    parser.add_argument("--limit", type=int, default=6)
    args = parser.parse_args()
    if args.handshake:
        result = run_handshake(model=args.model)
    elif args.smoke:
        result = run_smoke(args.model)
    elif args.scoring_investigator_eval:
        result = evaluate_scoring_investigator_cases(
            default_scoring_investigator_eval_cases()
        )
    elif args.scoring_investigator_kimi_eval:
        result = run_scoring_investigator_kimi_eval(
            model=args.model,
            limit=args.limit,
        )
    elif args.scout_v2_kimi_eval or args.wide_scout_kimi_eval:
        result = run_wide_scout_kimi_eval(model=args.model)
    else:
        result = rank_eval_cases(default_eval_cases())
        wide_scout = evaluate_wide_scout_cases(default_wide_scout_eval_cases())
        scoring = evaluate_scoring_investigator_cases(
            default_scoring_investigator_eval_cases()
        )
        result = {
            **result,
            "ok": (
                bool(result.get("ok"))
                and bool(wide_scout.get("ok"))
                and bool(scoring.get("ok"))
            ),
            "wide_scout": wide_scout,
            "scoring_investigator": scoring,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") or result.get("skipped") else 1


def _is_medium_only_expected_failure(row: dict[str, Any]) -> bool:
    return False


def _chunks(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


if __name__ == "__main__":
    raise SystemExit(main())
