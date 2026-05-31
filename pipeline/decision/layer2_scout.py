from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from pipeline.decision.layer2_models import CandidateGroup
from pipeline.decision.schema import to_json


DEFAULT_SCOUT_PROMPT_VERSION = "layer2-edge-scout-v1"


SCOUT_SYSTEM_PROMPT = """
You are the Edge Watch Scout for Hero Radar.
Decide whether an edge_watch candidate deserves Layer 2 scoring.
Do not promote deterministic level. Return strict JSON with:
include_in_l2_scoring boolean, scout_score number 0..1, reason string,
needed_context array of strings, risk string, confidence number 0..1.
"""


def scout_edge_watch_groups(
    conn: sqlite3.Connection,
    *,
    feed_run_id: str,
    groups: list[CandidateGroup],
    provider: Any,
    prompt_version: str = DEFAULT_SCOUT_PROMPT_VERSION,
) -> list[CandidateGroup]:
    included: list[CandidateGroup] = []
    for group in groups:
        payload = {
            "group_id": group.group_id,
            "candidate": group.context,
            "instruction": (
                "Include only if the evidence suggests a concrete product/project/"
                "workflow worth semantic scoring."
            ),
        }
        response = provider.complete_json(
            task="layer2_edge_scout",
            prompt_version=prompt_version,
            input_payload=payload,
            system_prompt=SCOUT_SYSTEM_PROMPT,
        )
        normalized = _validate_response(response)
        cache_key = _cache_key(
            provider.provider_name, provider.model, prompt_version, payload
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
                1 if normalized["include_in_l2_scoring"] else 0,
                normalized["scout_score"],
                normalized["reason"],
                to_json(normalized["needed_context"]),
                normalized["risk"],
                normalized["confidence"],
                provider.provider_name,
                provider.model,
                prompt_version,
                cache_key,
            ),
        )
        if normalized["include_in_l2_scoring"]:
            included.append(group)
    conn.commit()
    return included


def _validate_response(response: dict[str, Any]) -> dict[str, Any]:
    required = [
        "include_in_l2_scoring",
        "scout_score",
        "reason",
        "needed_context",
        "risk",
        "confidence",
    ]
    missing = [key for key in required if key not in response]
    if missing:
        raise ValueError(f"scout response missing fields: {missing}")
    needed_context = response["needed_context"]
    if not isinstance(needed_context, list):
        raise ValueError("scout response needed_context must be a list")
    return {
        "include_in_l2_scoring": bool(response["include_in_l2_scoring"]),
        "scout_score": _clamp_float(response["scout_score"], 0, 1),
        "reason": str(response["reason"])[:600],
        "needed_context": [
            str(item)[:80] for item in needed_context if str(item).strip()
        ][:8],
        "risk": str(response["risk"])[:300],
        "confidence": _clamp_float(response["confidence"], 0, 1),
    }


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
