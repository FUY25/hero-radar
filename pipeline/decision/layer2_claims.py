from __future__ import annotations

from typing import Any, Iterable

from pipeline.decision.layer2_contracts import SUPPORT_AXES


class EvidenceReferenceError(ValueError):
    pass


def normalize_attributable_claims(
    raw_claims: Any,
    *,
    valid_evidence_refs: Iterable[str],
    max_items: int = 8,
) -> tuple[list[dict[str, Any]], list[str]]:
    if raw_claims is None:
        return [], []
    if not isinstance(raw_claims, list):
        raise ValueError("attributable claims must be an array")
    valid_refs = {str(ref) for ref in valid_evidence_refs if str(ref)}
    allowed_axes = set(SUPPORT_AXES)
    normalized: list[dict[str, Any]] = []
    projected_text: list[str] = []
    for raw_claim in raw_claims[: max(0, int(max_items))]:
        if not isinstance(raw_claim, dict):
            raise ValueError("attributable claim must be an object")
        claim = str(raw_claim.get("claim") or "").strip()[:1_000]
        if not claim:
            raise ValueError("attributable claim is missing claim text")
        raw_refs = raw_claim.get("evidence_refs")
        if not isinstance(raw_refs, list) or not raw_refs:
            raise EvidenceReferenceError(
                f"claim {claim!r} must cite at least one evidence_ref"
            )
        evidence_refs = _dedupe_strings(raw_refs, max_items=8, max_chars=160)
        unknown_refs = [ref for ref in evidence_refs if ref not in valid_refs]
        if unknown_refs:
            raise EvidenceReferenceError(
                "claim cites unknown evidence_ref values: " + ", ".join(unknown_refs)
            )
        raw_axes = raw_claim.get("supports_axes")
        if not isinstance(raw_axes, list) or not raw_axes:
            raise ValueError(f"claim {claim!r} must identify supported axes")
        supports_axes = _dedupe_strings(raw_axes, max_items=8, max_chars=40)
        invalid_axes = [axis for axis in supports_axes if axis not in allowed_axes]
        if invalid_axes:
            raise ValueError("claim uses unknown scoring axes: " + ", ".join(invalid_axes))
        claim_type = str(raw_claim.get("claim_type") or "").strip()
        if claim_type not in {"observed", "inferred"}:
            raise ValueError("claim_type must be observed or inferred")
        normalized.append(
            {
                "claim": claim,
                "evidence_refs": evidence_refs,
                "supports_axes": supports_axes,
                "claim_type": claim_type,
            }
        )
        projected_text.append(claim[:240])
    return normalized, projected_text


def _dedupe_strings(values: list[Any], *, max_items: int, max_chars: int) -> list[str]:
    rows: list[str] = []
    for value in values:
        text = str(value or "").strip()[:max_chars]
        if text and text not in rows:
            rows.append(text)
        if len(rows) >= max_items:
            break
    return rows
