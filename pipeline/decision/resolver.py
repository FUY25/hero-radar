from __future__ import annotations

import json
import sqlite3
import urllib.parse
from typing import Any

from pipeline.decision.cache import api_cache_key, get_api_cache, put_api_cache, stable_hash
from pipeline.decision.entity_resolution import (
    entity_id_for_key,
    extract_domain_keys,
    extract_github_keys,
    normalize_name_key,
)


RESOLVER_SOURCE = "resolver"
RESOLVER_WINDOW = "classifier_enrichment"
PROJECTNESS_VALUES = {"project", "package", "company_product"}
ACCEPTED_X_TIERS = {"watch", "potential", "high", "high_potential"}


def _entity_label(entity_key: str) -> str:
    if ":" not in entity_key:
        return entity_key
    return entity_key.split(":", 1)[1].replace("-", " ").strip()


def _key_type(entity_key: str) -> str:
    return entity_key.split(":", 1)[0] if ":" in entity_key else "name"


def _npm_key_from_url(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.netloc or "").lower()
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if host not in {"npmjs.com", "www.npmjs.com"} or parts[:1] != ["package"]:
        return None
    package = "/".join(parts[1:])
    return f"npm:{package}" if package else None


def _link_from_key(key: str, url: str, confidence: float) -> dict[str, Any] | None:
    if ":" not in key:
        return None
    link_type = key.split(":", 1)[0]
    if link_type not in {"github", "domain", "npm"}:
        return None
    return {
        "type": link_type,
        "key": key,
        "url": url,
        "confidence": float(confidence),
        "source": RESOLVER_SOURCE,
    }


def _links_from_texts(texts: list[str], *, confidence: float) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    seen: set[str] = set()
    joined = " ".join(text for text in texts if text)
    for key in sorted(extract_github_keys([joined])):
        url = f"https://github.com/{key.split(':', 1)[1]}"
        link = _link_from_key(key, url, confidence)
        if link and key not in seen:
            seen.add(key)
            links.append(link)
    for key in sorted(extract_domain_keys([joined])):
        url = f"https://{key.split(':', 1)[1]}"
        link = _link_from_key(key, url, confidence)
        if link and key not in seen:
            seen.add(key)
            links.append(link)
    for raw_url in joined.split():
        npm_key = _npm_key_from_url(raw_url.strip(".,;:!?)\"]}'"))
        if npm_key and npm_key not in seen:
            seen.add(npm_key)
            links.append(
                _link_from_key(
                    npm_key,
                    f"https://www.npmjs.com/package/{npm_key.split(':', 1)[1]}",
                    confidence,
                )
            )
    return [link for link in links if link]


def _internal_candidate_rows(
    conn: sqlite3.Connection,
    entity_key: str,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    label = _entity_label(entity_key).lower()
    if not label:
        return []
    like = f"%{label}%"
    try:
        rows = conn.execute(
            """
            select name, url, description, metadata_json
            from items
            where lower(name) like ?
               or lower(coalesce(url, '')) like ?
               or lower(coalesce(description, '')) like ?
               or lower(metadata_json) like ?
            order by id desc
            limit ?
            """,
            (like, like, like, like, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    output: list[dict[str, Any]] = []
    for row in rows:
        try:
            metadata = json.loads(row[3] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        output.append(
            {
                "name": row[0] or "",
                "url": row[1] or "",
                "description": row[2] or "",
                "metadata": metadata if isinstance(metadata, dict) else {},
            }
        )
    return output


def _links_from_internal_rows(
    conn: sqlite3.Connection,
    entity_key: str,
) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in _internal_candidate_rows(conn, entity_key):
        metadata = row["metadata"]
        texts = [
            row["name"],
            row["url"],
            row["description"],
            *(str(metadata.get(key) or "") for key in ("repository", "repo", "homepage", "website", "url")),
        ]
        for link in _links_from_texts(texts, confidence=1.0):
            if link["key"] in seen:
                continue
            seen.add(link["key"])
            link["source"] = "internal"
            links.append(link)
    return links


def _slug_for_name_key(entity_key: str) -> str:
    return _entity_label(entity_key).lower().replace(" ", "-")


def _is_exact_name_link(entity_key: str, link: dict[str, Any]) -> bool:
    if not entity_key.startswith("name:"):
        return True
    slug = _slug_for_name_key(entity_key)
    key = str(link.get("key") or "").lower()
    if key.startswith("github:"):
        repo = key.split(":", 1)[1].split("/", 1)[-1]
        return repo == slug
    if key.startswith("domain:"):
        host = key.split(":", 1)[1]
        return host.split(".", 1)[0] == slug
    if key.startswith("npm:"):
        package = key.split(":", 1)[1].split("/")[-1]
        return package == slug
    return False


def _select_internal_links(entity_key: str, links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not entity_key.startswith("name:") or len(links) <= 1:
        return links
    exact = [link for link in links if _is_exact_name_link(entity_key, link)]
    return exact


def _normalize_search_result(result: dict[str, Any]) -> dict[str, Any] | None:
    link_type = str(result.get("type") or "").strip()
    key = str(result.get("key") or "").strip()
    url = str(result.get("url") or "").strip()
    if not key and url:
        links = _links_from_texts([url], confidence=float(result.get("confidence") or 0.5))
        if links:
            return links[0]
    if not key and link_type and url:
        if link_type == "npm":
            key = _npm_key_from_url(url) or ""
        else:
            links = _links_from_texts([url], confidence=float(result.get("confidence") or 0.5))
            key = links[0]["key"] if links else ""
    if not link_type and ":" in key:
        link_type = key.split(":", 1)[0]
    if not url:
        if key.startswith("github:"):
            url = f"https://github.com/{key.split(':', 1)[1]}"
        elif key.startswith("domain:"):
            url = f"https://{key.split(':', 1)[1]}"
        elif key.startswith("npm:"):
            url = f"https://www.npmjs.com/package/{key.split(':', 1)[1]}"
    confidence = float(result.get("confidence") or 0.5)
    return _link_from_key(key, url, confidence)


def normalize_resolved_link(result: dict[str, Any]) -> dict[str, Any] | None:
    return _normalize_search_result(result)


def _cache_key(entity_key: str, max_searches: int) -> tuple[str, str]:
    input_hash = stable_hash({"entity_key": entity_key, "max_searches": max_searches})
    return (
        api_cache_key(
            source=RESOLVER_SOURCE,
            external_id=entity_key,
            window=RESOLVER_WINDOW,
            input_hash=input_hash,
        ),
        input_hash,
    )


def resolve_candidate_links(
    conn: sqlite3.Connection,
    entity_key: str,
    *,
    search_client: Any | None = None,
    max_searches: int = 0,
    research_provider: Any | None = None,
    research_context: dict[str, Any] | None = None,
    max_research_rounds: int = 0,
) -> dict[str, Any]:
    internal_links = _select_internal_links(entity_key, _links_from_internal_rows(conn, entity_key))
    if internal_links:
        return {
            "entity_key": entity_key,
            "resolved_links": internal_links,
            "source": "internal",
        }

    key, input_hash = _cache_key(entity_key, max_searches)
    cached = get_api_cache(conn, key)
    if cached:
        response = cached
    else:
        links: list[dict[str, Any]] = []
        if search_client is not None and max_searches > 0:
            query = _entity_label(entity_key)
            results = search_client.search(query, limit=max_searches)
            for result in results[:max_searches]:
                if not isinstance(result, dict):
                    continue
                link = _normalize_search_result(result)
                if link:
                    links.append(link)

        response = {
            "entity_key": entity_key,
            "resolved_links": links,
            "source": "search" if links else "none",
        }
        put_api_cache(
            conn,
            cache_key=key,
            source=RESOLVER_SOURCE,
            external_id=entity_key,
            window=RESOLVER_WINDOW,
            input_hash=input_hash,
            response=response,
            status="ok",
        )
    if response.get("resolved_links"):
        return response
    if research_provider is not None and search_client is not None and max_research_rounds > 0:
        from pipeline.decision.web_research import research_candidate_link

        return research_candidate_link(
            conn,
            entity_key=entity_key,
            evidence_context=research_context or {"entity_key": entity_key},
            provider=research_provider,
            search_client=search_client,
            max_rounds=max_research_rounds,
            max_results=max(1, int(max_searches or 5)),
        )
    return response


def _ensure_entity(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    entity_key: str,
    now: str,
) -> None:
    canonical = _entity_label(entity_key) or entity_key
    conn.execute(
        """
        insert into entities(
            entity_id, canonical_entity, canonical_key, key_type, first_seen,
            aliases_json, source_item_ids_json
        )
        values (?, ?, ?, ?, ?, ?, ?)
        on conflict(entity_id) do update set
            canonical_entity = excluded.canonical_entity,
            canonical_key = excluded.canonical_key,
            key_type = excluded.key_type,
            aliases_json = excluded.aliases_json
        """,
        (
            entity_id,
            canonical,
            entity_key,
            _key_type(entity_key),
            now,
            json.dumps([entity_key], ensure_ascii=False),
            "[]",
        ),
    )


def _write_resolved_links(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    entity_id: str,
    entity_key: str,
    links: list[dict[str, Any]],
    now: str,
) -> tuple[int, int]:
    aliases = 0
    proposals = 0
    selected_aliases = {
        str(link.get("key") or "")
        for link in links
        if str(link.get("key") or "") and float(link.get("confidence") or 0) >= 0.8
    }
    if entity_key.startswith("name:") and selected_aliases:
        placeholders = ",".join("?" for _ in selected_aliases)
        conn.execute(
            f"""
            delete from alias_links
            where entity_id = ?
              and external_id = ?
              and origin = ?
              and alias not in ({placeholders})
            """,
            (entity_id, entity_key, RESOLVER_SOURCE, *sorted(selected_aliases)),
        )
    for link in links:
        key = str(link.get("key") or "")
        confidence = float(link.get("confidence") or 0)
        if not key:
            continue
        if confidence >= 0.8:
            before = conn.total_changes
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
                    RESOLVER_SOURCE,
                    entity_key,
                    key,
                    "deterministic",
                    RESOLVER_SOURCE,
                    1,
                    now,
                    entity_id,
                    key,
                    RESOLVER_SOURCE,
                ),
            )
            aliases += 1 if conn.total_changes > before else 0
            continue
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
                key,
                entity_id,
                confidence,
                f"resolver proposed link for {entity_key}",
                "open",
                now,
            ),
        )
        proposals += 1
    return aliases, proposals


def _accepted_classifier_candidates(
    conn: sqlite3.Connection,
    run_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        select entity_id, canonical_entity, alias, source, metric_name, metric_value
        from evidence_rows
        where run_id = ?
          and (
            (source = 'x_tweets' and metric_name = 'x_tier')
            or (source = 'hn_llm_classifier' and metric_name = 'hn_projectness')
          )
        order by id
        """,
        (run_id,),
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        metric_value = str(row[5] or "")
        if row[3] == "x_tweets" and metric_value not in ACCEPTED_X_TIERS:
            continue
        if row[3] == "hn_llm_classifier" and metric_value not in PROJECTNESS_VALUES:
            continue
        raw_key = str(row[1] or row[2] or "")
        entity_key = raw_key if ":" in raw_key else normalize_name_key(raw_key)
        if not entity_key:
            continue
        item = {"entity_id": str(row[0]), "entity_key": entity_key}
        dedupe_key = (item["entity_id"], item["entity_key"])
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        candidates.append(item)
    return candidates


def enrich_classifier_candidates(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    search_client: Any | None = None,
    max_searches_per_candidate: int = 0,
    research_provider: Any | None = None,
    max_research_rounds: int = 0,
    now: str,
) -> dict[str, int]:
    enriched = 0
    aliases = 0
    proposals = 0
    researched = 0
    for candidate in _accepted_classifier_candidates(conn, run_id):
        entity_id = candidate["entity_id"]
        entity_key = candidate["entity_key"]
        _ensure_entity(conn, entity_id=entity_id, entity_key=entity_key, now=now)
        result = resolve_candidate_links(
            conn,
            entity_key,
            search_client=search_client,
            max_searches=max_searches_per_candidate,
            research_provider=research_provider,
            research_context={"entity_key": entity_key, "run_id": run_id},
            max_research_rounds=max_research_rounds,
        )
        links = list(result.get("resolved_links") or [])
        if not links:
            continue
        if result.get("source") == "agentic_link_research":
            researched += 1
        written_aliases, written_proposals = _write_resolved_links(
            conn,
            run_id=run_id,
            entity_id=entity_id,
            entity_key=entity_key,
            links=links,
            now=now,
        )
        aliases += written_aliases
        proposals += written_proposals
        enriched += 1
    conn.commit()
    return {
        "enriched": enriched,
        "aliases": aliases,
        "proposals": proposals,
        "researched": researched,
    }
