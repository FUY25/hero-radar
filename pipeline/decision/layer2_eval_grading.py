from __future__ import annotations

import json
import re
from typing import Any, Mapping, Protocol


class EvalCaseLike(Protocol):
    model_input: Mapping[str, Any]
    gold: Mapping[str, Any]
    grader: Mapping[str, Any]


def grade_eval_artifact(
    case: EvalCaseLike, artifact: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    """Grade production output and trajectory; never consult provider requests."""

    score = float(artifact.get("score") or 0)
    minimum, maximum = [float(value) for value in case.gold["score_interval"]]
    expected_band = str(case.gold["score_band"])
    actual_band = _score_band(score)
    score_passed = minimum <= score <= maximum and actual_band == expected_band
    expected_preflight = str(case.gold["preflight_mode"])
    actual_preflight = str(artifact.get("preflight_mode") or "")
    expected_route = (
        "score_only"
        if bool(case.gold["should_print"])
        and bool(case.gold["is_product_or_repo"])
        and minimum >= 50
        else "suppress_or_low"
    )
    actual_route = str(artifact.get("route") or "")
    tool_trace = [
        row for row in artifact.get("tool_trace", []) if isinstance(row, Mapping)
    ]
    actual_tools = [str(row.get("tool") or "") for row in tool_trace]
    required_tools = {str(name) for name in case.gold.get("required_tool_names", [])}
    allowed_tools = {str(name) for name in case.gold.get("allowed_tool_names", [])}
    allowed_families = {
        str(name) for name in case.grader.get("allowed_tool_families", [])
    }
    forbidden_families = {
        str(name) for name in case.grader.get("forbidden_tool_families", [])
    }
    actual_families = {
        str(row.get("eval_family") or row.get("family") or "unknown")
        for row in tool_trace
        if row.get("tool")
    }
    unnecessary = sorted(set(actual_tools) - allowed_tools)
    missing_required = sorted(required_tools - set(actual_tools))
    forbidden = sorted(actual_families & forbidden_families)
    outside_allowed = sorted(actual_families - allowed_families) if allowed_families else []
    status_counts: dict[str, int] = {}
    for row in tool_trace:
        status = str(row.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    expected_outcome = case.grader.get("expected_tool_outcome")
    outcome_matches = _tool_outcome_matches(expected_outcome, tool_trace)
    bad_statuses = {
        "invalid",
        "rejected",
        "candidate_boundary_rejected",
        "unauthorized",
        "duplicate",
        "duplicate_call",
        "repeated",
        "repeated_signature",
        "unnecessary",
        "budget_exceeded",
    }
    trajectory_passed = not (
        missing_required
        or unnecessary
        or forbidden
        or outside_allowed
        or not outcome_matches
        or any(status_counts.get(status, 0) for status in bad_statuses)
    )
    result = artifact.get("result") if isinstance(artifact.get("result"), Mapping) else {}
    claims = [
        row
        for key in ("supporting_claims", "negative_claims")
        for row in result.get(key, [])
        if isinstance(row, Mapping)
    ]
    valid_refs = {
        str(evidence_id)
        for manifest in artifact.get("context_manifests", [])
        if isinstance(manifest, Mapping)
        for evidence_id in manifest.get("included_evidence_ids", [])
    }
    observations = {
        str(row.get("observation_id")): row
        for row in artifact.get("observations", [])
        if isinstance(row, Mapping) and row.get("observation_id")
    }
    evidence_catalog = {
        str(key): str(value)
        for key, value in (
            artifact.get("evidence_catalog", {}).items()
            if isinstance(artifact.get("evidence_catalog"), Mapping)
            else []
        )
    }
    valid_refs.update(evidence_catalog)
    valid_refs.update(observations)
    cited_refs = {
        str(reference)
        for claim in claims
        for reference in claim.get("evidence_refs", [])
    }
    unknown_refs = sorted(cited_refs - valid_refs)
    grounding_failures: list[str] = []
    for claim in claims:
        claim_tokens = _grounding_tokens(str(claim.get("claim") or ""))
        cited_text = " ".join(
            str(
                observations.get(str(reference), {}).get("excerpt")
                or evidence_catalog.get(str(reference))
                or ""
            )
            for reference in claim.get("evidence_refs", [])
        )
        if not cited_text:
            grounding_failures.append(
                "missing cited evidence text: "
                + str(claim.get("claim") or "")[:90]
            )
            continue
        overlap = claim_tokens & _grounding_tokens(cited_text)
        if not overlap:
            grounding_failures.append(str(claim.get("claim") or "")[:120])
    failed_tool = any(
        str(row.get("status") or "") not in {"ok", "success"} for row in tool_trace
    )
    known_gaps = [str(value) for value in result.get("known_gaps", [])]
    requires_gap = bool(case.grader.get("require_known_gap_after_failure"))
    gap_passed = not (requires_gap and failed_tool and not known_gaps)
    turns = int(artifact.get("turns") or 0)
    repair_count = int(artifact.get("repair_count") or 0)
    stopping_passed = (
        bool(artifact.get("final_output_valid"))
        and turns <= int(case.grader.get("max_turns") or 3)
        and repair_count <= int(case.grader.get("max_repairs") or 1)
    )
    must_finalize_turns = list(artifact.get("must_finalize_turns") or [])
    if must_finalize_turns:
        stopping_passed = stopping_passed and bool(
            artifact.get("trace")
            and artifact["trace"][-1].get("action") == "final"
        )
    brief_grade = _grade_brief(case, artifact.get("brief"))
    telemetry = artifact.get("telemetry") if isinstance(artifact.get("telemetry"), Mapping) else {}
    telemetry_fields = {
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "total_tokens",
        "latency_ms",
        "cost",
    }
    telemetry_passed = telemetry_fields.issubset(telemetry) and isinstance(
        telemetry.get("cost"), Mapping
    )
    return {
        "score": {
            "passed": score_passed,
            "actual": score,
            "actual_band": actual_band,
            "expected_band": expected_band,
            "allowed_interval": [minimum, maximum],
        },
        "preflight": {
            "passed": actual_preflight == expected_preflight,
            "actual": actual_preflight,
            "expected": expected_preflight,
        },
        "route": {
            "passed": actual_route == expected_route,
            "actual": actual_route,
            "expected": expected_route,
        },
        "tool_trajectory": {
            "passed": trajectory_passed,
            "actual_tools": actual_tools,
            "missing_required": missing_required,
            "unnecessary": unnecessary,
            "forbidden_families": forbidden,
            "outside_allowed_families": outside_allowed,
            "status_counts": status_counts,
            "expected_outcome": expected_outcome,
            "outcome_matches": outcome_matches,
        },
        "stopping_and_repair": {
            "passed": stopping_passed,
            "turns": turns,
            "repair_count": repair_count,
            "final_output_valid": bool(artifact.get("final_output_valid")),
            "must_finalize_turns": must_finalize_turns,
        },
        "evidence_references": {
            "passed": not unknown_refs,
            "unknown_refs": unknown_refs,
            "cited_refs": sorted(cited_refs),
        },
        "claim_grounding": {
            "passed": not grounding_failures,
            "lexical_failures": grounding_failures,
            "semantic_grader_hook": "not_configured",
        },
        "known_gaps": {
            "passed": gap_passed,
            "failed_tool": failed_tool,
            "known_gaps": known_gaps,
        },
        "telemetry": {
            "passed": telemetry_passed,
            "input_tokens": telemetry.get("input_tokens"),
            "output_tokens": telemetry.get("output_tokens"),
            "latency_ms": telemetry.get("latency_ms"),
            "cost": telemetry.get("cost"),
        },
        "brief": brief_grade,
    }


def _score_band(score: float) -> str:
    if score >= 70:
        return "high"
    if score >= 50:
        return "medium"
    return "low"


def _tool_outcome_matches(expected: Any, trace: list[Mapping[str, Any]]) -> bool:
    if expected in (None, "", "success"):
        return not trace or any(str(row.get("status")) == "ok" for row in trace)
    for row in trace:
        result = row.get("result") if isinstance(row.get("result"), Mapping) else {}
        if str(result.get("http_status")) == str(expected):
            return True
        if str(result.get("error") or "") == str(expected):
            return True
        if str(row.get("status") or "") == str(expected):
            return True
    return False


def _grounding_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9_\u4e00-\u9fff]+", text.lower())
        if len(token) >= 4
        and token
        not in {"with", "that", "this", "from", "have", "does", "recorded", "evidence"}
    }


def _grade_brief(
    case: EvalCaseLike, brief: Any
) -> dict[str, Any]:
    required = bool(case.model_input["requires_brief"])
    if not required:
        return {"passed": brief is None, "required": False, "checks": {}}
    if not isinstance(brief, Mapping):
        return {"passed": False, "required": True, "checks": {"present": False}}
    output = brief.get("output") if isinstance(brief.get("output"), Mapping) else {}
    serialized = json.dumps(output, ensure_ascii=False, sort_keys=True).lower()
    structure = bool(
        output.get("headline")
        and output.get("core_highlights")
        and output.get("use_cases")
        and isinstance(output.get("category"), Mapping)
    )
    chinese = bool(re.search(r"[\u4e00-\u9fff]", serialized))
    leakage_markers = (
        "tool_trace",
        "investigation_trace",
        "cache_key",
        "system prompt",
        "评分过程",
        "工具调用",
        "gold",
        "expected_",
    )
    no_process_leakage = not any(marker in serialized for marker in leakage_markers)
    decision = (brief.get("input") or {}).get("decision", {})
    caveat_required = bool(
        isinstance(decision, Mapping)
        and (decision.get("caveats") or decision.get("known_gaps"))
    )
    caveat_ok = not caveat_required or bool(output.get("caveat"))
    checks = {
        "present": True,
        "structure": structure,
        "contains_chinese": chinese,
        "no_internal_process_leakage": no_process_leakage,
        "caveat": caveat_ok,
        "semantic_grounding_hook": "not_configured",
    }
    return {
        "passed": all(value for value in checks.values() if isinstance(value, bool)),
        "required": True,
        "checks": checks,
    }
