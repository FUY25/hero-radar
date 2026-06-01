from __future__ import annotations

from typing import Any

from pipeline.decision.candidate_context import clean_preview_text
from pipeline.decision.layer2_models import CandidateGroup


MAX_PROJECT_CONTEXT_CHARS = 4000
MAX_WIDE_ONE_LINER_CHARS = 300
MAX_SUMMARIES = 8
MAX_SOURCE_CONTEXT = 12
MAX_WIDE_SOURCE_TITLES = 3
MAX_WIDE_SOURCE_TYPES = 5


def scout_context_for_group(group: CandidateGroup) -> dict[str, Any]:
    members = [
        member
        for member in group.context.get("members") or []
        if isinstance(member, dict)
    ]
    project_context = _project_context(members)
    qualitative_summaries = _qualitative_summaries(group.context.get("evidence_rows"))
    return {
        "group_id": group.group_id,
        "candidate": {
            "name": group.canonical_name,
            "canonical_key": group.canonical_key,
            "canonical_link": group.canonical_link,
            "level": group.level,
            "has_readme": any(
                bool(member.get("readme_excerpt_available")) for member in members
            ),
            "project_context": project_context,
            "qualitative_summaries": qualitative_summaries,
        },
        "source_context": _source_context(members),
    }


def wide_scout_context_for_group(group: CandidateGroup) -> dict[str, Any]:
    members = [
        member
        for member in group.context.get("members") or []
        if isinstance(member, dict)
    ]
    return {
        "group_id": group.group_id,
        "name": group.canonical_name,
        "link": group.canonical_link,
        "object_hint": _object_hint(group),
        "one_liner": _wide_one_liner(members, group.context.get("evidence_rows")),
        "source_titles": _wide_source_titles(members),
        "source_types": _wide_source_types(members),
    }


def _project_context(members: list[dict[str, Any]]) -> list[str]:
    snippets: list[str] = []
    for member in members:
        _append_unique(
            snippets,
            _clean_snippet(member.get("context_preview"), limit=MAX_PROJECT_CONTEXT_CHARS),
        )
    return snippets[:MAX_SUMMARIES]


def _qualitative_summaries(rows: Any) -> list[str]:
    summaries: list[str] = []
    if not isinstance(rows, list):
        return summaries
    for row in rows:
        if not isinstance(row, dict):
            continue
        _append_unique(summaries, _clean_snippet(row.get("note"), limit=600))
    return summaries[:MAX_SUMMARIES]


def _source_context(members: list[dict[str, Any]]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for member in members:
        source_links = member.get("source_links") or []
        if not isinstance(source_links, list):
            continue
        for link in source_links:
            if not isinstance(link, dict):
                continue
            entry = _source_entry(link)
            key = (
                entry.get("source", ""),
                entry.get("title", ""),
                entry.get("url", ""),
            )
            if key in seen:
                continue
            seen.add(key)
            entries.append(entry)
            if len(entries) >= MAX_SOURCE_CONTEXT:
                return entries
    return entries


def _source_entry(link: dict[str, Any]) -> dict[str, str]:
    title = _clean_snippet(
        link.get("name") or link.get("title") or link.get("label"),
        limit=240,
    )
    text = _clean_snippet(
        link.get("text") or link.get("description") or link.get("summary"),
        limit=600,
    )
    return {
        "source": str(link.get("source") or ""),
        "channel": str(link.get("channel") or ""),
        "title": title,
        "url": str(link.get("external_url") or link.get("url") or ""),
        "author": str(link.get("author") or link.get("author_name") or ""),
        "text": text,
    }


def _object_hint(group: CandidateGroup) -> str:
    key = str(group.canonical_key or "")
    if ":" in key:
        return key.split(":", 1)[0] or "unknown"
    if group.canonical_link:
        return "link"
    return "unknown"


def _wide_one_liner(members: list[dict[str, Any]], rows: Any) -> str:
    for member in members:
        snippet = _clean_snippet(
            member.get("context_preview"), limit=MAX_WIDE_ONE_LINER_CHARS
        )
        if snippet:
            return snippet
    summaries = _qualitative_summaries(rows)
    return summaries[0][:MAX_WIDE_ONE_LINER_CHARS] if summaries else ""


def _wide_source_titles(members: list[dict[str, Any]]) -> list[str]:
    titles: list[str] = []
    for entry in _source_context(members):
        _append_unique(titles, entry.get("title", ""))
    return titles[:MAX_WIDE_SOURCE_TITLES]


def _wide_source_types(members: list[dict[str, Any]]) -> list[str]:
    types: list[str] = []
    for entry in _source_context(members):
        _append_unique(types, entry.get("source", "") or entry.get("channel", ""))
    return types[:MAX_WIDE_SOURCE_TYPES]


def _clean_snippet(value: Any, *, limit: int) -> str:
    text = clean_preview_text(str(value or ""))
    return text[:limit]


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)
