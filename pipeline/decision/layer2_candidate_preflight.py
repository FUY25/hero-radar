from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping
from urllib.parse import urlparse

from pipeline.decision.layer2_models import CandidateGroup
from pipeline.decision.layer2_tool_registry import ToolCandidateContext


PreflightMode = Literal["score_from_context", "investigate", "cannot_score"]
SufficiencyLevel = Literal["weak", "medium", "strong"]
RICH_FIRST_PARTY_MIN_CHARS = 180
DIRECT_FINAL_MIN_ATTRIBUTABLE_EVIDENCE = 2
COMPLETE_PRODUCT_DESCRIPTION_MIN_CHARS = 80


@dataclass(frozen=True)
class CandidatePreflightResult:
    mode: PreflightMode
    must_finalize: bool
    information_sufficiency: dict[str, SufficiencyLevel]
    reason: str
    open_questions: tuple[str, ...]
    tool_candidate_context: ToolCandidateContext


def preflight_candidate(
    candidate: CandidateGroup | Mapping[str, Any],
    *,
    context_manifest: Mapping[str, Any] | None = None,
    direct_final_enabled: bool = False,
) -> CandidatePreflightResult:
    """Choose a deterministic scorer route and candidate-specific tool flags."""

    normalized = _candidate_mapping(candidate)
    context = dict(normalized.get("context") or {})
    members = [
        dict(item) for item in context.get("members") or () if isinstance(item, Mapping)
    ]
    evidence = [
        dict(item)
        for item in context.get("evidence_rows") or normalized.get("evidence_rows") or ()
        if isinstance(item, Mapping)
    ]
    name = str(
        normalized.get("canonical_name") or normalized.get("name") or ""
    ).strip()
    canonical_key = str(normalized.get("canonical_key") or "").strip()
    canonical_url = _safe_canonical_url(
        normalized.get("canonical_link") or normalized.get("canonical_url")
    )
    repo_key = _approved_repo_key(
        canonical_key,
        canonical_url,
        normalized=normalized,
        context=context,
        members=members,
    )
    entity_ids = _entity_ids(normalized, members)
    readme_text = _readme_text(normalized, context, members)
    description_text = _description_text(normalized, context, members)
    context_text = max((readme_text, description_text), key=len)
    attributable_evidence = [row for row in evidence if _has_attribution(row)]
    manifest = dict(context_manifest or {})
    evidence_availability = _evidence_availability(normalized, context)
    has_retrievable_evidence = bool(
        manifest.get("retrievable_evidence_ids")
        or evidence_availability.get("retrievable")
    )
    has_omitted_evidence = bool(
        has_retrievable_evidence
        or manifest.get("excluded_evidence_ids")
        or _omitted_count(evidence_availability)
    )
    source_families = {
        str(value).strip()
        for value in normalized.get("source_families")
        or context.get("source_families")
        or ()
        if str(value).strip()
    }
    source_families.update(
        str(row.get("family") or "").strip()
        for row in evidence
        if str(row.get("family") or "").strip()
    )
    needs_momentum_verification = bool(
        normalized.get("needs_momentum_verification")
        or context.get("needs_momentum_verification")
    )

    github_repo_url = _github_repo_from_url(canonical_url)
    resolved_key = canonical_key.startswith(("github:", "domain:", "npm:"))
    resolved_identity = bool(
        resolved_key
        or (canonical_url and not github_repo_url)
        or repo_key
    )
    identifiable_candidate = bool(name or canonical_key or canonical_url or entity_ids)
    rich_readme = bool(repo_key and len(readme_text) >= RICH_FIRST_PARTY_MIN_CHARS)
    rich_attributable_first_party = bool(
        resolved_identity
        and rich_readme
        and len(attributable_evidence) >= DIRECT_FINAL_MIN_ATTRIBUTABLE_EVIDENCE
        and not has_omitted_evidence
        and not needs_momentum_verification
    )
    sufficiency: dict[str, SufficiencyLevel] = {
        "identity": (
            "strong"
            if resolved_identity
            else ("medium" if identifiable_candidate or evidence else "weak")
        ),
        "workflow_shift": (
            "strong" if rich_readme else ("medium" if context_text else "weak")
        ),
        "technical_substance": (
            "strong" if rich_readme else ("medium" if repo_key else "weak")
        ),
        "product_market_fit": "medium" if context_text or evidence else "weak",
        "momentum": (
            "strong"
            if len(source_families) >= 3
            else ("medium" if source_families or evidence else "weak")
        ),
    }
    needs_technical_evidence = bool(repo_key and not rich_readme)
    needs_product_description = bool(
        canonical_url
        and len(context_text) < COMPLETE_PRODUCT_DESCRIPTION_MIN_CHARS
    )
    unresolved_identity = not resolved_identity
    tool_context = ToolCandidateContext(
        entity_ids=entity_ids,
        repo_key=repo_key,
        canonical_url=canonical_url,
        has_retrievable_evidence=has_retrievable_evidence,
        needs_technical_evidence=needs_technical_evidence,
        needs_product_description=needs_product_description,
        unresolved_identity=unresolved_identity,
        missing_first_party_material=bool(not canonical_url and not repo_key),
        needs_momentum_verification=needs_momentum_verification,
    )
    open_questions: list[str] = []
    if unresolved_identity:
        open_questions.append(
            "Resolve the candidate identity and canonical first-party source."
        )
    if needs_product_description:
        open_questions.append(
            "Retrieve an attributable first-party product description."
        )
    if needs_technical_evidence:
        open_questions.append(
            "Confirm the documented workflow and technical implementation."
        )
    if has_retrievable_evidence:
        open_questions.append(
            "Review omitted attributable evidence before final scoring."
        )
    if needs_momentum_verification:
        open_questions.append(
            "Verify whether independent momentum evidence changes the decision."
        )

    if not identifiable_candidate and not evidence and not readme_text:
        return CandidatePreflightResult(
            mode="cannot_score",
            must_finalize=True,
            information_sufficiency=sufficiency,
            reason=(
                "Candidate has neither an identifiable identity nor attributable "
                "evidence."
            ),
            open_questions=tuple(open_questions),
            tool_candidate_context=tool_context,
        )

    if rich_attributable_first_party and direct_final_enabled:
        return CandidatePreflightResult(
            mode="score_from_context",
            must_finalize=True,
            information_sufficiency=sufficiency,
            reason="Rich attributable first-party context is sufficient for scoring.",
            open_questions=(),
            tool_candidate_context=tool_context,
        )

    if rich_attributable_first_party:
        return CandidatePreflightResult(
            mode="investigate",
            must_finalize=False,
            information_sufficiency=sufficiency,
            reason="Direct-final is disabled; the scorer must confirm the final decision.",
            open_questions=(),
            tool_candidate_context=tool_context,
        )

    return CandidatePreflightResult(
        mode="investigate",
        must_finalize=False,
        information_sufficiency=sufficiency,
        reason=(
            "Candidate identity is unresolved and requires bounded investigation."
            if unresolved_identity
            else "Additional investigation is required before final scoring."
        ),
        open_questions=tuple(open_questions),
        tool_candidate_context=tool_context,
    )


def _candidate_mapping(candidate: CandidateGroup | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(candidate, CandidateGroup):
        return {
            "group_id": candidate.group_id,
            "canonical_entity_id": candidate.canonical_entity_id,
            "canonical_name": candidate.canonical_name,
            "canonical_key": candidate.canonical_key,
            "canonical_link": candidate.canonical_link,
            "member_entity_ids": candidate.member_entity_ids,
            "source_families": candidate.source_families,
            "context": candidate.context,
        }
    return dict(candidate)


def _entity_ids(
    candidate: Mapping[str, Any], members: list[dict[str, Any]]
) -> tuple[str, ...]:
    values = list(candidate.get("member_entity_ids") or ())
    values.extend(member.get("entity_id") for member in members)
    if candidate.get("canonical_entity_id"):
        values.append(candidate["canonical_entity_id"])
    return tuple(
        dict.fromkeys(str(value) for value in values if str(value or "").strip())
    )


def _approved_repo_key(
    canonical_key: str,
    canonical_url: str | None,
    *,
    normalized: Mapping[str, Any],
    context: Mapping[str, Any],
    members: list[dict[str, Any]],
) -> str | None:
    if canonical_key.startswith("github:"):
        value = canonical_key.split(":", 1)[1].strip("/")
        return value if value.count("/") == 1 else None

    repo_key = _github_repo_from_url(canonical_url)
    if not repo_key:
        return None
    binding_values = {
        str(normalized.get("binding_confidence") or "").lower(),
        str(context.get("binding_confidence") or "").lower(),
        *(str(member.get("binding_confidence") or "").lower() for member in members),
    }
    if binding_values.intersection({"verified", "resolved"}):
        return repo_key
    return None


def _github_repo_from_url(canonical_url: str | None) -> str | None:
    parsed = urlparse(canonical_url or "")
    if parsed.hostname and parsed.hostname.lower() in {"github.com", "www.github.com"}:
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if len(parts) == 2:
            return f"{parts[0]}/{parts[1].removesuffix('.git')}"
    return None


def _safe_canonical_url(value: Any) -> str | None:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return url


def _readme_text(
    candidate: Mapping[str, Any],
    context: Mapping[str, Any],
    members: list[dict[str, Any]],
) -> str:
    values = [
        candidate.get("readme_context"),
        context.get("readme_context"),
        *(
            member.get("context_preview")
            for member in members
            if member.get("readme_excerpt_available")
        ),
    ]
    return max((str(value or "").strip() for value in values), key=len, default="")


def _description_text(
    candidate: Mapping[str, Any],
    context: Mapping[str, Any],
    members: list[dict[str, Any]],
) -> str:
    values = [
        candidate.get("summary"),
        candidate.get("project_description"),
        candidate.get("context_summary"),
        context.get("summary"),
        context.get("project_description"),
        context.get("context_summary"),
        *(member.get("context_preview") for member in members),
    ]
    return max((str(value or "").strip() for value in values), key=len, default="")


def _has_attribution(row: Mapping[str, Any]) -> bool:
    has_id = row.get("evidence_id") not in (None, "") or row.get("id") not in (
        None,
        "",
    )
    has_source = bool(
        row.get("source") or row.get("family") or row.get("raw_url_or_ref")
    )
    return bool(has_id and has_source)


def _evidence_availability(
    candidate: Mapping[str, Any], context: Mapping[str, Any]
) -> Mapping[str, Any]:
    availability = candidate.get("evidence_availability") or context.get(
        "evidence_availability"
    )
    if not isinstance(availability, Mapping):
        return {}
    return availability


def _omitted_count(availability: Mapping[str, Any]) -> int:
    try:
        return max(0, int(availability.get("omitted_count") or 0))
    except (TypeError, ValueError):
        return 0
