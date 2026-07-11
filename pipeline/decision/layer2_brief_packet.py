from __future__ import annotations

from typing import Any, Mapping


def build_brief_writer_packet(
    row: Mapping[str, Any],
    *,
    output_schema: Mapping[str, Any],
    max_project_facts: int = 12,
    max_evidence_refs: int = 12,
) -> dict[str, Any]:
    group = row["group"]
    project_facts: list[dict[str, Any]] = []
    information_gaps: list[dict[str, str]] = []
    evidence_refs: list[str] = []

    for polarity, key in [
        ("supporting", "supporting_claims"),
        ("negative", "negative_claims"),
    ]:
        raw_claims = row.get(key)
        if not isinstance(raw_claims, list):
            continue
        for raw_claim in raw_claims:
            if not isinstance(raw_claim, Mapping):
                continue
            claim = str(raw_claim.get("claim") or "").strip()
            refs = _bounded_strings(raw_claim.get("evidence_refs"), 8, 160)
            if not claim or not refs:
                continue
            project_facts.append(
                {
                    "fact": claim[:1_000],
                    "evidence_refs": refs,
                    "supports_axes": _bounded_strings(
                        raw_claim.get("supports_axes"), 8, 40
                    ),
                    "fact_type": str(raw_claim.get("claim_type") or "observed"),
                    "polarity": polarity,
                }
            )
            _extend_unique(evidence_refs, refs, max_items=max_evidence_refs)
            if len(project_facts) >= max_project_facts:
                break
        if len(project_facts) >= max_project_facts:
            break

    observations = row.get("observations")
    if isinstance(observations, list):
        for raw_observation in observations:
            if len(project_facts) >= max_project_facts:
                break
            if not isinstance(raw_observation, Mapping):
                continue
            observation_id = str(raw_observation.get("observation_id") or "").strip()
            facts = raw_observation.get("facts")
            if not observation_id or not isinstance(facts, Mapping) or not facts:
                continue
            status = str(raw_observation.get("status") or "ok")
            if status not in {"ok", "success"}:
                reason = str(
                    facts.get("error")
                    or facts.get("message")
                    or raw_observation.get("error")
                    or status
                ).strip()
                information_gaps.append(
                    {
                        "observation_id": observation_id,
                        "status": status,
                        "reason": reason[:240],
                    }
                )
                continue
            project_facts.append(
                {
                    "fact": {
                        str(key): value
                        for key, value in list(facts.items())[:8]
                    },
                    "evidence_refs": [observation_id],
                    "supports_axes": _bounded_strings(
                        raw_observation.get("relevant_axes"), 8, 40
                    ),
                    "fact_type": "observed",
                    "polarity": "supporting",
                    "trust": str(
                        raw_observation.get("trust") or "external_untrusted"
                    ),
                }
            )
            _extend_unique(evidence_refs, [observation_id], max_items=max_evidence_refs)

    return {
        "candidate": {
            "identity": {
                "group_id": str(group.group_id),
                "canonical_name": str(group.canonical_name),
                "canonical_key": str(group.canonical_key),
                "canonical_link": str(group.canonical_link or ""),
            },
            "object_type": str(row.get("object_type") or "unknown"),
            "project_facts": project_facts,
            "information_gaps": information_gaps[:8],
            "top_evidence_refs": evidence_refs,
        },
        "decision": {
            "score": float(row.get("l2_score") or 0),
            "primary_reason": str(row.get("primary_reason") or "")[:160],
            "topic_tags": _bounded_strings(row.get("topic_tags"), 8, 40),
            "caveats": _bounded_strings(row.get("caveats"), 8, 240),
            "known_gaps": _bounded_strings(row.get("known_gaps"), 8, 240),
        },
        "output_schema": dict(output_schema),
    }


def _bounded_strings(value: Any, max_items: int, max_chars: int) -> list[str]:
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    for item in value:
        text = str(item or "").strip()[:max_chars]
        if text and text not in rows:
            rows.append(text)
        if len(rows) >= max_items:
            break
    return rows


def _extend_unique(target: list[str], values: list[str], *, max_items: int) -> None:
    for value in values:
        if value not in target:
            target.append(value)
        if len(target) >= max_items:
            return
