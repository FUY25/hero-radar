from __future__ import annotations

import json
import re
import sqlite3
import urllib.parse
from typing import Any

from pipeline.decision.bounded_parallel import bounded_parallel_map
from pipeline.decision.entity_resolution import (
    SHARED_DOMAIN_BLOCKLIST,
    entity_id_for_key,
    normalize_github_repo,
)
from pipeline.decision.llm_cache import (
    cache_key_for,
    get_cached_response,
    store_cached_response,
)


PROMPT_VERSION = "hn-projectness-v1"
TASK = "hn_classifier"
HN_SOURCES = {"hn_firebase", "hn_algolia"}
PROJECTNESS_ORDER = [
    "project",
    "package",
    "company_product",
    "news_article",
    "topic_discussion",
    "research_paper",
    "unknown",
]
PROJECTNESS_VALUES = set(PROJECTNESS_ORDER)
PROJECT_SIGNAL_VALUES = {"project", "package", "company_product"}
LINK_TYPES = {"github", "domain", "npm"}


def entity_id_for_link(key: str) -> str:
    return entity_id_for_key(key)


def _json_loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _score(row: dict[str, Any]) -> float:
    metadata = row.get("metadata") or {}
    for key in ("score", "points"):
        if metadata.get(key) is None:
            continue
        try:
            return float(metadata.get(key))
        except (TypeError, ValueError):
            continue
    return 0.0


def _comments(row: dict[str, Any]) -> float:
    metadata = row.get("metadata") or {}
    for key in ("comments", "num_comments"):
        if metadata.get(key) is None:
            continue
        try:
            return float(metadata.get(key))
        except (TypeError, ValueError):
            continue
    return 0.0


def candidate_hn_rows(conn: sqlite3.Connection, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        select id, source, external_id, name, url, fetched_at, description,
               metadata_json, raw_json
        from items
        where source in ('hn_firebase', 'hn_algolia')
        order by id desc
        """
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        metadata = _json_loads(row[7])
        candidates.append(
            {
                "item_id": row[0],
                "source": row[1],
                "external_id": row[2],
                "title": row[3],
                "url": row[4],
                "fetched_at": row[5],
                "description": row[6] or "",
                "metadata": metadata,
                "raw": _json_loads(row[8]),
            }
        )
    candidates.sort(key=lambda row: (_score(row), row["item_id"]), reverse=True)
    return candidates[: max(0, limit)]


def _normalized_title_key(title: str) -> str:
    lowered = title.strip().lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    lowered = re.sub(r"-+", "-", lowered)
    return f"title:{lowered or 'untitled'}"


def _normalized_external_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None
    path = parsed.path.rstrip("/")
    if host == "github.com":
        parts = [part for part in path.strip("/").split("/") if part]
        if len(parts) >= 2:
            path = f"/{parts[0].lower()}/{parts[1].lower().removesuffix('.git')}"
        else:
            return None
    elif host in {"npmjs.com"} and path.startswith("/package/"):
        pass
    elif host in SHARED_DOMAIN_BLOCKLIST:
        return None
    return urllib.parse.urlunparse(("https", host, path, "", "", ""))


def hn_unit_key(row: dict[str, Any]) -> str:
    external_url = _normalized_external_url(row.get("url"))
    if external_url:
        return f"url:{external_url}"
    return _normalized_title_key(str(row.get("title") or ""))


def _candidate_impact_item_ids(conn: sqlite3.Connection, table: str) -> set[int]:
    try:
        rows = conn.execute(
            f"""
            select e.source_item_ids_json
            from entities e
            join {table} c on c.entity_id = e.entity_id
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return set()
    item_ids: set[int] = set()
    for row in rows:
        try:
            parsed = json.loads(row[0] or "[]")
        except json.JSONDecodeError:
            parsed = []
        if not isinstance(parsed, list):
            continue
        for item in parsed:
            try:
                item_ids.add(int(item))
            except (TypeError, ValueError):
                continue
    return item_ids


def _product_likeness(unit: dict[str, Any]) -> int:
    title = str(unit.get("title") or "").lower()
    url = str(unit.get("url") or "")
    score = 0
    if title.startswith(("show hn:", "launch hn:", "launch:")):
        score += 20
    if "github.com/" in url:
        score += 12
    if "npmjs.com/package/" in url:
        score += 10
    if _normalized_external_url(url):
        score += 4
    return score


def candidate_hn_units(
    conn: sqlite3.Connection,
    limit: int,
    *,
    potential_item_ids: set[int] | None = None,
    edge_item_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    rows = candidate_hn_rows(conn, limit=1_000_000)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(hn_unit_key(row), []).append(row)

    potential_ids = (
        {int(item_id) for item_id in potential_item_ids}
        if potential_item_ids is not None
        else _candidate_impact_item_ids(conn, "potential_candidates")
    )
    edge_ids = (
        {int(item_id) for item_id in edge_item_ids}
        if edge_item_ids is not None
        else _candidate_impact_item_ids(conn, "edge_watch_candidates")
    )
    units: list[dict[str, Any]] = []
    for unit_key, unit_rows in grouped.items():
        representative = min(unit_rows, key=lambda row: int(row["item_id"]))
        item_ids = sorted(int(row["item_id"]) for row in unit_rows)
        candidate_impact = 0
        if any(item_id in potential_ids for item_id in item_ids):
            candidate_impact = 2
        elif any(item_id in edge_ids for item_id in item_ids):
            candidate_impact = 1
        unit = {
            "unit_key": unit_key,
            "item_id": representative["item_id"],
            "item_ids": item_ids,
            "rows": sorted(unit_rows, key=lambda row: int(row["item_id"])),
            "source": representative["source"],
            "sources": sorted({str(row["source"]) for row in unit_rows}),
            "external_id": representative["external_id"],
            "title": representative["title"],
            "url": representative["url"],
            "fetched_at": representative["fetched_at"],
            "description": representative.get("description", ""),
            "metadata": representative.get("metadata", {}),
            "best_score": max(_score(row) for row in unit_rows),
            "best_comments": max(_comments(row) for row in unit_rows),
            "row_count": len(unit_rows),
            "candidate_impact": candidate_impact,
        }
        unit["product_likeness"] = _product_likeness(unit)
        units.append(unit)
    units.sort(
        key=lambda unit: (
            unit["candidate_impact"],
            unit["product_likeness"],
            unit["best_score"],
            unit["row_count"],
            unit["unit_key"],
        ),
        reverse=True,
    )
    return units[: max(0, limit)]


def build_hn_prompt_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": TASK,
        "prompt_version": PROMPT_VERSION,
        "instructions": (
            "Classify whether this Hacker News item is about a concrete project, "
            "package, or product. Return JSON matching output_schema. News, broad "
            "topic discussions, and research papers without a concrete project "
            "binding should not create promotion evidence."
        ),
        "allowed_projectness": PROJECTNESS_ORDER,
        "allowed_link_types": sorted(LINK_TYPES),
        "item": {
            "item_id": row["item_id"],
            "unit_key": row.get("unit_key"),
            "source": row["source"],
            "external_id": row["external_id"],
            "title": row["title"],
            "url": row["url"],
            "description": row.get("description", ""),
            "metadata": {} if row.get("unit_key") else row.get("metadata", {}),
        },
        "output_schema": {
            "item_id": "integer",
            "projectness": "project|package|company_product|news_article|topic_discussion|research_paper|unknown",
            "confidence": "number from 0 to 1",
            "canonical_name": "string",
            "deterministic_links": [
                {"type": "github|domain|npm", "key": "type:value", "url": "https://..."}
            ],
            "proposed_links": [
                {
                    "type": "github|domain|npm",
                    "key": "type:value",
                    "url": "https://...",
                    "confidence": "number from 0 to 1",
                }
            ],
            "summary": "string",
        },
    }


def _derive_link_key(link_type: str, url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    path_parts = [part for part in parsed.path.strip("/").split("/") if part]
    if link_type == "github" and host == "github.com" and len(path_parts) >= 2:
        return normalize_github_repo(path_parts[0], path_parts[1])
    if link_type == "domain" and host:
        return f"domain:{host.removeprefix('www.')}"
    if link_type == "npm" and host in {"www.npmjs.com", "npmjs.com"} and path_parts[:1] == ["package"]:
        package = "/".join(path_parts[1:])
        return f"npm:{package}" if package else None
    return None


def _validate_link(link: Any, *, proposed: bool) -> None:
    if not isinstance(link, dict):
        raise ValueError("link must be an object")
    link_type = link.get("type")
    key = link.get("key")
    url = link.get("url")
    if link_type not in LINK_TYPES:
        raise ValueError(f"invalid link type: {link_type!r}")
    if isinstance(url, str) and (not isinstance(key, str) or not key):
        derived_key = _derive_link_key(str(link_type), url)
        if derived_key:
            link["key"] = derived_key
            key = derived_key
    if not isinstance(key, str) or not key.startswith(f"{link_type}:"):
        raise ValueError(f"malformed link key: {key!r}")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise ValueError(f"malformed link url: {url!r}")
    if proposed:
        confidence = link.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
            raise ValueError("proposed link confidence must be 0..1")


def validate_hn_output(payload: dict[str, Any]) -> dict[str, Any]:
    required = {
        "item_id",
        "projectness",
        "confidence",
        "canonical_name",
        "deterministic_links",
        "proposed_links",
        "summary",
    }
    missing = sorted(required - payload.keys())
    if missing:
        raise ValueError(f"missing HN output fields: {', '.join(missing)}")
    if not isinstance(payload["item_id"], int):
        raise ValueError("item_id must be an integer")
    if payload["projectness"] not in PROJECTNESS_VALUES:
        raise ValueError(f"invalid projectness: {payload['projectness']!r}")
    confidence = payload["confidence"]
    if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        raise ValueError("confidence must be 0..1")
    if not isinstance(payload["canonical_name"], str):
        raise ValueError("canonical_name must be a string")
    if not isinstance(payload["summary"], str):
        raise ValueError("summary must be a string")
    if not isinstance(payload["deterministic_links"], list):
        raise ValueError("deterministic_links must be a list")
    if not isinstance(payload["proposed_links"], list):
        raise ValueError("proposed_links must be a list")
    for link in payload["deterministic_links"]:
        _validate_link(link, proposed=False)
    for link in payload["proposed_links"]:
        _validate_link(link, proposed=True)
    return payload


def _sanitize_provider_link(link: Any, *, proposed: bool) -> dict[str, Any] | None:
    if not isinstance(link, dict):
        return None
    candidate = dict(link)
    try:
        _validate_link(candidate, proposed=proposed)
        return candidate
    except ValueError:
        if not isinstance(candidate.get("url"), str) or not candidate.get("url"):
            return None
        candidate["key"] = None
        try:
            _validate_link(candidate, proposed=proposed)
        except ValueError:
            return None
        return candidate


def _sanitize_provider_output(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(payload)
    if isinstance(sanitized.get("deterministic_links"), list):
        sanitized["deterministic_links"] = [
            link
            for link in (
                _sanitize_provider_link(link, proposed=False)
                for link in sanitized["deterministic_links"]
            )
            if link is not None
        ]
    if isinstance(sanitized.get("proposed_links"), list):
        sanitized["proposed_links"] = [
            link
            for link in (
                _sanitize_provider_link(link, proposed=True)
                for link in sanitized["proposed_links"]
            )
            if link is not None
        ]
    return sanitized


def _insert_evidence(
    conn: sqlite3.Connection,
    *,
    row: dict[str, Any],
    output: dict[str, Any],
    entity_id: str,
    run_id: str,
    now: str,
) -> None:
    projectness = output["projectness"]
    signal_label = "watch" if projectness in PROJECT_SIGNAL_VALUES else "noise"
    canonical_name = output.get("canonical_name") or row["title"]
    note = f"{canonical_name}: {output['summary']}".strip()
    conn.execute(
        """
        insert into evidence_rows(
            entity_id, canonical_entity, alias, source, event_at,
            relative_to_reference, metric_name, metric_value, family, rule_id,
            rule_version, signal_label, historical_safety, note, raw_url_or_ref,
            run_id
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity_id,
            canonical_name,
            canonical_name,
            "hn_llm_classifier",
            now,
            None,
            "hn_projectness",
            projectness,
            "hn",
            "hn_llm_projectness",
            PROMPT_VERSION,
            signal_label,
            "llm_source_classifier",
            note,
            f"item:{row['item_id']}",
            run_id,
        ),
    )


def _insert_aliases_and_proposals(
    conn: sqlite3.Connection,
    *,
    row: dict[str, Any],
    output: dict[str, Any],
    entity_id: str,
    run_id: str,
    now: str,
) -> None:
    for link in output["deterministic_links"]:
        conn.execute(
            """
            insert into alias_links(
                entity_id, source, external_id, alias, confidence, origin,
                approved, created_at
            )
            select ?, ?, ?, ?, ?, ?, ?, ?
            where not exists (
                select 1 from alias_links
                where entity_id = ? and alias = ? and origin = ?
            )
            """,
            (
                entity_id,
                "hn_llm_classifier",
                row["external_id"],
                link["key"],
                "deterministic",
                "hn_llm_classifier",
                1,
                now,
                entity_id,
                link["key"],
                "hn_llm_classifier",
            ),
        )
    for link in output["proposed_links"]:
        conn.execute(
            """
            insert into entity_merge_proposals(
                run_id, orphan, target_entity_id, confidence, reason, status,
                created_at
            )
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                link["key"],
                entity_id,
                float(link["confidence"]),
                f"HN proposed link from item {row['item_id']}: {output['summary']}",
                "open",
                now,
            ),
        )


def _provider_model(provider: Any) -> str:
    return str(getattr(provider, "model", "unknown"))


def _provider_name(provider: Any) -> str:
    return str(getattr(provider, "provider_name", provider.__class__.__name__.lower()))


def _cache_key_for_input(*, provider: Any, input_payload: dict[str, Any]) -> str:
    return cache_key_for(
        provider=_provider_name(provider),
        model=_provider_model(provider),
        prompt_version=PROMPT_VERSION,
        task=TASK,
        input_payload=input_payload,
    )


def _get_cached_output(
    conn: sqlite3.Connection,
    *,
    provider: Any,
    input_payload: dict[str, Any],
) -> dict[str, Any] | None:
    cached = get_cached_response(
        conn,
        _cache_key_for_input(provider=provider, input_payload=input_payload),
    )
    if not cached or cached["status"] != "ok":
        return None
    response = _sanitize_provider_output(dict(cached["response_json"]))
    validate_hn_output(response)
    return response


def _store_output_cache(
    conn: sqlite3.Connection,
    *,
    provider: Any,
    input_payload: dict[str, Any],
    system_prompt: str,
    response_payload: dict[str, Any],
    status: str,
    error: str | None = None,
) -> None:
    store_cached_response(
        conn,
        provider=_provider_name(provider),
        model=_provider_model(provider),
        prompt_version=PROMPT_VERSION,
        task=TASK,
        input_payload=input_payload,
        request_payload={"system_prompt": system_prompt, "input_payload": input_payload},
        response_payload=response_payload,
        status=status,
        error=error,
    )


def _call_provider(
    *,
    provider: Any,
    input_payload: dict[str, Any],
    system_prompt: str,
) -> dict[str, Any]:
    response = provider.complete_json(
        task=TASK,
        prompt_version=PROMPT_VERSION,
        input_payload=input_payload,
        system_prompt=system_prompt,
    )
    response = _sanitize_provider_output(response)
    validate_hn_output(response)
    return response


def _complete_with_cache(
    conn: sqlite3.Connection,
    *,
    provider: Any,
    input_payload: dict[str, Any],
    system_prompt: str,
) -> dict[str, Any]:
    cached = _get_cached_output(conn, provider=provider, input_payload=input_payload)
    if cached:
        return cached
    try:
        response = _call_provider(
            provider=provider,
            input_payload=input_payload,
            system_prompt=system_prompt,
        )
    except Exception as exc:
        _store_output_cache(
            conn,
            provider=provider,
            input_payload=input_payload,
            system_prompt=system_prompt,
            response_payload={"error": str(exc)},
            status="error",
            error=str(exc),
        )
        raise
    _store_output_cache(
        conn,
        provider=provider,
        input_payload=input_payload,
        response_payload=response,
        system_prompt=system_prompt,
        status="ok",
    )
    return response


def run_hn_classifier(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    provider: Any,
    limit: int,
    now: str,
    llm_concurrency: int = 1,
    potential_item_ids: set[int] | None = None,
    edge_item_ids: set[int] | None = None,
) -> dict[str, Any]:
    if llm_concurrency <= 0:
        raise ValueError("llm_concurrency must be positive")
    system_prompt = (
        "You are a bounded Hacker News source classifier. Return only JSON. "
        "Do not promote news articles, topic discussions, or generic terms as projects."
    )
    units = candidate_hn_units(
        conn,
        limit,
        potential_item_ids=potential_item_ids,
        edge_item_ids=edge_item_ids,
    )
    outputs_by_unit_key: dict[str, dict[str, Any]] = {}
    uncached_jobs: list[dict[str, Any]] = []
    cache_hits = 0
    cache_misses = 0
    for unit in units:
        input_payload = build_hn_prompt_payload(unit)
        cached = _get_cached_output(conn, provider=provider, input_payload=input_payload)
        if cached:
            cache_hits += 1
            outputs_by_unit_key[unit["unit_key"]] = cached
            continue
        cache_misses += 1
        uncached_jobs.append({"unit_key": unit["unit_key"], "input_payload": input_payload})

    def complete_job(job: dict[str, Any]) -> dict[str, Any]:
        try:
            response = _call_provider(
                provider=provider,
                input_payload=job["input_payload"],
                system_prompt=system_prompt,
            )
            return {**job, "response": response, "error": None}
        except Exception as exc:
            return {**job, "response": {"error": str(exc)}, "error": exc}

    results = bounded_parallel_map(
        uncached_jobs,
        complete_job,
        concurrency=llm_concurrency,
    )
    first_error: Exception | None = None
    for result in results:
        error = result["error"]
        status = "error" if error else "ok"
        _store_output_cache(
            conn,
            provider=provider,
            input_payload=result["input_payload"],
            system_prompt=system_prompt,
            response_payload=result["response"],
            status=status,
            error=str(error) if error else None,
        )
        if error:
            if first_error is None:
                first_error = error
            continue
        outputs_by_unit_key[result["unit_key"]] = result["response"]
    if first_error:
        raise first_error

    classified = 0
    classified_items = 0
    aliases = 0
    proposals = 0
    for unit in units:
        output = outputs_by_unit_key[unit["unit_key"]]
        deterministic_links = output["deterministic_links"]
        for row in unit["rows"]:
            entity_key = deterministic_links[0]["key"] if deterministic_links else f"hn:{row['item_id']}"
            entity_id = entity_id_for_link(entity_key)
            _insert_evidence(
                conn,
                row=row,
                output=output,
                entity_id=entity_id,
                run_id=run_id,
                now=now,
            )
            classified_items += 1
        before_aliases = conn.total_changes
        _insert_aliases_and_proposals(
            conn,
            row=unit,
            output=output,
            entity_id=entity_id_for_link(
                deterministic_links[0]["key"] if deterministic_links else f"hn:{unit['item_id']}"
            ),
            run_id=run_id,
            now=now,
        )
        aliases += max(0, conn.total_changes - before_aliases - len(output["proposed_links"]))
        proposals += len(output["proposed_links"])
        classified += 1
    conn.commit()
    return {
        "classified": classified,
        "classified_items": classified_items,
        "aliases": aliases,
        "proposals": proposals,
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
    }
