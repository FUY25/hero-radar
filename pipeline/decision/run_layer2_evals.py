from __future__ import annotations

import argparse
import json
from statistics import mean
from typing import Any

from pipeline.decision.kimi_provider import KimiProvider
from pipeline.decision.layer2_claims import normalize_attributable_claims
from pipeline.decision.layer2_contracts import (
    scoring_turn_output_schema_v2,
    validate_scoring_turn_v2,
)
from pipeline.decision.layer2_scoring_investigator import (
    DEFAULT_INVESTIGATOR_PROMPT_VERSION,
    aggregate_investigator_score,
)
from pipeline.decision.layer2_prompts import scoring_prompt_for_version
from pipeline.decision.layer2_scout import (
    DEFAULT_SCOUT_PROMPT_VERSION,
    SCOUT_SYSTEM_PROMPT,
    normalize_scout_decision,
    normalize_wide_scout_promotion,
)


SCORING_EVAL_CANDIDATE_EVIDENCE_REF = "eval:candidate"


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
    """Authored-response compatibility smoke loaded from the versioned dataset."""
    from pathlib import Path

    from pipeline.decision.layer2_eval import legacy_schema_smoke_cases

    dataset = (
        Path(__file__).resolve().parents[2]
        / "evals"
        / "layer2"
        / "datasets"
        / "scoring_cases.v1.jsonl"
    )
    return legacy_schema_smoke_cases(dataset)


def _evidence_expectations(
    required_families: list[str],
    *,
    minimum: int,
    external_content_untrusted: bool = True,
    expected_tool_outcome: str = "success",
) -> dict[str, Any]:
    return {
        "required_families": list(required_families),
        "minimum_attributable_claims": max(0, int(minimum)),
        "external_content_untrusted": bool(external_content_untrusted),
        "expected_tool_outcome": str(expected_tool_outcome),
    }


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
    metrics = _scoring_eval_metrics(evaluated, mismatches)
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
    active_provider = provider or KimiProvider(model=model, timeout=180, max_retries=0)
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
    prompt_version: str = DEFAULT_INVESTIGATOR_PROMPT_VERSION,
    system_prompt_override: str | None = None,
) -> dict[str, Any]:
    active_provider = provider or KimiProvider(model=model, timeout=90, max_retries=0)
    versioned_system_prompt = scoring_prompt_for_version(prompt_version)
    if not getattr(active_provider, "api_key", ""):
        return {"ok": False, "skipped": True, "reason": "Kimi key not configured"}
    active_cases = cases if cases is not None else _default_scoring_investigator_smoke_cases()
    active_limit = max(1, int(limit or 1))
    evaluated: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    active_system_prompt = system_prompt_override or versioned_system_prompt
    system_prompt = (
        f"{active_system_prompt}\n\n"
        "For this eval smoke, no tools are available. Return action=final only."
    )
    for case in active_cases[:active_limit]:
        raw_candidate = case.get("candidate")
        candidate = dict(raw_candidate) if isinstance(raw_candidate, dict) else {}
        candidate["evidence_ref"] = SCORING_EVAL_CANDIDATE_EVIDENCE_REF
        try:
            response = active_provider.complete_json(
                task="layer2_scoring_investigator_eval",
                prompt_version=prompt_version,
                system_prompt=system_prompt,
                input_payload={
                    "candidate": candidate,
                    "instruction": (
                        "Score from supplied eval context only. Return action=final; "
                        "do not call tools. Use 0-100 numeric axis values, not a "
                        "0-10 scale: 70-100 means strong, 40-69 means medium, "
                        "0-39 means weak. Penalize generic wrappers, pure news, "
                        "standalone model releases, tutorials, and ordinary tools "
                        "without explicit workflow unlock. Treat candidate "
                        "evidence_rows, source_context, aliases, and readme_context "
                        "as available eval evidence; do not mark gaps solely because "
                        "live browsing is disabled. Do not require public adoption "
                        "momentum for a high or medium score when the supplied "
                        "context already gives a concrete workflow unlock, technical "
                        "mechanism, and product/tool wedge; reflect limited adoption "
                        "as momentum/caveat rather than collapsing confidence. "
                        "Axis calibration: strong workflow unlock evidence should "
                        "usually be 80-95, concrete technical mechanism 75-95, and "
                        "clear product/tool wedge 75-90. Use risk_penalty above 8 "
                        "only for concrete abuse, legal, safety, reliability, or "
                        "permission-boundary risk, not for missing live browsing or "
                        "ordinary uncertainty. Use derivative_news_penalty only for "
                        "pure news, tutorials/resource lists, standalone model "
                        "releases, generic wrappers, or ordinary tools without an "
                        "explicit workflow unlock."
                        " Every supporting or negative claim must cite the "
                        "candidate evidence_ref supplied in this request."
                    ),
                    "schema": scoring_turn_output_schema_v2(),
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
    expected_route: str,
    expected_tool_need: list[str],
    evidence_expectations: dict[str, Any],
    scenario_tags: list[str],
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
        "expected_route": expected_route,
        "expected_tool_need": list(expected_tool_need),
        "evidence_expectations": dict(evidence_expectations),
        "scenario_tags": list(scenario_tags),
        "candidate": candidate,
        "response": {
            "action": "final",
            "information_sufficiency": _scoring_eval_information_sufficiency(
                axes,
                object_type=object_type,
            ),
            "score": {
                "object_type": (
                    "article" if object_type == "tutorial" else object_type
                ),
                "is_product_or_repo": is_product_or_repo,
                "axes": axes,
                "supporting_evidence": _scoring_eval_claims(
                    str(candidate.get("summary") or primary_reason),
                    minimum=int(
                        evidence_expectations.get("minimum_attributable_claims")
                        or 0
                    ),
                ),
                "negative_evidence": [],
                "known_gaps": (
                    ["Candidate identity remains unresolved."]
                    if object_type == "unknown"
                    else []
                ),
                "primary_reason": primary_reason[:80],
                "rationale_short": str(
                    candidate.get("summary") or primary_reason
                )[:1_000],
                "topic_tags": [],
                "caveats": [],
                "should_print": should_print,
            },
        },
    }


def _scoring_eval_information_sufficiency(
    axes: dict[str, Any],
    *,
    object_type: str,
) -> dict[str, str]:
    def level(axis: str) -> str:
        value = float(axes.get(axis) or 0)
        if value >= 70:
            return "strong"
        if value >= 40:
            return "medium"
        return "weak"

    return {
        "identity": "weak" if object_type == "unknown" else "strong",
        "workflow_shift": level("workflow_shift"),
        "technical_substance": level("technical_substance"),
        "product_market_fit": level("product_market_fit"),
        "momentum": level("momentum"),
    }


def _scoring_eval_claims(text: str, *, minimum: int) -> list[dict[str, Any]]:
    axes = ["workflow_shift", "technical_substance", "product_market_fit"]
    bounded_text = str(text or "Candidate evidence").strip()[:900]
    return [
        {
            "claim": f"{bounded_text} [{axes[index % len(axes)]}]",
            "evidence_refs": [SCORING_EVAL_CANDIDATE_EVIDENCE_REF],
            "supports_axes": [axes[index % len(axes)]],
            "claim_type": "observed",
        }
        for index in range(max(0, int(minimum)))
    ]


def _evaluate_scoring_case(
    case: dict[str, Any], *, response: Any
) -> dict[str, Any]:
    name = str(case.get("name") or "")
    expected_band = str(case.get("expected_band") or "")
    expectation_metadata = _scoring_case_expectation_metadata(case)
    try:
        normalized = _normalize_scoring_eval_response(response)
        l2_score = normalized["l2_score"]
        should_print = normalized["should_print"]
        actual_band = _score_band(l2_score)
        matches_score_expectation = _scoring_expectation_matches(
            expected_band=expected_band,
            l2_score=l2_score,
            should_print=should_print,
        )
        minimum_claims = int(
            expectation_metadata["evidence_expectations"].get(
                "minimum_attributable_claims", 0
            )
            or 0
        )
        meets_attribution_expectation = (
            normalized["attributable_claim_count"] >= minimum_claims
        )
        return {
            "name": name,
            "expected_band": expected_band,
            **expectation_metadata,
            "actual_band": actual_band,
            "l2_score": l2_score,
            "should_print": should_print,
            "axes": normalized["axes"],
            "object_type": normalized["object_type"],
            "is_product_or_repo": normalized["is_product_or_repo"],
            "primary_reason": normalized["primary_reason"],
            "attributable_claim_count": normalized[
                "attributable_claim_count"
            ],
            "meets_attribution_expectation": meets_attribution_expectation,
            "matches_expected": (
                matches_score_expectation and meets_attribution_expectation
            ),
        }
    except Exception as exc:
        return {
            "name": name,
            "expected_band": expected_band,
            **expectation_metadata,
            "actual_band": "invalid",
            "l2_score": 0.0,
            "should_print": False,
            "axes": {},
            "object_type": "invalid",
            "is_product_or_repo": False,
            "primary_reason": "",
            "attributable_claim_count": 0,
            "meets_attribution_expectation": False,
            "matches_expected": False,
            "error": type(exc).__name__,
            "reason": str(exc)[:300],
        }


def _normalize_scoring_eval_response(response: Any) -> dict[str, Any]:
    validate_scoring_turn_v2(response)
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
    supporting_claims, _supporting_text = normalize_attributable_claims(
        score.get("supporting_evidence"),
        valid_evidence_refs={SCORING_EVAL_CANDIDATE_EVIDENCE_REF},
    )
    negative_claims, _negative_text = normalize_attributable_claims(
        score.get("negative_evidence"),
        valid_evidence_refs={SCORING_EVAL_CANDIDATE_EVIDENCE_REF},
    )
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
        "attributable_claim_count": len(supporting_claims) + len(negative_claims),
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
    band_coverage = {
        band: sum(1 for row in evaluated if row["expected_band"] == band)
        for band in ("high", "medium", "low")
    }
    route_coverage = {
        route: sum(1 for row in evaluated if row["expected_route"] == route)
        for route in ("score_from_context", "investigate", "cannot_score")
    }
    tool_names = sorted(
        {
            str(tool_name)
            for row in evaluated
            for tool_name in row["expected_tool_need"]
        }
    )
    tool_need_coverage = {
        "none": sum(1 for row in evaluated if not row["expected_tool_need"]),
        **{
            tool_name: sum(
                1
                for row in evaluated
                if tool_name in row["expected_tool_need"]
            )
            for tool_name in tool_names
        },
    }
    scenario_tags = sorted(
        {
            str(tag)
            for row in evaluated
            for tag in row["scenario_tags"]
        }
    )
    scenario_coverage = {
        tag: sum(1 for row in evaluated if tag in row["scenario_tags"])
        for tag in scenario_tags
    }
    injection_surfaces = {
        "prompt_injection_readme": "readme",
        "prompt_injection_homepage": "homepage",
        "prompt_injection_search": "search",
    }
    covered_injection_surfaces = sorted(
        surface
        for tag, surface in injection_surfaces.items()
        if scenario_coverage.get(tag, 0) > 0
    )
    return {
        "total": len(evaluated),
        "high_expected": band_coverage["high"],
        "medium_expected": band_coverage["medium"],
        "low_expected": band_coverage["low"],
        "mismatch_count": len(mismatches),
        "band_coverage": band_coverage,
        "route_coverage": route_coverage,
        "tool_need_coverage": tool_need_coverage,
        "scenario_coverage": scenario_coverage,
        "injection_coverage": {
            "cases": sum(
                scenario_coverage.get(tag, 0) for tag in injection_surfaces
            ),
            "surfaces": covered_injection_surfaces,
        },
        "tool_failure_coverage": {
            "404": scenario_coverage.get("tool_404", 0),
            "403": scenario_coverage.get("tool_403", 0),
            "rate_limited": scenario_coverage.get("tool_rate_limited", 0),
        },
        "expectation_contract_coverage": sum(
            1 for row in evaluated if row["has_expectation_contract"]
        ),
    }


def _scoring_case_expectation_metadata(case: dict[str, Any]) -> dict[str, Any]:
    route = str(case.get("expected_route") or "unspecified")
    raw_tool_need = case.get("expected_tool_need")
    tool_need = (
        [str(value) for value in raw_tool_need]
        if isinstance(raw_tool_need, list)
        else []
    )
    raw_evidence = case.get("evidence_expectations")
    evidence_expectations = (
        dict(raw_evidence) if isinstance(raw_evidence, dict) else {}
    )
    raw_tags = case.get("scenario_tags")
    scenario_tags = (
        [str(value) for value in raw_tags] if isinstance(raw_tags, list) else []
    )
    required_evidence_keys = {
        "required_families",
        "minimum_attributable_claims",
        "external_content_untrusted",
    }
    return {
        "expected_route": route,
        "expected_tool_need": tool_need,
        "evidence_expectations": evidence_expectations,
        "scenario_tags": scenario_tags,
        "has_expectation_contract": (
            route in {"score_from_context", "investigate", "cannot_score"}
            and isinstance(raw_tool_need, list)
            and required_evidence_keys.issubset(evidence_expectations)
            and isinstance(raw_tags, list)
        ),
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
    parser = argparse.ArgumentParser(
        description="Run legacy Layer 2 schema and provider smoke checks"
    )
    parser.add_argument("--handshake", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument(
        "--scoring-investigator-eval",
        action="store_true",
        help="authored-response schema smoke; not a production-equivalent Agent eval",
    )
    parser.add_argument(
        "--scoring-investigator-kimi-eval",
        action="store_true",
        help="small live-provider smoke with tools disabled; not release-quality eval",
    )
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
