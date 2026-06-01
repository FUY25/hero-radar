from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from pipeline.decision.layer2_models import CandidateGroup
from pipeline.decision.schema import to_json


DEFAULT_SCORING_PROMPT_VERSION = "layer2-scoring-v1"


SCORING_SYSTEM_PROMPT = """
You score Hero Radar candidates for today's Feed.
Return strict JSON with axes object:
momentum, workflow_shift, technical_substance, adoption_path, confidence,
derivative_news_penalty.
Positive axes are 0..100. derivative_news_penalty is 0..25.
Also return primary_reason, topic_tags, rationale_short, caveats.
Ground claims in the provided evidence/context.
"""


def aggregate_l2_score(axes: dict[str, Any]) -> float:
    score = (
        0.25 * _axis(axes, "momentum")
        + 0.25 * _axis(axes, "workflow_shift")
        + 0.20 * _axis(axes, "technical_substance")
        + 0.15 * _axis(axes, "adoption_path")
        + 0.15 * _axis(axes, "confidence")
        - _penalty(axes, "derivative_news_penalty")
    )
    return round(max(0.0, min(100.0, score)), 2)


def score_candidate_groups(
    conn: sqlite3.Connection,
    *,
    feed_run_id: str,
    groups: list[CandidateGroup],
    provider: Any,
    prompt_version: str = DEFAULT_SCORING_PROMPT_VERSION,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for group in groups:
        payload = {"group_id": group.group_id, "candidate": group.context}
        response = provider.complete_json(
            task="layer2_scoring",
            prompt_version=prompt_version,
            input_payload=payload,
            system_prompt=SCORING_SYSTEM_PROMPT,
        )
        try:
            normalized = _validate_response(response)
        except ValueError as exc:
            repair_payload = {
                **payload,
                "validation_error": str(exc),
                "previous_response_shape": _response_shape(response),
                "instruction": (
                    "Return a complete corrected scoring JSON object with axes, "
                    "primary_reason, topic_tags, rationale_short, and caveats."
                ),
            }
            response = provider.complete_json(
                task="layer2_scoring_repair",
                prompt_version=prompt_version,
                input_payload=repair_payload,
                system_prompt=SCORING_SYSTEM_PROMPT,
            )
            normalized = _validate_response(response)
        l2_score = aggregate_l2_score(normalized["axes"])
        cache_key = _cache_key(
            provider.provider_name, provider.model, prompt_version, payload
        )
        conn.execute(
            """
            insert or replace into l2_scores(
              feed_run_id, group_id, l2_score, axes_json, primary_reason,
              topic_tags_json, rationale_short, caveats_json, provider, model,
              prompt_version, cache_key
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feed_run_id,
                group.group_id,
                l2_score,
                to_json(normalized["axes"]),
                normalized["primary_reason"],
                to_json(normalized["topic_tags"]),
                normalized["rationale_short"],
                to_json(normalized["caveats"]),
                provider.provider_name,
                provider.model,
                prompt_version,
                cache_key,
            ),
        )
        results.append({"group": group, "l2_score": l2_score, **normalized})
    conn.commit()
    return results


def _validate_response(response: dict[str, Any]) -> dict[str, Any]:
    if "axes" not in response or not isinstance(response["axes"], dict):
        raise ValueError("scoring response missing axes")
    axes = {
        "momentum": _axis(response["axes"], "momentum"),
        "workflow_shift": _axis(response["axes"], "workflow_shift"),
        "technical_substance": _axis(response["axes"], "technical_substance"),
        "adoption_path": _axis(response["axes"], "adoption_path"),
        "confidence": _axis(response["axes"], "confidence"),
        "derivative_news_penalty": _penalty(
            response["axes"], "derivative_news_penalty"
        ),
    }
    return {
        "axes": axes,
        "primary_reason": str(response.get("primary_reason") or "Signal")[:80],
        "topic_tags": [
            str(item)[:40]
            for item in response.get("topic_tags") or []
            if str(item).strip()
        ][:8],
        "rationale_short": str(response.get("rationale_short") or "")[:800],
        "caveats": [
            str(item)[:240]
            for item in response.get("caveats") or []
            if str(item).strip()
        ][:8],
    }


def _axis(axes: dict[str, Any], key: str) -> float:
    return _clamp_float(axes.get(key), 0, 100)


def _penalty(axes: dict[str, Any], key: str) -> float:
    return _clamp_float(axes.get(key, 0), 0, 25)


def _clamp_float(value: Any, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"expected numeric value, got {value!r}") from exc
    return max(minimum, min(maximum, number))


def _response_shape(response: dict[str, Any]) -> dict[str, Any]:
    return {
        key: type(value).__name__
        for key, value in response.items()
        if isinstance(key, str)
    }


def _cache_key(
    provider: str, model: str, prompt_version: str, payload: dict[str, Any]
) -> str:
    digest = hashlib.sha256(to_json(payload).encode("utf-8")).hexdigest()
    return f"{provider}:{model}:{prompt_version}:{digest}"
