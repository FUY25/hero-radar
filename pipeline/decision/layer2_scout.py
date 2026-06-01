from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from pipeline.decision.layer2_scout_context import wide_scout_context_for_group
from pipeline.decision.layer2_models import CandidateGroup
from pipeline.decision.schema import to_json


DEFAULT_SCOUT_PROMPT_VERSION = "layer2-edge-scout-v3"


NOVELTY_AXES = ("workflow_shift", "technical_substance", "product_market_fit")
NOVELTY_VALUES = {"none", "weak", "medium", "strong"}
BLOCKED_OBJECT_TYPES = {"model", "article", "tutorial", "discussion", "news", "unknown"}


SCOUT_SYSTEM_PROMPT = """
You are the Edge Watch Scout for Hero Radar.
You are a fast wide triage gate, not a scorer.

You receive many edge_watch candidates. Return only the candidates that may be
worth spending a later scoring call on. Omit all obvious filters.

Promote when there is a plausible concrete product, repo, package, tool, or
workflow with any possible novelty or product signal. Be rough and permissive:
the later scorer will judge workflow_shift, technical_substance, and
product_market_fit in detail.

Filter obvious noise:
- acquisition, funding, policy, or company news
- pure article, tutorial, resource list, discussion, paper, dataset, or model
  release with no product/workflow wrapper
- routine release with no new usage, technical, or product angle
- generic chatbot or thin wrapper with no clear wedge

Return strict JSON with top-level promotions array only:
{
  "promotions": [
    {
      "group_id": "...",
      "reason_code": "possible_workflow_shift|possible_technical_substance|possible_product_wedge|concrete_tool_unclear_but_interesting",
      "reason": "Short reason for why this might be worth scoring."
    }
  ]
}
"""


def scout_edge_watch_groups(
    conn: sqlite3.Connection,
    *,
    feed_run_id: str,
    groups: list[CandidateGroup],
    provider: Any,
    prompt_version: str = DEFAULT_SCOUT_PROMPT_VERSION,
    batch_size: int = 30,
) -> list[CandidateGroup]:
    included: list[CandidateGroup] = []
    context_by_id: dict[str, dict[str, Any]] = {}
    for batch in _chunks(groups, max(1, int(batch_size or 1))):
        candidates = [wide_scout_context_for_group(group) for group in batch]
        for candidate in candidates:
            context_by_id[str(candidate["group_id"])] = candidate
        payload = {
            "candidates": candidates,
            "decision_rule": (
                "Return only candidates that may be worth a later scoring call. "
                "Omit obvious filters."
            ),
            "instruction": (
                "Do not score every candidate. Return JSON object with promotions "
                "array only."
            ),
        }
        response = provider.complete_json(
            task="layer2_edge_scout",
            prompt_version=prompt_version,
            input_payload=payload,
            system_prompt=SCOUT_SYSTEM_PROMPT,
        )
        promotions = _validate_promotions_response(
            response, expected_group_ids=[group.group_id for group in batch]
        )
        for group in batch:
            decision = promotions.get(group.group_id) or _filtered_decision(group.group_id)
            cache_key = _cache_key(
                provider.provider_name,
                provider.model,
                prompt_version,
                {"candidate": context_by_id[group.group_id]},
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
                    group.group_id,
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


def normalize_wide_scout_promotion(response: dict[str, Any]) -> dict[str, Any]:
    group_id = str(response.get("group_id") or "").strip()
    if not group_id:
        raise ValueError("wide scout promotion missing group_id")
    reason_code = str(
        response.get("reason_code") or "concrete_tool_unclear_but_interesting"
    )
    reason = str(response.get("reason") or "Possible Edge Watch signal.")[:600]
    return {
        "group_id": group_id,
        "include_in_l2_scoring": True,
        "scout_score": 1.0,
        "reason": reason,
        "risk": f"reason_code={reason_code}"[:300],
        "confidence": 0.0,
    }


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
    normalized = [
        normalize_scout_decision(item) for item in decisions if isinstance(item, dict)
    ]
    by_group_id = {decision["group_id"]: decision for decision in normalized}
    missing = [
        group_id for group_id in expected_group_ids if group_id not in by_group_id
    ]
    if missing:
        raise ValueError(f"scout response missing decisions for groups: {missing}")
    return [by_group_id[group_id] for group_id in expected_group_ids]


def _validate_promotions_response(
    response: dict[str, Any], *, expected_group_ids: list[str]
) -> dict[str, dict[str, Any]]:
    promotions = response.get("promotions")
    if not isinstance(promotions, list):
        raise ValueError("scout response missing promotions array")
    expected = set(expected_group_ids)
    normalized: dict[str, dict[str, Any]] = {}
    for item in promotions:
        if not isinstance(item, dict):
            continue
        promotion = normalize_wide_scout_promotion(item)
        if promotion["group_id"] not in expected:
            continue
        normalized[promotion["group_id"]] = promotion
    return normalized


def _filtered_decision(group_id: str) -> dict[str, Any]:
    return {
        "group_id": group_id,
        "include_in_l2_scoring": False,
        "scout_score": 0.0,
        "reason": "Not selected by wide scout.",
        "risk": "reason_code=not_selected",
        "confidence": 0.0,
    }


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
