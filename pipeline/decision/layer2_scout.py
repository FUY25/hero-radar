from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from pipeline.decision.layer2_scout_context import scout_context_for_group
from pipeline.decision.layer2_models import CandidateGroup
from pipeline.decision.schema import to_json


DEFAULT_SCOUT_PROMPT_VERSION = "layer2-edge-scout-v2"


NOVELTY_AXES = ("workflow_shift", "technical_substance", "product_market_fit")
NOVELTY_VALUES = {"none", "weak", "medium", "strong"}
BLOCKED_OBJECT_TYPES = {"model", "article", "tutorial", "discussion", "news", "unknown"}


SCOUT_SYSTEM_PROMPT = """
You are the Edge Watch Scout for Hero Radar.
Decide whether each edge_watch candidate is a concrete product, repo, package,
tool, or workflow worth Layer 2 scoring.

Evaluate candidates independently. Do not rank, compare, or enforce a quota.
Return strict JSON with top-level decisions array. Each decision must include:
group_id, is_concrete_product boolean, object_type string,
workflow_shift, technical_substance, product_market_fit, confidence number 0..1,
and reason string.

Use novelty values only: none, weak, medium, strong.
Medium is not enough for inclusion; at least one novelty axis must be strong.
Do not require an academic breakthrough for a strong axis. Strong technical
substance can be an unusual system combination, local runtime, validation or
release-evidence harness, memory/tool protocol, multi-agent runtime, or
inspectable reliability mechanism.
News, articles, tutorials, discussions, standalone model releases, and unknown
objects are not concrete products unless the candidate is actually about a
linked product/repo/package/workflow.
"""


def scout_edge_watch_groups(
    conn: sqlite3.Connection,
    *,
    feed_run_id: str,
    groups: list[CandidateGroup],
    provider: Any,
    prompt_version: str = DEFAULT_SCOUT_PROMPT_VERSION,
    batch_size: int = 3,
) -> list[CandidateGroup]:
    included: list[CandidateGroup] = []
    group_by_id = {group.group_id: group for group in groups}
    context_by_id: dict[str, dict[str, Any]] = {}
    for batch in _chunks(groups, max(1, int(batch_size or 1))):
        candidates = [scout_context_for_group(group) for group in batch]
        for candidate in candidates:
            context_by_id[str(candidate["group_id"])] = candidate
        payload = {
            "candidates": candidates,
            "decision_rule": (
                "Include only concrete products with at least one strong novelty "
                "axis among workflow_shift, technical_substance, and "
                "product_market_fit. Medium-only candidates must be filtered."
            ),
            "instruction": (
                "Judge every candidate independently. Return JSON object with "
                "decisions array in any order."
            ),
        }
        response = provider.complete_json(
            task="layer2_edge_scout",
            prompt_version=prompt_version,
            input_payload=payload,
            system_prompt=SCOUT_SYSTEM_PROMPT,
        )
        decisions = _validate_batch_response(
            response, expected_group_ids=[group.group_id for group in batch]
        )
        for decision in decisions:
            group_id = decision["group_id"]
            group = group_by_id[group_id]
            cache_key = _cache_key(
                provider.provider_name,
                provider.model,
                prompt_version,
                {"candidate": context_by_id[group_id]},
            )
            conn.execute(
                """
                insert or replace into l2_scout_results(
                  feed_run_id, group_id, included_in_scoring, scout_score, reason,
                  needed_context_json, risk, confidence, provider, model, prompt_version, cache_key
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feed_run_id,
                    group_id,
                    1 if decision["include_in_l2_scoring"] else 0,
                    decision["scout_score"],
                    decision["reason"],
                    to_json([]),
                    decision["risk"],
                    decision["confidence"],
                    provider.provider_name,
                    provider.model,
                    prompt_version,
                    cache_key,
                ),
            )
            if decision["include_in_l2_scoring"]:
                included.append(group)
    conn.commit()
    return included


def normalize_scout_decision(response: dict[str, Any]) -> dict[str, Any]:
    required = ["group_id", "is_concrete_product", "object_type", *NOVELTY_AXES]
    missing = [key for key in required if key not in response]
    if missing:
        raise ValueError(f"scout response missing fields: {missing}")
    axes = {axis: _novelty_value(response.get(axis)) for axis in NOVELTY_AXES}
    strong_axes = [axis for axis, value in axes.items() if value == "strong"]
    object_type = _object_type(response.get("object_type"))
    is_concrete = bool(response.get("is_concrete_product"))
    include = scout_decision_included(
        is_concrete_product=is_concrete,
        object_type=object_type,
        axes=axes,
    )
    return {
        "group_id": str(response["group_id"]),
        "is_concrete_product": is_concrete,
        "object_type": object_type,
        **axes,
        "include_in_l2_scoring": include,
        "scout_score": scout_decision_score(
            is_concrete_product=is_concrete,
            object_type=object_type,
            axes=axes,
        ),
        "reason": str(response.get("reason") or "")[:600],
        "risk": _risk(object_type, strong_axes),
        "confidence": _clamp_float(response.get("confidence", 0), 0, 1),
    }


def scout_decision_included(
    *,
    is_concrete_product: bool,
    object_type: str,
    axes: dict[str, str],
) -> bool:
    return (
        bool(is_concrete_product)
        and object_type not in BLOCKED_OBJECT_TYPES
        and any(axes.get(axis) == "strong" for axis in NOVELTY_AXES)
    )


def scout_decision_score(
    *,
    is_concrete_product: bool,
    object_type: str,
    axes: dict[str, str],
) -> float:
    if not is_concrete_product or object_type in BLOCKED_OBJECT_TYPES:
        return 0.0
    strong_count = sum(1 for axis in NOVELTY_AXES if axes.get(axis) == "strong")
    return {0: 0.35, 1: 0.75, 2: 0.85, 3: 0.95}[strong_count]


def _validate_batch_response(
    response: dict[str, Any], *, expected_group_ids: list[str]
) -> list[dict[str, Any]]:
    decisions = response.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("scout response missing decisions array")
    normalized = [normalize_scout_decision(item) for item in decisions if isinstance(item, dict)]
    by_group_id = {decision["group_id"]: decision for decision in normalized}
    missing = [group_id for group_id in expected_group_ids if group_id not in by_group_id]
    if missing:
        raise ValueError(f"scout response missing decisions for groups: {missing}")
    return [by_group_id[group_id] for group_id in expected_group_ids]


def _novelty_value(value: Any) -> str:
    normalized = str(value or "none").strip().lower()
    return normalized if normalized in NOVELTY_VALUES else "none"


def _object_type(value: Any) -> str:
    normalized = str(value or "unknown").strip().lower()
    return normalized or "unknown"


def _risk(object_type: str, strong_axes: list[str]) -> str:
    axes = ",".join(strong_axes) if strong_axes else "none"
    return f"object_type={object_type};strong_axes={axes}"[:300]


def _chunks(groups: list[CandidateGroup], size: int) -> list[list[CandidateGroup]]:
    return [groups[index : index + size] for index in range(0, len(groups), size)]


def _clamp_float(value: Any, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"expected numeric value, got {value!r}") from exc
    return max(minimum, min(maximum, number))


def _cache_key(
    provider: str, model: str, prompt_version: str, payload: dict[str, Any]
) -> str:
    digest = hashlib.sha256(to_json(payload).encode("utf-8")).hexdigest()
    return f"{provider}:{model}:{prompt_version}:{digest}"
