from __future__ import annotations

import sqlite3
from typing import Any

from pipeline.decision.cache import api_cache_key, get_api_cache, put_api_cache, stable_hash
from pipeline.decision.resolver import normalize_resolved_link


RESEARCH_SOURCE = "agentic_link_research"
RESEARCH_WINDOW = "candidate_link"
PROMPT_VERSION = "agentic-link-research-v1"
SYSTEM_PROMPT = (
    "You identify the official project link for one candidate. Prefer GitHub repo, "
    "then official domain, then npm package. Use search only when current observations "
    "are insufficient. Return strict JSON only. Do not invent links. If unsure after "
    "max rounds, return give_up."
)


def _cache_key(
    entity_key: str,
    evidence_context: dict[str, Any],
    max_rounds: int,
    max_results: int,
) -> tuple[str, str]:
    input_hash = stable_hash(
        {
            "entity_key": entity_key,
            "evidence_context": evidence_context,
            "max_rounds": max_rounds,
            "max_results": max_results,
            "prompt_version": PROMPT_VERSION,
        }
    )
    return (
        api_cache_key(
            source=RESEARCH_SOURCE,
            external_id=entity_key,
            window=RESEARCH_WINDOW,
            input_hash=input_hash,
        ),
        input_hash,
    )


def research_candidate_link(
    conn: sqlite3.Connection,
    *,
    entity_key: str,
    evidence_context: dict[str, Any],
    provider: Any,
    search_client: Any,
    max_rounds: int = 3,
    max_results: int = 5,
) -> dict[str, Any]:
    rounds = max(1, min(int(max_rounds or 3), 3))
    results_limit = max(1, int(max_results or 5))
    cache_key, input_hash = _cache_key(entity_key, evidence_context, rounds, results_limit)
    cached = get_api_cache(conn, cache_key)
    if cached:
        return cached

    observations: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    rounds_run = 0
    for round_index in range(1, rounds + 1):
        rounds_run = round_index
        action = provider.complete_json(
            task="agentic_link_research",
            prompt_version=PROMPT_VERSION,
            system_prompt=SYSTEM_PROMPT,
            input_payload={
                "entity_key": entity_key,
                "evidence_context": evidence_context,
                "observations": observations,
                "round_index": round_index,
                "max_rounds": rounds,
                "schema": {
                    "action": "search|final|give_up",
                    "query": "string",
                    "selected": {
                        "type": "github|domain|npm",
                        "key": "string",
                        "url": "string",
                        "confidence": "0..1",
                    },
                    "reason": "string",
                },
            },
        )
        action_name = str(action.get("action") or "").strip()
        if action_name == "search":
            query = str(action.get("query") or "").strip()
            raw_results = search_client.search(query, limit=results_limit) if query else []
            observations.append({"query": query, "results": list(raw_results)[:results_limit]})
            continue
        if action_name == "final":
            selected = action.get("selected") or {}
            link = normalize_resolved_link(selected) if isinstance(selected, dict) else None
            if link:
                links.append(link)
            break
        if action_name == "give_up":
            break

    response = {
        "entity_key": entity_key,
        "resolved_links": links,
        "source": RESEARCH_SOURCE,
        "rounds": rounds_run,
        "observations": observations,
    }
    put_api_cache(
        conn,
        cache_key=cache_key,
        source=RESEARCH_SOURCE,
        external_id=entity_key,
        window=RESEARCH_WINDOW,
        input_hash=input_hash,
        response=response,
        status="ok",
    )
    return response
