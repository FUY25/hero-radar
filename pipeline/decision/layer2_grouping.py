from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

from pipeline.decision.candidate_context import context_bundle_for_entity, key_to_url
from pipeline.decision.layer2_models import LEVEL_RANK, CandidateGroup
from pipeline.decision.schema import to_json


def build_candidate_groups(
    conn: sqlite3.Connection, *, decision_run_id: str
) -> list[CandidateGroup]:
    rows = _candidate_rows(conn, decision_run_id)
    rows_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        rows_by_key[_presentation_key(conn, row)].append(row)

    groups: list[CandidateGroup] = []
    for key, members in sorted(rows_by_key.items()):
        canonical = sorted(
            members,
            key=lambda row: (
                -LEVEL_RANK.get(row["level"], 0),
                row["canonical_entity"].lower(),
                row["entity_id"],
            ),
        )[0]
        member_ids = [canonical["entity_id"]] + sorted(
            row["entity_id"]
            for row in members
            if row["entity_id"] != canonical["entity_id"]
        )
        source_families = sorted(
            {
                family
                for row in members
                for family in row.get("source_families", [])
                if family
            }
        )
        canonical_link = _best_group_link(conn, canonical, members)
        digest = hashlib.sha1("|".join(member_ids).encode("utf-8")).hexdigest()[:12]
        groups.append(
            CandidateGroup(
                group_id=f"group:{digest}",
                canonical_entity_id=canonical["entity_id"],
                canonical_name=canonical["canonical_entity"],
                canonical_key=canonical["canonical_key"],
                canonical_link=canonical_link,
                member_entity_ids=member_ids,
                level=canonical["level"],
                source_families=source_families,
                grouping_reason={"key": key, "member_count": len(member_ids)},
            )
        )
    return groups


def persist_candidate_groups(
    conn: sqlite3.Connection, *, feed_run_id: str, groups: list[CandidateGroup]
) -> None:
    for group in groups:
        conn.execute(
            """
            insert or replace into l2_candidate_groups(
              group_id, feed_run_id, canonical_entity_id, canonical_name,
              canonical_key, canonical_link, member_entity_ids_json, level,
              source_families_json, evidence_hash, grouping_reason_json, context_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                group.group_id,
                feed_run_id,
                group.canonical_entity_id,
                group.canonical_name,
                group.canonical_key,
                group.canonical_link,
                to_json(group.member_entity_ids),
                group.level,
                to_json(group.source_families),
                group.evidence_hash,
                to_json(group.grouping_reason),
                to_json(group.context),
            ),
        )
    conn.commit()


def _candidate_rows(
    conn: sqlite3.Connection, decision_run_id: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in conn.execute(
        """
        select pc.entity_id, e.canonical_entity, e.canonical_key, pc.level,
               pc.fired_families_json, e.source_item_ids_json
        from potential_candidates pc
        join entities e on e.entity_id = pc.entity_id
        where pc.run_id = ?
        """,
        (decision_run_id,),
    ).fetchall():
        rows.append(
            {
                "entity_id": row[0],
                "canonical_entity": row[1],
                "canonical_key": row[2],
                "level": row[3],
                "source_families": _json_loads(row[4], []),
                "source_item_ids": _json_loads(row[5], []),
            }
        )
    for row in conn.execute(
        """
        select ew.entity_id, e.canonical_entity, e.canonical_key, e.source_item_ids_json
        from edge_watch_candidates ew
        join entities e on e.entity_id = ew.entity_id
        where ew.run_id = ?
        """,
        (decision_run_id,),
    ).fetchall():
        context = context_bundle_for_entity(conn, entity_id=row[0], run_id=decision_run_id)
        rows.append(
            {
                "entity_id": row[0],
                "canonical_entity": row[1],
                "canonical_key": row[2],
                "level": "edge_watch",
                "source_families": context.get("source_families", []),
                "source_item_ids": _json_loads(row[3], []),
            }
        )
    return rows


def _presentation_key(conn: sqlite3.Connection, row: dict[str, Any]) -> str:
    key = str(row.get("canonical_key") or "")
    alias_key = _approved_alias_key(conn, row["entity_id"])
    alias_group_key = _link_group_key(alias_key)
    if alias_group_key:
        return alias_group_key
    link_key = _link_group_key(key)
    if link_key:
        return link_key
    alias_link = key_to_url(alias_key)
    if alias_link:
        return f"link:{alias_link.lower().rstrip('/')}"
    source_link_key = _source_link_group_key(conn, row)
    if source_link_key:
        return source_link_key
    return f"entity:{row['entity_id']}"


def _link_group_key(key: str | None) -> str:
    value = str(key or "")
    if value.startswith("github:"):
        return f"github:{value.split(':', 1)[1].lower().strip('/')}"
    if value.startswith("npm:"):
        return f"npm:{value.split(':', 1)[1].lower().strip()}"
    if value.startswith("domain:"):
        domain = value.split(":", 1)[1].lower().strip("/")
        if _is_content_domain(domain):
            return ""
        return f"domain:{domain}"
    return ""


def _best_group_link(
    conn: sqlite3.Connection, canonical: dict[str, Any], members: list[dict[str, Any]]
) -> str:
    for row in [canonical, *members]:
        alias_key = _approved_alias_key(conn, row["entity_id"])
        if alias_key and alias_key.startswith("github:"):
            return key_to_url(_normalize_github_key(alias_key)) or ""
    for row in [canonical, *members]:
        canonical_link = key_to_url(_normalize_github_key(row["canonical_key"]))
        if canonical_link:
            return canonical_link
        alias_link = key_to_url(
            _normalize_github_key(_approved_alias_key(conn, row["entity_id"]))
        )
        if alias_link:
            return alias_link
    return ""


def _source_link_group_key(conn: sqlite3.Connection, row: dict[str, Any]) -> str:
    ids = []
    for value in row.get("source_item_ids") or []:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not ids:
        return ""
    placeholders = ",".join("?" for _ in ids)
    try:
        urls = conn.execute(
            f"""
            select url
            from items
            where id in ({placeholders})
              and coalesce(url, '') != ''
            order by id
            """,
            ids,
        ).fetchall()
    except sqlite3.OperationalError:
        return ""
    for (url,) in urls:
        key = _github_repo_url_group_key(str(url or ""))
        if key:
            return key
    return ""


def _github_repo_url_group_key(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return ""
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        return ""
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        return ""
    owner, repo = parts
    repo = repo.removesuffix(".git")
    if not owner or not repo:
        return ""
    return f"github:{owner.lower()}/{repo.lower()}"


def _approved_alias_key(conn: sqlite3.Connection, entity_id: str) -> str:
    row = conn.execute(
        """
        select alias
        from alias_links
        where entity_id = ? and approved = 1
          and (alias like 'github:%' or alias like 'domain:%' or alias like 'npm:%')
        order by case
            when alias like 'github:%' then 0
            when alias like 'npm:%' then 1
            else 2
        end, id
        limit 1
        """,
        (entity_id,),
    ).fetchone()
    return str(row[0] or "") if row else ""


def _normalize_github_key(key: str | None) -> str:
    value = str(key or "").strip()
    if value.startswith("github:"):
        return f"github:{value.split(':', 1)[1].lower().strip('/')}"
    return value


def _is_content_domain(domain: str) -> bool:
    return (
        domain.startswith("blog.")
        or domain.startswith("news.")
        or domain.startswith("newsroom.")
        or domain
        in {
            "medium.com",
            "substack.com",
            "blogspot.com",
            "github.io",
            "vercel.app",
            "netlify.app",
            "huggingface.co",
            "npmjs.com",
            "pypi.org",
            "producthunt.com",
            "x.com",
            "twitter.com",
        }
        or domain.endswith(".medium.com")
        or domain.endswith(".substack.com")
    )


def _json_loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default
