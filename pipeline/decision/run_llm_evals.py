from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.decision.hn_classifier import (  # noqa: E402
    PROJECT_SIGNAL_VALUES,
    PROMPT_VERSION as HN_PROMPT_VERSION,
    TASK as HN_TASK,
    build_hn_prompt_payload,
    validate_hn_output,
)
from pipeline.decision.llm_evals import hn_eval_cases, x_eval_cases  # noqa: E402
from pipeline.decision.llm_provider import DeepSeekProvider  # noqa: E402
from pipeline.decision.smoke_llm import load_env_file, load_json_secrets  # noqa: E402
from pipeline.decision.x_classifier import (  # noqa: E402
    X_STAGE1_PROMPT_VERSION,
    X_STAGE1_TASK,
    X_STAGE2_PROMPT_VERSION,
    X_STAGE2_TASK,
    accepted_x_tier,
    build_x_stage1_prompt_payload,
    build_x_stage2_prompt_payload,
    validate_x_stage1_output,
    validate_x_stage2_output,
)


TIER_ORDER = {"none": 0, "watch": 1, "potential": 2, "high": 3}


def summarize_results(results: list[dict[str, Any]]) -> dict[str, int]:
    passed = sum(1 for result in results if result["passed"])
    return {"total": len(results), "passed": passed, "failed": len(results) - passed}


def hn_row_for_case(case: dict[str, Any], item_id: int) -> dict[str, Any]:
    input_payload = case["input"]
    return {
        "item_id": item_id,
        "source": "hn_eval",
        "external_id": case["name"],
        "title": input_payload["title"],
        "url": input_payload["url"],
        "description": input_payload.get("description", ""),
        "metadata": {},
    }


def hn_actual(output: dict[str, Any]) -> dict[str, Any]:
    deterministic_links = output.get("deterministic_links") or []
    first_link = deterministic_links[0] if deterministic_links else {}
    projectness = output["projectness"]
    return {
        "projectness": projectness,
        "noise": projectness not in PROJECT_SIGNAL_VALUES,
        "has_alias": bool(deterministic_links),
        "deterministic_link_type": first_link.get("type"),
    }


def compare_hn_expected(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if actual["projectness"] != expected.get("projectness"):
        failures.append(
            f"projectness expected {expected.get('projectness')} got {actual['projectness']}"
        )
    if "noise" in expected and actual["noise"] != expected["noise"]:
        failures.append(f"noise expected {expected['noise']} got {actual['noise']}")
    if expected.get("requires_alias") and not actual["has_alias"]:
        failures.append("expected deterministic alias link")
    if expected.get("deterministic_link_type") and actual["deterministic_link_type"] != expected[
        "deterministic_link_type"
    ]:
        failures.append(
            "deterministic_link_type expected "
            f"{expected['deterministic_link_type']} got {actual['deterministic_link_type']}"
        )
    return failures


def run_hn_eval_cases(
    provider: Any,
    cases: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, case in enumerate(cases[: max(0, limit)], start=1):
        row = hn_row_for_case(case, index)
        payload = build_hn_prompt_payload(row)
        response = provider.complete_json(
            task=HN_TASK,
            prompt_version=HN_PROMPT_VERSION,
            input_payload=payload,
            system_prompt=(
                "Return strict JSON. Classify whether this HN item is concrete "
                "project evidence or noise."
            ),
        )
        output = validate_hn_output(response)
        actual = hn_actual(output)
        failures = compare_hn_expected(case["expected"], actual)
        results.append(
            {
                "case": case["name"],
                "kind": "hn",
                "passed": not failures,
                "expected": case["expected"],
                "actual": actual,
                "failures": failures,
            }
        )
    return results


def normalized_x_tweets(case: dict[str, Any]) -> list[dict[str, Any]]:
    tweets = []
    for tweet in case["input"].get("tweets", []):
        tweets.append(
            {
                "tweet_id": tweet["tweet_id"],
                "author_username": tweet.get("author_username") or tweet.get("author") or "",
                "text": tweet["text"],
                "url": tweet.get("url"),
                "created_at": tweet.get("created_at") or "2026-05-31T00:00:00Z",
            }
        )
    return tweets


def x_aggregate_for_case(case: dict[str, Any], tweets: list[dict[str, Any]]) -> dict[str, Any]:
    authors = {tweet["author_username"].lower() for tweet in tweets if tweet["author_username"]}
    return {
        "entity_id": f"eval:{case['name']}",
        "window": "24h",
        "distinct_authors": len(authors),
        "credible_authors": int(case["input"].get("credible_authors") or 0),
        "mention_count": len(tweets),
        "mention_acceleration": float(len(tweets)),
        "source_refs": [f"tweet:{tweet['tweet_id']}" for tweet in tweets],
    }


def x_actual(output: dict[str, Any], aggregate: dict[str, Any]) -> dict[str, Any]:
    accepted = accepted_x_tier(output, aggregate=aggregate)
    return {
        "raw_x_tier": output["x_tier"],
        "accepted_x_tier": accepted,
        "entity_confidence": output["entity_confidence"],
        "cited_tweet_ids_count": len(output["cited_tweet_ids"]),
    }


def compare_x_expected(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    expected_tier = expected.get("x_tier")
    if expected_tier and actual["accepted_x_tier"] != expected_tier:
        failures.append(
            f"x_tier expected {expected_tier} got {actual['accepted_x_tier']}"
        )
    max_tier = expected.get("max_tier")
    if max_tier and TIER_ORDER[actual["accepted_x_tier"]] > TIER_ORDER[max_tier]:
        failures.append(
            f"accepted tier {actual['accepted_x_tier']} exceeds max_tier {max_tier}"
        )
    if expected.get("requires_citations") and actual["accepted_x_tier"] != "none":
        if actual["cited_tweet_ids_count"] == 0:
            failures.append("accepted non-none tier without citations")
    if expected.get("noise") is True and actual["accepted_x_tier"] != "none":
        failures.append(f"noise expected none got {actual['accepted_x_tier']}")
    if (
        expected.get("noise") is False
        and "max_tier" not in expected
        and actual["accepted_x_tier"] == "none"
    ):
        failures.append("expected non-noise x signal")
    return failures


def run_x_eval_cases(
    provider: Any,
    cases: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in cases[: max(0, limit)]:
        tweets = normalized_x_tweets(case)
        aggregate = x_aggregate_for_case(case, tweets)
        payload = build_x_stage2_prompt_payload(aggregate, tweets)
        response = provider.complete_json(
            task=X_STAGE2_TASK,
            prompt_version=X_STAGE2_PROMPT_VERSION,
            input_payload=payload,
            system_prompt=(
                "Return strict JSON. Judge the X social signal tier for this "
                "eval case. Cite tweet ids."
            ),
        )
        output = validate_x_stage2_output(response)
        actual = x_actual(output, aggregate)
        failures = compare_x_expected(case["expected"], actual)
        results.append(
            {
                "case": case["name"],
                "kind": "x",
                "passed": not failures,
                "expected": case["expected"],
                "actual": actual,
                "failures": failures,
            }
        )
    return results


def stage1_actual(output: dict[str, Any]) -> dict[str, int]:
    triage = output.get("triage") or []
    return {
        "total": len(triage),
        "closer_look_count": sum(1 for item in triage if item.get("closer_look")),
        "product_signal_count": sum(
            1
            for item in triage
            if item.get("about_concrete_project")
            and (item.get("product_names") or item.get("product_links") or item.get("project_refs"))
        ),
        "project_ref_count": sum(len(item.get("project_refs") or []) for item in triage),
    }


def compare_x_stage1_expected(
    expected: dict[str, Any],
    actual: dict[str, int],
) -> list[str]:
    failures: list[str] = []
    if expected.get("noise") is True and actual["closer_look_count"] != 0:
        failures.append("noise case should not request closer look")
    if expected.get("noise") is False and actual["product_signal_count"] == 0:
        failures.append("expected at least one product signal")
    return failures


def run_x_stage1_eval_cases(
    provider: Any,
    cases: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in cases[: max(0, limit)]:
        tweets = normalized_x_tweets(case)
        payload = build_x_stage1_prompt_payload(tweets)
        response = provider.complete_json(
            task=X_STAGE1_TASK,
            prompt_version=X_STAGE1_PROMPT_VERSION,
            input_payload=payload,
            system_prompt=(
                "Return strict JSON. Triage each tweet for concrete product "
                "signals, product names, product links, and closer-look status."
            ),
        )
        output = validate_x_stage1_output(response)
        actual = stage1_actual(output)
        failures = compare_x_stage1_expected(case["expected"], actual)
        results.append(
            {
                "case": case["name"],
                "kind": "x_stage1",
                "passed": not failures,
                "expected": case["expected"],
                "actual": actual,
                "failures": failures,
            }
        )
    return results


def build_provider(model: str | None) -> DeepSeekProvider:
    load_env_file()
    load_json_secrets()
    return DeepSeekProvider(model=model)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run bounded LLM classifier evals")
    parser.add_argument("--kind", choices=["all", "hn", "x"], default="all")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)

    provider = build_provider(args.model)
    results: list[dict[str, Any]] = []
    if args.kind in {"all", "hn"}:
        results.extend(run_hn_eval_cases(provider, hn_eval_cases(), limit=args.limit))
    if args.kind in {"all", "x"}:
        results.extend(run_x_eval_cases(provider, x_eval_cases(), limit=args.limit))
    payload = {"summary": summarize_results(results), "results": results}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
