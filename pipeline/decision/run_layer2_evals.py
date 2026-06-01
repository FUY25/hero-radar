from __future__ import annotations

import argparse
import json
from statistics import mean
from typing import Any

from pipeline.decision.kimi_provider import KimiProvider
from pipeline.decision.layer2_scout import NOVELTY_AXES, normalize_scout_decision


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
    parser.add_argument("--model", default="kimi-k2.5")
    args = parser.parse_args()
    if args.handshake:
        result = run_handshake(model=args.model)
    elif args.smoke:
        result = run_smoke(args.model)
    else:
        result = rank_eval_cases(default_eval_cases())
        scout_v2 = evaluate_scout_v2_cases(default_scout_v2_eval_cases())
        result = {
            **result,
            "ok": bool(result.get("ok")) and bool(scout_v2.get("ok")),
            "scout_v2": scout_v2,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") or result.get("skipped") else 1


def _is_medium_only_expected_failure(row: dict[str, Any]) -> bool:
    blocked_types = {"model", "article", "tutorial", "discussion", "news", "unknown"}
    return (
        not row["expected_include"]
        and row["object_type"] not in blocked_types
        and all(row[axis] == "medium" for axis in NOVELTY_AXES)
    )


if __name__ == "__main__":
    raise SystemExit(main())
