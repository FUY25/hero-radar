from __future__ import annotations

from typing import Any


def hn_eval_cases() -> list[dict[str, Any]]:
    return [
        {
            "name": "hn_project_with_github",
            "input": {
                "title": "Show HN: Clawdbot",
                "url": "https://news.ycombinator.com/item?id=1",
                "description": "An AI coding assistant with a public repo.",
            },
            "expected": {
                "projectness": "project",
                "requires_alias": True,
                "noise": False,
                "deterministic_link_type": "github",
            },
            "fake_provider_response": {
                "item_id": 1,
                "projectness": "project",
                "confidence": 0.92,
                "canonical_name": "Clawdbot",
                "deterministic_links": [
                    {
                        "type": "github",
                        "key": "github:owner/clawdbot",
                        "url": "https://github.com/owner/clawdbot",
                    }
                ],
                "proposed_links": [],
                "summary": "Show HN launch for Clawdbot.",
            },
        },
        {
            "name": "hn_news_noise",
            "input": {
                "title": "Major AI lab announces policy change",
                "url": "https://example.com/news",
                "description": "Industry news without a concrete project launch.",
            },
            "expected": {
                "projectness": "news_article",
                "requires_alias": False,
                "noise": True,
            },
            "fake_provider_response": {
                "item_id": 2,
                "projectness": "news_article",
                "confidence": 0.9,
                "canonical_name": "",
                "deterministic_links": [],
                "proposed_links": [],
                "summary": "News article, not project evidence.",
            },
        },
        {
            "name": "hn_topic_noise",
            "input": {
                "title": "Ask HN: What do you use for MCP?",
                "url": "https://news.ycombinator.com/item?id=2",
                "description": "General discussion with generic terms.",
            },
            "expected": {
                "projectness": "topic_discussion",
                "requires_alias": False,
                "noise": True,
            },
            "fake_provider_response": {
                "item_id": 3,
                "projectness": "topic_discussion",
                "confidence": 0.87,
                "canonical_name": "",
                "deterministic_links": [],
                "proposed_links": [],
                "summary": "Topic discussion, not launch evidence.",
            },
        },
    ]


def x_eval_cases() -> list[dict[str, Any]]:
    return [
        {
            "name": "x_linked_two_credible_potential",
            "input": {
                "tweets": [
                    {
                        "tweet_id": "t1",
                        "author": "credible1",
                        "text": "New repo https://github.com/owner/repo is useful",
                    },
                    {
                        "tweet_id": "t2",
                        "author": "credible2",
                        "text": "Trying owner/repo for agents today",
                    },
                ],
                "credible_authors": 2,
            },
            "expected": {
                "x_tier": "potential",
                "entity_confidence": "linked",
                "requires_citations": True,
                "noise": False,
            },
            "fake_stage1_response": {
                "triage": [
                    {
                        "tweet_id": "t1",
                        "about_concrete_project": True,
                        "closer_look": True,
                        "project_refs": [
                            {
                                "entity_key": "github:owner/repo",
                                "entity_name": "owner/repo",
                                "entity_confidence": "linked",
                                "confidence": 0.9,
                            }
                        ],
                        "expression_strength": "recommendation",
                        "evidence_quote": "New repo",
                        "reason": "Links a concrete repo.",
                    }
                ]
            },
            "fake_stage2_response": {
                "entity_key": "github:owner/repo",
                "x_tier": "potential",
                "entity_confidence": "linked",
                "x_expression_strength": "recommendation",
                "cited_tweet_ids": ["t1", "t2"],
                "rationale": "Two credible authors cite the same repo.",
                "cross_source_notes": [],
            },
        },
        {
            "name": "x_fuzzy_no_citations_not_potential",
            "input": {
                "tweets": [{"tweet_id": "t3", "author": "credible1", "text": "Clawdbot looks interesting"}],
                "credible_authors": 1,
            },
            "expected": {
                "max_tier": "watch",
                "requires_citations": True,
                "noise": False,
            },
            "fake_stage2_response": {
                "entity_key": "name:clawdbot",
                "x_tier": "potential",
                "entity_confidence": "fuzzy_name",
                "x_expression_strength": "recommendation",
                "cited_tweet_ids": [],
                "rationale": "Fuzzy name-only output without citations.",
                "cross_source_notes": [],
            },
        },
        {
            "name": "x_generic_known_term_none",
            "input": {
                "tweets": [
                    {
                        "tweet_id": "t4",
                        "author": "credible2",
                        "text": "OpenAI Claude and MCP are everywhere today",
                    }
                ],
                "credible_authors": 1,
            },
            "expected": {"x_tier": "none", "noise": True},
            "fake_stage2_response": {
                "entity_key": "term:MCP",
                "x_tier": "none",
                "entity_confidence": "fuzzy_name",
                "x_expression_strength": "neutral",
                "cited_tweet_ids": [],
                "rationale": "Generic known term without concrete binding.",
                "cross_source_notes": [],
            },
        },
    ]


def validate_eval_coverage(
    cases: list[dict[str, Any]],
    *,
    required_names: set[str],
) -> None:
    names = [str(case.get("name", "")) for case in cases]
    missing = sorted(required_names - set(names))
    if missing:
        raise AssertionError(f"missing eval cases: {', '.join(missing)}")
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise AssertionError(f"duplicate eval cases: {', '.join(duplicates)}")
    for case in cases:
        if "input" not in case or "expected" not in case:
            raise AssertionError(f"eval case {case.get('name', '<unnamed>')} is incomplete")
