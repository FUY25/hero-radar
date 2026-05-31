from __future__ import annotations

import json
import sqlite3
from typing import Any


SOURCE_CLASSIFIER_SOURCES = {"hn_llm_classifier", "x_tweets", "npm_registry"}
BACKFILL_SOURCES = {"github_api", "github_stargazers", "npm_downloads"}
KEY_LINK_TYPES = {"github", "domain", "npm"}
MAX_SOURCE_LINKS = 12

SOURCE_CHANNEL_LABELS = {
    "github_trending": "GitHub Trending",
    "github_movers_trending_repos": "Trending Repos",
    "github_movers_repofomo": "RepoFOMO",
    "github_search": "GitHub Search",
    "hn_search": "HN Search",
    "hn_top": "HN Top",
    "product_hunt": "Product Hunt",
    "huggingface_models": "HF Models",
    "huggingface_datasets": "HF Datasets",
    "huggingface_spaces": "HF Spaces",
    "npm_search": "npm Search",
    "pypi_newest": "PyPI Newest",
    "pypi_updates": "PyPI Updates",
    "x_tweets": "X Tweets",
}

EXCLUDED_SOURCE_LINK_SOURCES = {"ossinsight_trending", "ossinsight_trending_optional", "x_project_mentions"}


def key_to_url(key: str | None) -> str | None:
    value = str(key or "").strip()
    if value.startswith("github:"):
        return f"https://github.com/{value.split(':', 1)[1]}"
    if value.startswith("domain:"):
        return f"https://{value.split(':', 1)[1]}"
    if value.startswith("npm:"):
        return f"https://www.npmjs.com/package/{value.split(':', 1)[1]}"
    return None


def context_bundle_for_entity(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    run_id: str,
) -> dict[str, Any]:
    entity = _entity_row(conn, entity_id)
    evidence = _evidence_rows(conn, entity_id, run_id)
    canonical_key = str(entity.get("canonical_key") or "")
    alias_key = _best_alias_key(conn, entity_id, canonical_key)

    canonical_link = key_to_url(canonical_key)
    binding = "verified" if canonical_link else "none"
    if not canonical_link:
        canonical_link = key_to_url(alias_key)
        binding = "resolved" if canonical_link else "none"
    if not canonical_link:
        canonical_link = _best_source_link(conn, entity)
        binding = "weak" if canonical_link else "none"

    readme_preview = _readme_preview(conn, canonical_key, alias_key)
    context_preview = readme_preview or _best_source_description(conn, entity)
    bullets = _dedupe_bullets([_evidence_bullet(row) for row in evidence])
    source_links = _source_links_for_bullets(conn, bullets)
    links_by_ref = {link["ref"]: link for link in source_links}
    for bullet in bullets:
        bullet["source_links"] = [
            links_by_ref[ref]
            for ref in bullet.get("source_refs") or []
            if ref in links_by_ref
        ]

    return {
        "entity_id": entity_id,
        "canonical_link": canonical_link,
        "binding_confidence": binding,
        "context_preview": context_preview,
        "readme_excerpt_available": bool(readme_preview),
        "evidence_count": len(bullets),
        "evidence_bullets": bullets,
        "source_families": sorted({bullet["family"] for bullet in bullets if bullet.get("family")}),
        "source_link_count": len(source_links),
        "source_links": source_links[:MAX_SOURCE_LINKS],
    }


def _json_loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _entity_row(conn: sqlite3.Connection, entity_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        select entity_id, canonical_entity, canonical_key, key_type, first_seen,
               aliases_json, source_item_ids_json
        from entities
        where entity_id = ?
        """,
        (entity_id,),
    ).fetchone()
    if not row:
        return {"entity_id": entity_id, "source_item_ids": []}
    return {
        "entity_id": row[0],
        "canonical_entity": row[1],
        "canonical_key": row[2],
        "key_type": row[3],
        "first_seen": row[4],
        "aliases": _json_loads(row[5], []),
        "source_item_ids": _json_loads(row[6], []),
    }


def _evidence_rows(
    conn: sqlite3.Connection,
    entity_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        select id, source, event_at, metric_name, metric_value, family, rule_id,
               rule_version, signal_label, note, raw_url_or_ref
        from evidence_rows
        where entity_id = ? and run_id = ?
        order by
            case
                when family = 'github' then 0
                when family = 'hn' then 1
                when family = 'x_social' then 2
                when family = 'package_family' then 3
                when source = 'resolver' then 4
                when family = 'cross_source' then 5
                else 6
            end,
            id
        """,
        (entity_id, run_id),
    ).fetchall()
    return [
        {
            "id": row[0],
            "source": row[1],
            "event_at": row[2],
            "metric_name": row[3],
            "metric_value": row[4],
            "family": row[5],
            "rule_id": row[6],
            "rule_version": row[7],
            "signal_label": row[8],
            "note": row[9],
            "raw_url_or_ref": row[10],
        }
        for row in rows
    ]


def _best_alias_key(
    conn: sqlite3.Connection,
    entity_id: str,
    canonical_key: str | None = None,
) -> str | None:
    rows = conn.execute(
        """
        select alias
        from alias_links
        where entity_id = ? and approved = 1
        order by
            case
                when origin = 'resolver' and external_id = ? then 0
                when origin = 'resolver' then 1
                when alias like 'github:%' then 2
                when alias like 'domain:%' then 3
                when alias like 'npm:%' then 4
                else 5
            end,
            id
        """,
        (entity_id, canonical_key or ""),
    ).fetchall()
    for row in rows:
        alias = str(row[0] or "")
        if alias.split(":", 1)[0] in KEY_LINK_TYPES:
            return alias
    return None


def _source_item_ids(entity: dict[str, Any]) -> list[int]:
    ids: list[int] = []
    for value in entity.get("source_item_ids") or []:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return ids


def _best_source_description(conn: sqlite3.Connection, entity: dict[str, Any]) -> str:
    ids = _source_item_ids(entity)
    if not ids:
        return ""
    placeholders = ",".join("?" for _ in ids)
    try:
        row = conn.execute(
            f"""
            select description
            from items
            where id in ({placeholders})
              and coalesce(description, '') != ''
            order by id
            limit 1
            """,
            ids,
        ).fetchone()
    except sqlite3.OperationalError:
        return ""
    return str(row[0]) if row else ""


def _best_source_link(conn: sqlite3.Connection, entity: dict[str, Any]) -> str | None:
    ids = _source_item_ids(entity)
    if not ids:
        return None
    placeholders = ",".join("?" for _ in ids)
    try:
        rows = conn.execute(
            f"""
            select url
            from items
            where id in ({placeholders})
              and coalesce(url, '') != ''
            order by
              case
                when url like 'https://github.com/%' then 0
                when url like 'https://www.npmjs.com/package/%' then 1
                else 2
              end,
              id
            """,
            ids,
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    for row in rows:
        url = str(row[0] or "").strip()
        if url.startswith("http://") or url.startswith("https://"):
            return url
    return None


def _readme_preview(
    conn: sqlite3.Connection,
    canonical_key: str | None,
    alias_key: str | None,
) -> str:
    repo_key = _repo_key(canonical_key) or _repo_key(alias_key)
    if not repo_key:
        return ""
    row = conn.execute(
        """
        select response_json
        from api_cache
        where source = 'github_readme'
          and external_id = ?
          and status = 'ok'
        order by fetched_at desc
        limit 1
        """,
        (repo_key,),
    ).fetchone()
    if not row:
        return ""
    response = _json_loads(row[0], {})
    return str(response.get("preview") or response.get("excerpt") or "")[:1000]


def _repo_key(key: str | None) -> str | None:
    value = str(key or "").strip()
    if not value.startswith("github:"):
        return None
    repo = value.split(":", 1)[1].strip("/")
    if "/" not in repo:
        return None
    owner, name = repo.split("/", 1)
    return f"{owner.lower()}/{name.lower()}"


def _evidence_bullet(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": _evidence_label(row),
        "family": row["family"],
        "origin_type": _origin_type(row),
        "provenance_badge": _provenance_badge(row),
        "strength": row["signal_label"],
        "source_refs": [row["raw_url_or_ref"]] if row.get("raw_url_or_ref") else [],
    }


def _dedupe_bullets(bullets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    order: list[tuple[Any, ...]] = []
    for bullet in bullets:
        key = (
            bullet.get("label"),
            bullet.get("family"),
            bullet.get("origin_type"),
            bullet.get("provenance_badge"),
        )
        if key not in merged:
            merged[key] = {**bullet, "source_refs": []}
            order.append(key)
        refs = merged[key]["source_refs"]
        for ref in bullet.get("source_refs") or []:
            if ref and ref not in refs:
                refs.append(ref)
    return [merged[key] for key in order]


def _source_links_for_bullets(conn: sqlite3.Connection, bullets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[str] = []
    for bullet in bullets:
        for ref in bullet.get("source_refs") or []:
            value = str(ref or "").strip()
            if value and value not in refs:
                refs.append(value)

    links: list[dict[str, Any]] = []
    seen_items: set[int] = set()
    for ref in refs:
        link = _source_link_for_ref(conn, ref)
        if not link:
            continue
        item_id = link.get("item_id")
        if isinstance(item_id, int) and item_id in seen_items:
            continue
        if isinstance(item_id, int):
            seen_items.add(item_id)
        links.append(link)
    return links


def _source_link_for_ref(conn: sqlite3.Connection, ref: str) -> dict[str, Any] | None:
    if ref.startswith("item:"):
        try:
            item_id = int(ref.split(":", 1)[1])
        except (TypeError, ValueError):
            return None
        return _source_link_for_item_id(conn, item_id, ref)
    if ref.startswith("tweet:"):
        return _source_link_for_tweet_ref(conn, ref)
    return None


def _source_link_for_tweet_ref(conn: sqlite3.Connection, ref: str) -> dict[str, Any] | None:
    tweet_id = ref.split(":", 1)[1].strip()
    if not tweet_id:
        return None
    try:
        row = conn.execute(
            """
            select id
            from items
            where source = 'x_tweets'
              and (
                external_id = ?
                or external_id like ?
                or url like ?
              )
            order by
              case when external_id = ? then 0 else 1 end,
              id
            limit 1
            """,
            (ref, f"%:{tweet_id}", f"%/{tweet_id}", ref),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    return _source_link_for_item_id(conn, int(row[0]), ref)


def _source_link_for_item_id(conn: sqlite3.Connection, item_id: int, ref: str) -> dict[str, Any] | None:
    try:
        row = conn.execute(
            """
            select id, source, name, url, metadata_json
            from items
            where id = ?
            """,
            (item_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None

    source = str(row[1] or "")
    channel = _dashboard_channel_for_source(source)
    if not channel:
        return None
    metadata = _json_loads(row[4], {})
    label = _channel_label(channel)
    return {
        "ref": ref,
        "item_id": int(row[0]),
        "source": source,
        "channel": channel,
        "channel_label": label,
        "label": label,
        "name": str(row[2] or ""),
        "external_url": str(row[3] or ""),
        "window": str(metadata.get("window") or ""),
    }


def _dashboard_channel_for_source(source: str) -> str | None:
    if source in EXCLUDED_SOURCE_LINK_SOURCES:
        return None
    if source == "hn_algolia":
        return "hn_search"
    if source == "hn_firebase":
        return "hn_top"
    if source.startswith("huggingface_") or source.startswith("pypi_"):
        return source
    return source


def _channel_label(channel: str) -> str:
    return SOURCE_CHANNEL_LABELS.get(channel, channel)


def _evidence_label(row: dict[str, Any]) -> str:
    source = str(row.get("source") or "")
    family = str(row.get("family") or "")
    metric = str(row.get("metric_name") or "")
    value = str(row.get("metric_value") or "")
    if family == "github" and metric in {"stars_today", "period_stars", "stargazers_delta"}:
        return f"GH +{_compact_number(value)} stars / 24h"
    if family == "hn" and metric == "hn_max_points_7d":
        return f"HN max {_compact_number(value)} pts / 7d"
    if family == "hn" and metric in {"hn_score", "score"}:
        return f"HN front page, {value} pts"
    if source == "x_tweets" and metric == "x_tier":
        return f"X {value}"
    if source == "hn_llm_classifier" and metric == "hn_projectness":
        return f"HN classifier: {value}"
    if source == "resolver":
        return f"Resolved {value}"
    return f"{family}: {metric} {value}".strip()


def _compact_number(raw: str) -> str:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return str(raw)
    if abs(value) >= 1000:
        compact = value / 1000
        return f"{compact:.1f}k".replace(".0k", "k")
    if value.is_integer():
        return str(int(value))
    return f"{value:g}"


def _origin_type(row: dict[str, Any]) -> str:
    source = str(row.get("source") or "")
    family = str(row.get("family") or "")
    if source in SOURCE_CLASSIFIER_SOURCES:
        return "source_classifier"
    if source == "resolver" or family == "resolver":
        return "resolver"
    if source in BACKFILL_SOURCES:
        return "backfill"
    if family == "cross_source":
        return "cross_source_rule"
    return "deterministic_rule"


def _provenance_badge(row: dict[str, Any]) -> str:
    origin = _origin_type(row)
    return {
        "source_classifier": "LLM classifier",
        "resolver": "resolver",
        "backfill": "backfill",
        "cross_source_rule": "cross-source",
        "deterministic_rule": "rule",
    }[origin]
