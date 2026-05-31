from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from typing import Any

from pipeline.decision.entity_resolution import entity_id_for_key as stage_a_entity_id_for_key
from pipeline.decision.entity_resolution import normalize_github_repo
from pipeline.decision.llm_cache import (
    cache_key_for,
    get_cached_response,
    store_cached_response,
)


X_STAGE1_PROMPT_VERSION = "x-stage1-v1"
X_STAGE2_PROMPT_VERSION = "x-stage2-v1"
X_STAGE1_TASK = "x_stage1"
X_STAGE2_TASK = "x_stage2"
ENTITY_CONFIDENCE_VALUES = {"linked", "exact_handle", "fuzzy_name"}
EXPRESSION_STRENGTH_VALUES = {
    "neutral",
    "recommendation",
    "strong_recommendation",
    "adoption_or_usage",
    "strong_emotion",
    "mixed",
}
X_TIER_VALUES = {"none", "watch", "potential", "high"}
GENERIC_KNOWN_TERMS = {"openai", "claude", "mcp"}
GITHUB_RE = re.compile(
    r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)


def _parse_time(value: str) -> dt.datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _json_loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def entity_id_for_key(entity_key: str) -> str:
    return stage_a_entity_id_for_key(entity_key)


def github_key_from_text(text: str) -> str | None:
    match = GITHUB_RE.search(text or "")
    if not match:
        return None
    owner = match.group(1)
    repo = match.group(2).removesuffix(".git").rstrip(".,;:!?)\"]}'")
    return normalize_github_repo(owner, repo)


def _x_tweets_select_fields(conn: sqlite3.Connection) -> str:
    columns = {row[1] for row in conn.execute("pragma table_info(x_tweets_store)")}
    imported_expr = "imported_at"
    if "imported_at" not in columns:
        imported_expr = "first_seen_at" if "first_seen_at" in columns else "created_at"
    return (
        "tweet_id, author_username, text, url, created_at, "
        f"{imported_expr} as imported_at, raw_json"
    )


def candidate_tweets(
    conn: sqlite3.Connection,
    *,
    now: str,
    limit: int,
) -> list[dict[str, Any]]:
    now_dt = _parse_time(now)
    since_dt = now_dt - dt.timedelta(days=7)
    fields = _x_tweets_select_fields(conn)
    rows = conn.execute(
        f"""
        select {fields}
        from x_tweets_store
        order by created_at desc
        """
    ).fetchall()
    tweets: list[dict[str, Any]] = []
    for row in rows:
        created_at = _parse_time(row[4])
        if created_at < since_dt or created_at > now_dt:
            continue
        text = row[2] or ""
        github_key = github_key_from_text(text)
        tweets.append(
            {
                "tweet_id": row[0],
                "author_username": row[1],
                "text": text,
                "url": row[3],
                "created_at": row[4],
                "imported_at": row[5],
                "raw": _json_loads(row[6]),
                "deterministic_hints": (
                    [{"entity_key": github_key, "entity_confidence": "linked"}]
                    if github_key
                    else []
                ),
            }
        )
        if len(tweets) >= limit:
            break
    return tweets


def build_x_stage1_prompt_payload(tweets: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "task": X_STAGE1_TASK,
        "prompt_version": X_STAGE1_PROMPT_VERSION,
        "instructions": (
            "Return JSON. Triage each tweet for whether it is about a concrete "
            "repo, package, product, or project. Generic known terms such as "
            "OpenAI, Claude, and MCP require a concrete binding before they count."
        ),
        "allowed_entity_confidence": sorted(ENTITY_CONFIDENCE_VALUES),
        "allowed_expression_strength": sorted(EXPRESSION_STRENGTH_VALUES),
        "tweets": [
            {
                "tweet_id": tweet["tweet_id"],
                "author_username": tweet["author_username"],
                "text": tweet["text"],
                "url": tweet.get("url"),
                "created_at": tweet["created_at"],
                "deterministic_hints": tweet.get("deterministic_hints", []),
            }
            for tweet in tweets
        ],
        "output_schema": {
            "triage": [
                {
                    "tweet_id": "string",
                    "about_concrete_project": "boolean",
                    "closer_look": "boolean",
                    "project_refs": [
                        {
                            "entity_key": "github:owner/repo|domain:example.com|npm:pkg|name:project",
                            "entity_name": "string",
                            "entity_confidence": "linked|exact_handle|fuzzy_name",
                            "confidence": "number from 0 to 1",
                        }
                    ],
                    "expression_strength": "neutral|recommendation|strong_recommendation|adoption_or_usage|strong_emotion|mixed",
                    "evidence_quote": "short quote from tweet",
                    "reason": "string",
                }
            ]
        },
    }


def build_x_stage2_prompt_payload(
    aggregate: dict[str, Any],
    tweets: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "task": X_STAGE2_TASK,
        "prompt_version": X_STAGE2_PROMPT_VERSION,
        "instructions": (
            "Return JSON. Judge the X-only source signal for this concrete entity. "
            "Do not let engagement counts drive the tier. Cite tweet ids. Generic "
            "terms without concrete repo/domain/package/product binding are none. "
            "Two credible authors citing or trying the same linked project is "
            "potential, not high. High tier requires exceptional adoption, multiple "
            "independent strong recommendations, or unusually strong cross-source "
            "confirmation beyond two credible tweets."
        ),
        "allowed_x_tier": ["none", "watch", "potential", "high"],
        "allowed_entity_confidence": sorted(ENTITY_CONFIDENCE_VALUES),
        "allowed_expression_strength": sorted(EXPRESSION_STRENGTH_VALUES),
        "aggregate": {
            "entity_id": aggregate["entity_id"],
            "window": aggregate["window"],
            "distinct_authors": aggregate["distinct_authors"],
            "credible_authors": aggregate["credible_authors"],
            "mention_count": aggregate["mention_count"],
            "mention_acceleration": aggregate.get("mention_acceleration"),
            "source_refs": aggregate.get("source_refs", []),
        },
        "tweets": [
            {
                "tweet_id": tweet["tweet_id"],
                "author_username": tweet["author_username"],
                "text": tweet["text"],
                "url": tweet.get("url"),
                "created_at": tweet["created_at"],
            }
            for tweet in tweets
        ],
        "output_schema": {
            "entity_key": "github:owner/repo|domain:example.com|npm:pkg|name:project|term:name",
            "x_tier": "none|watch|potential|high",
            "entity_confidence": "linked|exact_handle|fuzzy_name",
            "x_expression_strength": "neutral|recommendation|strong_recommendation|adoption_or_usage|strong_emotion|mixed",
            "cited_tweet_ids": ["tweet id strings"],
            "rationale": "string",
            "cross_source_notes": ["string"],
        },
    }


def _require_fields(payload: dict[str, Any], fields: set[str], label: str) -> None:
    missing = sorted(fields - payload.keys())
    if missing:
        raise ValueError(f"missing {label} fields: {', '.join(missing)}")


def _validate_project_ref(ref: Any) -> None:
    if not isinstance(ref, dict):
        raise ValueError("project_ref must be an object")
    _require_fields(
        ref,
        {"entity_key", "entity_name", "entity_confidence", "confidence"},
        "project_ref",
    )
    if not isinstance(ref["entity_key"], str) or ":" not in ref["entity_key"]:
        raise ValueError("project_ref entity_key must be typed")
    if not isinstance(ref["entity_name"], str):
        raise ValueError("project_ref entity_name must be a string")
    if ref["entity_confidence"] not in ENTITY_CONFIDENCE_VALUES:
        raise ValueError("invalid project_ref entity_confidence")
    confidence = ref["confidence"]
    if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        raise ValueError("project_ref confidence must be 0..1")


def validate_x_stage1_output(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("triage"), list):
        raise ValueError("stage1 output must contain triage list")
    required = {
        "tweet_id",
        "about_concrete_project",
        "closer_look",
        "project_refs",
        "expression_strength",
        "evidence_quote",
        "reason",
    }
    for item in payload["triage"]:
        if not isinstance(item, dict):
            raise ValueError("triage item must be an object")
        _require_fields(item, required, "triage")
        if not isinstance(item["tweet_id"], str):
            raise ValueError("triage tweet_id must be a string")
        if not isinstance(item["about_concrete_project"], bool):
            raise ValueError("about_concrete_project must be boolean")
        if not isinstance(item["closer_look"], bool):
            raise ValueError("closer_look must be boolean")
        if not isinstance(item["project_refs"], list):
            raise ValueError("project_refs must be a list")
        if item["expression_strength"] not in EXPRESSION_STRENGTH_VALUES:
            raise ValueError("invalid expression_strength")
        if not isinstance(item["evidence_quote"], str):
            raise ValueError("evidence_quote must be a string")
        if not isinstance(item["reason"], str):
            raise ValueError("reason must be a string")
        for ref in item["project_refs"]:
            _validate_project_ref(ref)
    return payload


def validate_x_stage2_output(
    payload: dict[str, Any],
    *,
    strict_for_promotion: bool = False,
) -> dict[str, Any]:
    required = {
        "entity_key",
        "x_tier",
        "entity_confidence",
        "x_expression_strength",
        "cited_tweet_ids",
        "rationale",
        "cross_source_notes",
    }
    if not isinstance(payload, dict):
        raise ValueError("stage2 output must be an object")
    _require_fields(payload, required, "stage2")
    if not isinstance(payload["entity_key"], str) or ":" not in payload["entity_key"]:
        raise ValueError("entity_key must be typed")
    if payload["x_tier"] not in X_TIER_VALUES:
        raise ValueError("invalid x_tier")
    if payload["entity_confidence"] not in ENTITY_CONFIDENCE_VALUES:
        raise ValueError("invalid entity_confidence")
    if payload["x_expression_strength"] not in EXPRESSION_STRENGTH_VALUES:
        raise ValueError("invalid x_expression_strength")
    if not isinstance(payload["cited_tweet_ids"], list) or not all(
        isinstance(tweet_id, str) for tweet_id in payload["cited_tweet_ids"]
    ):
        raise ValueError("cited_tweet_ids must be a list of strings")
    if not isinstance(payload["rationale"], str):
        raise ValueError("rationale must be a string")
    if not isinstance(payload["cross_source_notes"], list) or not all(
        isinstance(note, str) for note in payload["cross_source_notes"]
    ):
        raise ValueError("cross_source_notes must be a list of strings")
    if strict_for_promotion and payload["x_tier"] != "none":
        if not payload["cited_tweet_ids"]:
            raise ValueError("promoting x_tier requires cited_tweet_ids")
        if payload["x_tier"] in {"potential", "high"} and payload[
            "entity_confidence"
        ] not in {"linked", "exact_handle"}:
            raise ValueError("potential/high requires linked or exact entity confidence")
    return payload


def accepted_x_tier(output: dict[str, Any]) -> str:
    tier = output["x_tier"]
    entity_key = output["entity_key"]
    confidence = output["entity_confidence"]
    citations = output["cited_tweet_ids"]
    if tier == "none":
        return "none"
    if not citations:
        return "none"
    if entity_key.lower().startswith("term:"):
        term = entity_key.split(":", 1)[1].strip().lower()
        if term in GENERIC_KNOWN_TERMS:
            return "none"
    if confidence == "fuzzy_name" and tier in {"potential", "high"}:
        return "watch"
    return tier


def _provider_name(provider: Any) -> str:
    return str(getattr(provider, "provider_name", provider.__class__.__name__.lower()))


def _provider_model(provider: Any) -> str:
    return str(getattr(provider, "model", "unknown"))


def _complete_with_cache(
    conn: sqlite3.Connection,
    *,
    provider: Any,
    task: str,
    prompt_version: str,
    input_payload: dict[str, Any],
    system_prompt: str,
) -> dict[str, Any]:
    provider_name = _provider_name(provider)
    model = _provider_model(provider)
    cache_key = cache_key_for(
        provider=provider_name,
        model=model,
        prompt_version=prompt_version,
        task=task,
        input_payload=input_payload,
    )
    cached = get_cached_response(conn, cache_key)
    if cached and cached["status"] == "ok":
        return dict(cached["response_json"])
    request_payload = {"system_prompt": system_prompt, "input_payload": input_payload}
    try:
        response = provider.complete_json(
            task=task,
            prompt_version=prompt_version,
            input_payload=input_payload,
            system_prompt=system_prompt,
        )
        if task == X_STAGE1_TASK:
            validate_x_stage1_output(response)
        else:
            validate_x_stage2_output(response)
    except Exception as exc:
        store_cached_response(
            conn,
            provider=provider_name,
            model=model,
            prompt_version=prompt_version,
            task=task,
            input_payload=input_payload,
            request_payload=request_payload,
            response_payload={"error": str(exc)},
            status="error",
            error=str(exc),
        )
        raise
    store_cached_response(
        conn,
        provider=provider_name,
        model=model,
        prompt_version=prompt_version,
        task=task,
        input_payload=input_payload,
        request_payload=request_payload,
        response_payload=response,
        status="ok",
    )
    return response


def _chunks(values: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    if size <= 0:
        raise ValueError("batch_size must be positive")
    return [values[index : index + size] for index in range(0, len(values), size)]


def _upsert_entity_mention(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    run_id: str,
    window: str,
    distinct_authors: int,
    credible_authors: int,
    mention_count: int,
    mention_acceleration: float,
    source_refs: list[str],
) -> None:
    conn.execute(
        """
        insert into entity_mentions(
            entity_id, run_id, window, distinct_authors, credible_authors,
            mention_count, mention_acceleration, source_refs_json
        )
        values (?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(run_id, entity_id, window) do update set
            distinct_authors = excluded.distinct_authors,
            credible_authors = excluded.credible_authors,
            mention_count = excluded.mention_count,
            mention_acceleration = excluded.mention_acceleration,
            source_refs_json = excluded.source_refs_json
        """,
        (
            entity_id,
            run_id,
            window,
            distinct_authors,
            credible_authors,
            mention_count,
            mention_acceleration,
            json.dumps(source_refs, ensure_ascii=False),
        ),
    )


def run_x_stage1(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    provider: Any,
    credible_handles: set[str],
    now: str,
    limit: int,
    batch_size: int,
) -> dict[str, Any]:
    tweets = candidate_tweets(conn, now=now, limit=limit)
    tweet_by_id = {tweet["tweet_id"]: tweet for tweet in tweets}
    credible = {handle.lower() for handle in credible_handles}
    system_prompt = (
        "You are a bounded X source triage classifier. Return only JSON. "
        "Prefer concrete repo/domain/package/project references over generic terms."
    )
    mentions: dict[str, list[dict[str, Any]]] = {}
    triaged = 0
    for batch in _chunks(tweets, batch_size):
        input_payload = build_x_stage1_prompt_payload(batch)
        output = _complete_with_cache(
            conn,
            provider=provider,
            task=X_STAGE1_TASK,
            prompt_version=X_STAGE1_PROMPT_VERSION,
            input_payload=input_payload,
            system_prompt=system_prompt,
        )
        for item in output["triage"]:
            triaged += 1
            if not item["closer_look"]:
                continue
            tweet = tweet_by_id.get(item["tweet_id"])
            if tweet is None:
                continue
            for ref in item["project_refs"]:
                entity_id = entity_id_for_key(ref["entity_key"])
                mentions.setdefault(entity_id, []).append(
                    {
                        "tweet": tweet,
                        "entity_key": ref["entity_key"],
                        "entity_name": ref["entity_name"],
                        "entity_confidence": ref["entity_confidence"],
                        "expression_strength": item["expression_strength"],
                    }
                )
    now_dt = _parse_time(now)
    total_mentions = 0
    for entity_id, refs in mentions.items():
        for window, start_dt in (
            ("24h", now_dt - dt.timedelta(hours=24)),
            ("7d", now_dt - dt.timedelta(days=7)),
        ):
            window_refs = [
                ref for ref in refs if _parse_time(ref["tweet"]["created_at"]) >= start_dt
            ]
            if not window_refs:
                continue
            authors = {ref["tweet"]["author_username"].lower() for ref in window_refs}
            credible_authors = {
                ref["tweet"]["author_username"].lower()
                for ref in window_refs
                if ref["tweet"]["author_username"].lower() in credible
            }
            source_refs = sorted({f"tweet:{ref['tweet']['tweet_id']}" for ref in window_refs})
            mention_count = len(window_refs)
            mention_acceleration = float(mention_count)
            _upsert_entity_mention(
                conn,
                entity_id=entity_id,
                run_id=run_id,
                window=window,
                distinct_authors=len(authors),
                credible_authors=len(credible_authors),
                mention_count=mention_count,
                mention_acceleration=mention_acceleration,
                source_refs=source_refs,
            )
            if window == "24h":
                total_mentions += mention_count
    conn.commit()
    return {"triaged": triaged, "mentions": total_mentions, "entities": len(mentions)}


def _source_refs(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _tweet_rows_for_refs(conn: sqlite3.Connection, source_refs: list[str]) -> list[dict[str, Any]]:
    tweet_ids = [ref.split(":", 1)[1] for ref in source_refs if ref.startswith("tweet:")]
    if not tweet_ids:
        return []
    placeholders = ",".join("?" for _ in tweet_ids)
    fields = _x_tweets_select_fields(conn)
    rows = conn.execute(
        f"""
        select {fields}
        from x_tweets_store
        where tweet_id in ({placeholders})
        """,
        tuple(tweet_ids),
    ).fetchall()
    by_id = {
        row[0]: {
            "tweet_id": row[0],
            "author_username": row[1],
            "text": row[2] or "",
            "url": row[3],
            "created_at": row[4],
            "imported_at": row[5],
            "raw": _json_loads(row[6]),
        }
        for row in rows
    }
    return [by_id[tweet_id] for tweet_id in tweet_ids if tweet_id in by_id]


def candidate_entity_mentions(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        select entity_id, window, distinct_authors, credible_authors, mention_count,
               mention_acceleration, source_refs_json
        from entity_mentions
        where run_id = ? and window = '24h'
          and (credible_authors >= 2 or mention_count >= 3 or mention_acceleration >= 2)
        order by credible_authors desc, mention_count desc, entity_id
        limit ?
        """,
        (run_id, limit),
    ).fetchall()
    return [
        {
            "entity_id": row[0],
            "window": row[1],
            "distinct_authors": row[2],
            "credible_authors": row[3],
            "mention_count": row[4],
            "mention_acceleration": row[5],
            "source_refs": _source_refs(row[6]),
        }
        for row in rows
    ]


def _signal_label(tier: str) -> str:
    if tier == "none":
        return "noise"
    if tier == "high":
        return "high_potential"
    return tier


def _insert_x_evidence(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    now: str,
    aggregate: dict[str, Any],
    output: dict[str, Any],
) -> None:
    entity_key = output["entity_key"]
    accepted_tier = accepted_x_tier(output)
    raw_refs = ",".join(output["cited_tweet_ids"])
    raw_url_or_ref = ",".join(f"tweet:{tweet_id}" for tweet_id in output["cited_tweet_ids"])
    if not raw_url_or_ref:
        raw_url_or_ref = ",".join(aggregate.get("source_refs", []))
    common = {
        "entity_id": aggregate["entity_id"],
        "canonical_entity": entity_key,
        "alias": entity_key,
        "source": "x_tweets",
        "event_at": now,
        "relative_to_reference": None,
        "family": "x_social",
        "rule_version": X_STAGE2_PROMPT_VERSION,
        "historical_safety": "llm_source_classifier",
        "raw_url_or_ref": raw_url_or_ref,
        "run_id": run_id,
    }
    metrics: list[tuple[str, str, str, str]] = [
        (
            "distinct_authors",
            str(aggregate["distinct_authors"]),
            "context",
            f"X aggregate for {entity_key}",
        ),
        (
            "credible_authors",
            str(aggregate["credible_authors"]),
            "context",
            f"X aggregate for {entity_key}",
        ),
        (
            "mention_count",
            str(aggregate["mention_count"]),
            "context",
            f"X aggregate for {entity_key}",
        ),
        (
            "mention_acceleration",
            str(aggregate.get("mention_acceleration") or 0),
            "context",
            f"X aggregate for {entity_key}",
        ),
        (
            "x_expression_strength",
            output["x_expression_strength"],
            "context",
            output["rationale"],
        ),
        ("x_llm_summary", output["rationale"], "context", output["rationale"]),
        (
            "x_tier",
            accepted_tier,
            _signal_label(accepted_tier),
            (
                f"accepted={accepted_tier}; raw={output['x_tier']}; "
                f"confidence={output['entity_confidence']}; citations={raw_refs}; "
                f"{output['rationale']}"
            ),
        ),
    ]
    conn.executemany(
        """
        insert into evidence_rows(
            entity_id, canonical_entity, alias, source, event_at,
            relative_to_reference, metric_name, metric_value, family, rule_id,
            rule_version, signal_label, historical_safety, note, raw_url_or_ref,
            run_id
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                common["entity_id"],
                common["canonical_entity"],
                common["alias"],
                common["source"],
                common["event_at"],
                common["relative_to_reference"],
                metric_name,
                metric_value,
                common["family"],
                f"x_social_{metric_name}",
                common["rule_version"],
                signal_label,
                common["historical_safety"],
                note,
                common["raw_url_or_ref"],
                common["run_id"],
            )
            for metric_name, metric_value, signal_label, note in metrics
        ],
    )


def run_x_stage2(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    provider: Any,
    now: str,
    limit: int,
) -> dict[str, Any]:
    system_prompt = (
        "You are a bounded X source tier classifier. Return only JSON. "
        "Potential requires citations and a linked or exact entity. Fuzzy "
        "name-only outputs cannot be sole Potential evidence."
    )
    tiered = 0
    for aggregate in candidate_entity_mentions(conn, run_id=run_id, limit=limit):
        tweets = _tweet_rows_for_refs(conn, aggregate["source_refs"])
        input_payload = build_x_stage2_prompt_payload(aggregate, tweets)
        output = _complete_with_cache(
            conn,
            provider=provider,
            task=X_STAGE2_TASK,
            prompt_version=X_STAGE2_PROMPT_VERSION,
            input_payload=input_payload,
            system_prompt=system_prompt,
        )
        validate_x_stage2_output(output)
        _insert_x_evidence(
            conn,
            run_id=run_id,
            now=now,
            aggregate=aggregate,
            output=output,
        )
        tiered += 1
    conn.commit()
    return {"tiered": tiered}
