from __future__ import annotations

import json
import sqlite3
import urllib.parse
from typing import Any

from pipeline.decision.entity_resolution import entity_id_for_key, normalize_github_repo
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
            "source": row["source"],
            "external_id": row["external_id"],
            "title": row["title"],
            "url": row["url"],
            "description": row.get("description", ""),
            "metadata": row.get("metadata", {}),
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


def _complete_with_cache(
    conn: sqlite3.Connection,
    *,
    provider: Any,
    input_payload: dict[str, Any],
    system_prompt: str,
) -> dict[str, Any]:
    provider_name = _provider_name(provider)
    model = _provider_model(provider)
    key = cache_key_for(
        provider=provider_name,
        model=model,
        prompt_version=PROMPT_VERSION,
        task=TASK,
        input_payload=input_payload,
    )
    cached = get_cached_response(conn, key)
    if cached and cached["status"] == "ok":
        return dict(cached["response_json"])
    request_payload = {"system_prompt": system_prompt, "input_payload": input_payload}
    try:
        response = provider.complete_json(
            task=TASK,
            prompt_version=PROMPT_VERSION,
            input_payload=input_payload,
            system_prompt=system_prompt,
        )
        validate_hn_output(response)
    except Exception as exc:
        store_cached_response(
            conn,
            provider=provider_name,
            model=model,
            prompt_version=PROMPT_VERSION,
            task=TASK,
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
        prompt_version=PROMPT_VERSION,
        task=TASK,
        input_payload=input_payload,
        request_payload=request_payload,
        response_payload=response,
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
) -> dict[str, Any]:
    system_prompt = (
        "You are a bounded Hacker News source classifier. Return only JSON. "
        "Do not promote news articles, topic discussions, or generic terms as projects."
    )
    classified = 0
    aliases = 0
    proposals = 0
    for row in candidate_hn_rows(conn, limit):
        input_payload = build_hn_prompt_payload(row)
        output = _complete_with_cache(
            conn,
            provider=provider,
            input_payload=input_payload,
            system_prompt=system_prompt,
        )
        deterministic_links = output["deterministic_links"]
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
        before_aliases = conn.total_changes
        _insert_aliases_and_proposals(
            conn,
            row=row,
            output=output,
            entity_id=entity_id,
            run_id=run_id,
            now=now,
        )
        aliases += max(0, conn.total_changes - before_aliases - len(output["proposed_links"]))
        proposals += len(output["proposed_links"])
        classified += 1
    conn.commit()
    return {"classified": classified, "aliases": aliases, "proposals": proposals}
