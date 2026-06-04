from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable

from pipeline.decision.layer2_models import LEVEL_RANK, CandidateGroup
from pipeline.decision.schema import to_json, utc_now


DEFAULT_INVESTIGATOR_PROMPT_VERSION = "layer2-scoring-investigator-v1"
DEFAULT_BRIEF_PROMPT_VERSION = "layer2-scoring-investigator-brief-v2"


SCORING_INVESTIGATOR_SYSTEM_PROMPT = """
You are the Layer 2 Scoring Investigator for Hero Radar.

Your job is to decide whether this candidate is strategically worth reading
today for AI/product/developer-tool intelligence. First decide whether the
provided context is sufficient to score the candidate against the rubric. If
critical information is missing, request the smallest primitive tool calls
needed. Do not browse broadly. Do not call tools for facts already present in
context.

You have at most 3 investigation turns. Prefer final scoring when enough
evidence exists. If evidence remains weak after the budget, score with lower
confidence and list known gaps.

Reward real workflow unlocks, non-obvious technical mechanisms, concrete
product/repo/tool wedges, and credible momentum attached to substance. Do not
dismiss messy or gray-zone utilities solely because the category looks
low-status. If a candidate unlocks a real workflow, score workflow_shift
accordingly and express abuse/legal/quality concerns separately as
risk_penalty, caveats, and confidence.

Penalize pure news, standalone model releases without a workflow wrapper,
tutorials, resource lists, generic chatbot wrappers, ordinary tools without a
new workflow, and claims not grounded in evidence.

Return strict JSON. On each turn return either:
{"action":"use_tools","information_need":"...","tool_requests":[{"name":"...","arguments":{...}}]}
or:
{"action":"final","score":{...},"brief":{"should_print":false}}
"""


BRIEF_SYSTEM_PROMPT = """
You write the selected Hero Radar Layer 2 deepdive brief.

Use only the provided score, candidate context, investigation trace, and tool
trace. Do not ask for tools. Write concise Chinese for a product-intelligence
reader. Keep project names, repo names, and URLs in their original language.

Analyze the project itself, not the evidence trail. Core highlights must describe
the product/repo's own capability, interaction model, technical mechanism, or
product wedge. Do not put evidence quality, GitHub/HN/npm/Product Hunt traction,
alias proof, or "why this is credible" into core_highlights unless that evidence
directly explains a user-facing capability.

Use cases must be jobs for actual end users of the project, such as developers,
teams, creators, researchers, or operators. Do not write Hero Radar analyst tasks
like "track this project", "evaluate the trend", or "observe adoption". Evidence
and momentum are already shown separately in the feed.

Return strict JSON with:
{
  "category": {"primary":"...", "tags":["..."]},
  "headline": "Chinese one-sentence project thesis",
  "core_highlights": ["1-3 Chinese items about the project itself"],
  "use_cases": ["1-4 Chinese end-user jobs enabled by the project"],
  "caveat": "optional Chinese risk or uncertainty"
}
"""


ToolFn = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class InvestigatorLimits:
    max_investigation_turns: int = 3
    max_scoring_attempts: int = 3
    max_tool_calls_per_candidate: int = 8
    max_web_search_calls_per_candidate: int = 1
    max_github_file_calls_per_candidate: int = 3
    max_homepage_fetches_per_candidate: int = 1
    max_tool_result_chars: int = 6000


def aggregate_investigator_score(
    axes: dict[str, Any],
    *,
    object_type: str,
    is_product_or_repo: bool,
) -> float:
    normalized = _normalize_axes(axes)
    score = (
        0.25 * normalized["workflow_shift"]
        + 0.25 * normalized["technical_substance"]
        + 0.20 * normalized["product_market_fit"]
        + 0.15 * normalized["momentum"]
        + 0.15 * normalized["confidence"]
        - normalized["risk_penalty"]
        - normalized["derivative_news_penalty"]
    )
    score = round(max(0.0, min(100.0, score)), 2)
    if (
        max(
            normalized["workflow_shift"],
            normalized["technical_substance"],
            normalized["product_market_fit"],
        )
        < 70
    ):
        score = min(score, 69.0)
    if str(object_type) in {"news", "article"} and not is_product_or_repo:
        score = min(score, 55.0)
    return round(score, 2)


def score_with_investigator(
    conn: sqlite3.Connection,
    *,
    feed_run_id: str,
    groups: list[CandidateGroup],
    provider: Any,
    tools: dict[str, ToolFn],
    limits: InvestigatorLimits | None = None,
    prompt_version: str = DEFAULT_INVESTIGATOR_PROMPT_VERSION,
) -> list[dict[str, Any]]:
    active_limits = limits or InvestigatorLimits()
    results: list[dict[str, Any]] = []
    for group in groups:
        result = _score_one_group(
            conn,
            feed_run_id=feed_run_id,
            group=group,
            provider=provider,
            tools=tools,
            limits=active_limits,
            prompt_version=prompt_version,
        )
        results.append(result)
    conn.commit()
    return results


def select_deepdive_brief_candidates(
    scored: list[dict[str, Any]],
    *,
    min_score: float = 70,
    target_count: int = 8,
    max_count: int = 10,
) -> list[dict[str, Any]]:
    if max_count <= 0 or target_count <= 0:
        return []
    eligible = [
        row
        for row in scored
        if float(row.get("l2_score") or 0) >= float(min_score)
        and bool(row.get("should_print", True))
    ]
    ordered = sorted(
        eligible,
        key=lambda row: (
            -float(row.get("l2_score") or 0),
            -LEVEL_RANK.get(getattr(row.get("group"), "level", ""), 0),
            getattr(row.get("group"), "group_id", ""),
        ),
    )
    limit = min(max_count, target_count)
    selected = ordered[:limit]
    if len(selected) == limit and limit < max_count:
        cutoff = float(selected[-1].get("l2_score") or 0)
        for row in ordered[limit:max_count]:
            if float(row.get("l2_score") or 0) != cutoff:
                break
            selected.append(row)
    return selected


def generate_deepdive_briefs(
    conn: sqlite3.Connection,
    *,
    feed_run_id: str,
    selected: list[dict[str, Any]],
    provider: Any,
    prompt_version: str = DEFAULT_BRIEF_PROMPT_VERSION,
) -> list[dict[str, Any]]:
    briefs: list[dict[str, Any]] = []
    for rank, row in enumerate(selected, start=1):
        group = row["group"]
        payload = {
            "group_id": group.group_id,
            "candidate": group.context,
            "candidate_identity": _candidate_identity(group),
            "score": {
                key: row.get(key)
                for key in [
                    "object_type",
                    "is_product_or_repo",
                    "axes",
                    "l2_score",
                    "supporting_evidence",
                    "negative_evidence",
                    "known_gaps",
                    "primary_reason",
                    "rationale_short",
                    "topic_tags",
                    "caveats",
                ]
            },
            "investigation_trace": row.get("trace") or [],
            "tool_trace": row.get("tool_trace") or [],
            "schema": _brief_schema(),
        }
        response = provider.complete_json(
            task="layer2_scoring_investigator_brief",
            prompt_version=prompt_version,
            input_payload=payload,
            system_prompt=BRIEF_SYSTEM_PROMPT,
        )
        brief = normalize_deepdive_brief(response)
        cache_payload = {**payload, "brief": brief}
        cache_key = _cache_key(
            provider.provider_name,
            provider.model,
            prompt_version,
            cache_payload,
        )
        conn.execute(
            """
            insert or replace into l2_deepdive_briefs(
              feed_run_id, group_id, status, brief_json, language,
              provider, model, prompt_version, cache_key, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feed_run_id,
                group.group_id,
                "ok",
                to_json(brief),
                "zh",
                provider.provider_name,
                provider.model,
                prompt_version,
                cache_key,
                utc_now(),
            ),
        )
        conn.execute(
            """
            update l2_feed_items
            set deepdive_status = ?
            where feed_run_id = ? and group_id = ?
            """,
            ("briefed", feed_run_id, group.group_id),
        )
        conn.execute(
            """
            insert or replace into l2_feed_items(
              feed_run_id, group_id, section, rank, deepdive_status
            )
            values (?, ?, ?, ?, ?)
            """,
            (feed_run_id, group.group_id, "today_focus", rank, "briefed"),
        )
        briefs.append({"group": group, "brief": brief})
    conn.commit()
    return briefs


def normalize_deepdive_brief(response: dict[str, Any]) -> dict[str, Any]:
    category = response.get("category") if isinstance(response, dict) else {}
    if not isinstance(category, dict):
        category = {}
    brief = {
        "category": {
            "primary": str(category.get("primary") or "未分类")[:40],
            "tags": _string_list(category.get("tags"), 8, 40),
        },
        "headline": str(response.get("headline") or "")[:160],
        "core_highlights": _string_list(response.get("core_highlights"), 3, 220),
        "use_cases": _string_list(response.get("use_cases"), 4, 220),
    }
    caveat = str(response.get("caveat") or "").strip()
    if caveat:
        brief["caveat"] = caveat[:240]
    if not brief["headline"]:
        raise ValueError("deepdive brief missing headline")
    if not brief["core_highlights"]:
        raise ValueError("deepdive brief missing core_highlights")
    return brief


def _score_one_group(
    conn: sqlite3.Connection,
    *,
    feed_run_id: str,
    group: CandidateGroup,
    provider: Any,
    tools: dict[str, ToolFn],
    limits: InvestigatorLimits,
    prompt_version: str,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "known_facts": [],
        "open_questions": [],
        "tool_trace": [],
        "information_sufficiency": {
            "identity": "weak",
            "workflow_shift": "weak",
            "technical_substance": "weak",
            "product_market_fit": "weak",
            "momentum": "weak",
        },
    }
    turn_trace: list[dict[str, Any]] = []
    tool_trace: list[dict[str, Any]] = []
    tool_counts: dict[str, int] = {}
    total_tool_calls = 0
    final_response: dict[str, Any] | None = None

    for turn_index in range(1, max(1, limits.max_investigation_turns) + 1):
        payload = {
            "group_id": group.group_id,
            "candidate": group.context,
            "candidate_identity": _candidate_identity(group),
            "state": state,
            "available_tools": sorted(tools),
            "limits": _limits_payload(limits),
            "turn_index": turn_index,
            "schema": _turn_schema(),
        }
        response = provider.complete_json(
            task="layer2_scoring_investigator_turn",
            prompt_version=prompt_version,
            input_payload=payload,
            system_prompt=SCORING_INVESTIGATOR_SYSTEM_PROMPT,
        )
        action = str(response.get("action") or "").strip()
        turn_trace.append(
            {
                "turn": turn_index,
                "action": action,
                "information_need": str(response.get("information_need") or ""),
            }
        )
        if action == "final":
            final_response = response
            break
        if action != "use_tools":
            final_response = response
            break
        requests = response.get("tool_requests")
        if not isinstance(requests, list):
            requests = []
        for request in requests:
            trace_row, total_tool_calls = _run_tool_request(
                request if isinstance(request, dict) else {},
                tools=tools,
                limits=limits,
                tool_counts=tool_counts,
                total_tool_calls=total_tool_calls,
            )
            tool_trace.append(trace_row)
        state["tool_trace"] = tool_trace

    if final_response is None:
        raise ValueError("scoring investigator did not produce final score")
    normalized = _validate_or_repair_final(
        provider,
        prompt_version=prompt_version,
        group=group,
        state=state,
        turn_trace=turn_trace,
        tool_trace=tool_trace,
        response=final_response,
        limits=limits,
    )
    cache_payload = {
        "group_id": group.group_id,
        "evidence_hash": group.evidence_hash,
        "turn_trace": turn_trace,
        "tool_trace": tool_trace,
        "score": normalized,
    }
    cache_key = _cache_key(provider.provider_name, provider.model, prompt_version, cache_payload)
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
            normalized["l2_score"],
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
    conn.execute(
        """
        insert or replace into l2_scoring_investigations(
          feed_run_id, group_id, status, trace_json, tool_trace_json,
          provider, model, prompt_version, cache_key, created_at
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            feed_run_id,
            group.group_id,
            "ok",
            to_json(turn_trace),
            to_json(tool_trace),
            provider.provider_name,
            provider.model,
            prompt_version,
            cache_key,
            utc_now(),
        ),
    )
    return {"group": group, **normalized, "tool_trace": tool_trace, "trace": turn_trace}


def _validate_or_repair_final(
    provider: Any,
    *,
    prompt_version: str,
    group: CandidateGroup,
    state: dict[str, Any],
    turn_trace: list[dict[str, Any]],
    tool_trace: list[dict[str, Any]],
    response: dict[str, Any],
    limits: InvestigatorLimits,
) -> dict[str, Any]:
    try:
        return _validate_final_response(response)
    except ValueError as exc:
        if limits.max_scoring_attempts < 2:
            raise
        repair_payload = {
            "group_id": group.group_id,
            "candidate": group.context,
            "state": state,
            "turn_trace": turn_trace,
            "tool_trace": tool_trace,
            "validation_error": str(exc),
            "previous_response_shape": _response_shape(response),
            "instruction": "Return a complete corrected action=final scoring JSON object.",
            "schema": _turn_schema(),
        }
        repaired = provider.complete_json(
            task="layer2_scoring_investigator_repair",
            prompt_version=prompt_version,
            input_payload=repair_payload,
            system_prompt=SCORING_INVESTIGATOR_SYSTEM_PROMPT,
        )
        return _validate_final_response(repaired)


def _validate_final_response(response: dict[str, Any]) -> dict[str, Any]:
    if str(response.get("action") or "") != "final":
        raise ValueError("scoring investigator final response must use action=final")
    score = response.get("score")
    if not isinstance(score, dict):
        raise ValueError("scoring investigator final response missing score")
    axes = score.get("axes")
    if not isinstance(axes, dict):
        raise ValueError("scoring investigator score missing axes")
    normalized_axes = _normalize_axes(axes)
    object_type = str(score.get("object_type") or "unknown")[:40]
    is_product_or_repo = bool(score.get("is_product_or_repo", False))
    l2_score = aggregate_investigator_score(
        normalized_axes,
        object_type=object_type,
        is_product_or_repo=is_product_or_repo,
    )
    return {
        "object_type": object_type,
        "is_product_or_repo": is_product_or_repo,
        "axes": normalized_axes,
        "l2_score": l2_score,
        "supporting_evidence": _string_list(score.get("supporting_evidence"), 8, 240),
        "negative_evidence": _string_list(score.get("negative_evidence"), 8, 240),
        "known_gaps": _string_list(score.get("known_gaps"), 8, 160),
        "primary_reason": str(score.get("primary_reason") or "Signal")[:80],
        "rationale_short": str(score.get("rationale_short") or "")[:1000],
        "topic_tags": _string_list(score.get("topic_tags"), 8, 40),
        "caveats": _string_list(score.get("caveats"), 8, 240),
        "should_print": bool(score.get("should_print", False)),
        "brief": response.get("brief") if isinstance(response.get("brief"), dict) else {},
    }


def _run_tool_request(
    request: dict[str, Any],
    *,
    tools: dict[str, ToolFn],
    limits: InvestigatorLimits,
    tool_counts: dict[str, int],
    total_tool_calls: int,
) -> tuple[dict[str, Any], int]:
    name = str(request.get("name") or "")
    arguments = request.get("arguments") if isinstance(request.get("arguments"), dict) else {}
    family = _tool_family(name)
    if total_tool_calls >= max(0, limits.max_tool_calls_per_candidate):
        return _trace_row(name, arguments, family, "budget_exceeded", {}), total_tool_calls
    if not _within_family_budget(tool_counts, family, limits):
        return _trace_row(name, arguments, family, "budget_exceeded", {}), total_tool_calls
    if name not in tools:
        return _trace_row(name, arguments, family, "unavailable", {}), total_tool_calls
    total_tool_calls += 1
    tool_counts[family] = tool_counts.get(family, 0) + 1
    try:
        result = tools[name](arguments)
    except Exception as exc:
        return (
            {
                **_trace_row(name, arguments, family, "error", {}),
                "error_type": type(exc).__name__,
                "error": str(exc)[:800],
            },
            total_tool_calls,
        )
    return (
        _trace_row(
            name,
            arguments,
            family,
            str(result.get("status") or "ok") if isinstance(result, dict) else "ok",
            _trim_result(result if isinstance(result, dict) else {"result": result}, limits.max_tool_result_chars),
        ),
        total_tool_calls,
    )


def _within_family_budget(
    counts: dict[str, int], family: str, limits: InvestigatorLimits
) -> bool:
    caps = {
        "web_search": limits.max_web_search_calls_per_candidate,
        "github_file": limits.max_github_file_calls_per_candidate,
        "homepage": limits.max_homepage_fetches_per_candidate,
    }
    cap = caps.get(family)
    return cap is None or counts.get(family, 0) < max(0, cap)


def _tool_family(name: str) -> str:
    if name == "web_search":
        return "web_search"
    if name == "fetch_github_file":
        return "github_file"
    if name == "fetch_homepage_or_docs":
        return "homepage"
    if name == "fetch_github_readme":
        return "github_readme"
    if name == "read_evidence_rows":
        return "evidence"
    return "generic"


def _trace_row(
    name: str,
    arguments: dict[str, Any],
    family: str,
    status: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "tool": name,
        "arguments": arguments,
        "family": family,
        "status": status,
        "result": result,
    }


def _normalize_axes(axes: dict[str, Any]) -> dict[str, float]:
    return {
        "workflow_shift": _clamp_float(axes.get("workflow_shift"), 0, 100),
        "technical_substance": _clamp_float(axes.get("technical_substance"), 0, 100),
        "product_market_fit": _clamp_float(axes.get("product_market_fit"), 0, 100),
        "momentum": _clamp_float(axes.get("momentum"), 0, 100),
        "confidence": _clamp_float(axes.get("confidence"), 0, 100),
        "risk_penalty": _clamp_float(axes.get("risk_penalty", 0), 0, 25),
        "derivative_news_penalty": _clamp_float(
            axes.get("derivative_news_penalty", 0), 0, 25
        ),
    }


def _clamp_float(value: Any, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"expected numeric axis value, got {value!r}") from exc
    return max(minimum, min(maximum, number))


def _string_list(value: Any, limit: int, item_chars: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:item_chars] for item in value if str(item).strip()][:limit]


def _trim_result(result: dict[str, Any], max_chars: int) -> dict[str, Any]:
    text = to_json(result)
    if len(text) <= max_chars:
        return result
    return {"truncated": True, "text": text[:max_chars]}


def _candidate_identity(group: CandidateGroup) -> dict[str, Any]:
    return {
        "group_id": group.group_id,
        "canonical_entity_id": group.canonical_entity_id,
        "canonical_name": group.canonical_name,
        "canonical_key": group.canonical_key,
        "canonical_link": group.canonical_link,
        "level": group.level,
        "source_families": group.source_families,
        "evidence_hash": group.evidence_hash,
    }


def _limits_payload(limits: InvestigatorLimits) -> dict[str, int]:
    return {
        "max_investigation_turns": limits.max_investigation_turns,
        "max_tool_calls_per_candidate": limits.max_tool_calls_per_candidate,
        "max_web_search_calls_per_candidate": limits.max_web_search_calls_per_candidate,
        "max_github_file_calls_per_candidate": limits.max_github_file_calls_per_candidate,
        "max_homepage_fetches_per_candidate": limits.max_homepage_fetches_per_candidate,
    }


def _turn_schema() -> dict[str, Any]:
    return {
        "action": "use_tools|final",
        "information_need": "string",
        "tool_requests": [{"name": "string", "arguments": {}}],
        "score": {
            "object_type": "product|repo|package|research_tool|model_release|article|news|unknown",
            "is_product_or_repo": "boolean",
            "axes": {
                "workflow_shift": "0..100",
                "technical_substance": "0..100",
                "product_market_fit": "0..100",
                "momentum": "0..100",
                "confidence": "0..100",
                "risk_penalty": "0..25",
                "derivative_news_penalty": "0..25",
            },
            "supporting_evidence": ["string"],
            "negative_evidence": ["string"],
            "known_gaps": ["string"],
            "primary_reason": "string",
            "rationale_short": "string",
            "topic_tags": ["string"],
            "caveats": ["string"],
            "should_print": "boolean",
        },
    }


def _brief_schema() -> dict[str, Any]:
    return {
        "category": {"primary": "string", "tags": ["string"]},
        "headline": "Chinese one-line brief headline",
        "core_highlights": [
            "1-3 concise Chinese items around workflow, technical substance, or product wedge"
        ],
        "use_cases": ["1-4 concise Chinese use cases"],
        "caveat": "optional Chinese caveat",
    }


def _response_shape(response: dict[str, Any]) -> dict[str, str]:
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
