from __future__ import annotations

import dataclasses
import hashlib
import re
import urllib.parse
from collections import defaultdict
from typing import Any, Iterable


GITHUB_RE = re.compile(r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")

SHARED_DOMAIN_BLOCKLIST = {
    "github.io",
    "vercel.app",
    "netlify.app",
    "huggingface.co",
    "npmjs.com",
    "pypi.org",
    "producthunt.com",
    "x.com",
    "twitter.com",
    "github.com",
    "news.ycombinator.com",
}

GENERIC_NAME_STOPWORDS = {
    "agent",
    "agents",
    "open",
    "studio",
    "browser",
    "desktop",
    "assistant",
    "code",
    "mcp",
    "ai",
    "app",
    "tool",
    "tools",
    "sdk",
    "api",
}


@dataclasses.dataclass(frozen=True)
class ExtractedKeys:
    github_repo_keys: set[str]
    domain_keys: set[str]
    name_key: str | None
    alias_candidates: set[str]


@dataclasses.dataclass(frozen=True)
class SourceRef:
    item_id: int
    source: str
    external_id: str
    name: str


@dataclasses.dataclass(frozen=True)
class Entity:
    entity_id: str
    canonical_entity: str
    canonical_key: str
    key_type: str
    aliases: tuple[str, ...]
    source_refs: tuple[SourceRef, ...]


@dataclasses.dataclass(frozen=True)
class ResolutionResult:
    entities: list[Entity]
    item_to_entity: dict[int, str]


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, key: str) -> None:
        self.parent.setdefault(key, key)

    def find(self, key: str) -> str:
        self.add(key)
        root = key
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[key] != key:
            parent = self.parent[key]
            self.parent[key] = root
            key = parent
        return root

    def union(self, a: str, b: str) -> None:
        root_a = self.find(a)
        root_b = self.find(b)
        if root_a != root_b:
            self.parent[root_b] = root_a


def normalize_github_repo(owner: str, repo: str) -> str:
    clean_repo = repo.removesuffix(".git")
    return f"github:{owner.lower()}/{clean_repo.lower()}"


def urls_from_row(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for field in ("url", "description", "name"):
        value = row.get(field)
        if isinstance(value, str):
            values.append(value)
    metadata = row.get("metadata") or {}
    if isinstance(metadata, dict):
        for key in ("url", "website", "homepage", "repository", "repo", "link"):
            value = metadata.get(key)
            if isinstance(value, str):
                values.append(value)
        project_urls = metadata.get("project_urls")
        if isinstance(project_urls, dict):
            values.extend(str(value) for value in project_urls.values() if value)
        links = metadata.get("links")
        if isinstance(links, dict):
            values.extend(str(value) for value in links.values() if value)
    return values


def extract_github_keys(texts: Iterable[str]) -> set[str]:
    keys: set[str] = set()
    for text in texts:
        for match in GITHUB_RE.finditer(text):
            keys.add(normalize_github_repo(match.group(1), match.group(2)))
    return keys


def registrable_domain(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.hostname or "").lower().strip(".")
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) < 2:
        return None
    domain = ".".join(parts[-2:])
    if domain in SHARED_DOMAIN_BLOCKLIST:
        return None
    return domain


def extract_domain_keys(texts: Iterable[str]) -> set[str]:
    keys: set[str] = set()
    url_re = re.compile(r"https?://[^\s)>\"]+")
    for text in texts:
        for raw_url in url_re.findall(text):
            domain = registrable_domain(raw_url)
            if domain:
                keys.add(f"domain:{domain}")
    return keys


def normalize_name_key(name: str | None) -> str | None:
    if not name:
        return None
    lowered = name.lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    lowered = re.sub(r"-+", "-", lowered)
    if not lowered or lowered in GENERIC_NAME_STOPWORDS:
        return None
    if len(lowered) < 6:
        return None
    return f"name:{lowered}"


def extract_keys(row: dict[str, Any]) -> ExtractedKeys:
    texts = urls_from_row(row)
    github_keys = extract_github_keys(texts)
    domain_keys = set() if github_keys else extract_domain_keys(texts)
    raw_name = str(row.get("name") or "")
    alias_candidates = {raw_name.strip()} if raw_name.strip() else set()
    return ExtractedKeys(
        github_repo_keys=github_keys,
        domain_keys=domain_keys,
        name_key=normalize_name_key(raw_name),
        alias_candidates=alias_candidates,
    )


def key_strength(key: str) -> int:
    if key.startswith("github:"):
        return 3
    if key.startswith("domain:"):
        return 2
    return 1


def entity_id_for_key(key: str) -> str:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return f"entity:{digest}"


def resolve_entities(rows: list[dict[str, Any]], *, first_seen: str) -> ResolutionResult:
    _ = first_seen
    uf = UnionFind()
    item_keys: dict[int, list[str]] = {}
    item_aliases: dict[int, set[str]] = {}

    for row in rows:
        item_id = int(row["id"])
        keys = extract_keys(row)
        merge_keys = list(keys.github_repo_keys or keys.domain_keys)
        if not merge_keys and keys.name_key:
            merge_keys = [keys.name_key]
        if not merge_keys:
            merge_keys = [f"item:{item_id}"]
        for key in merge_keys:
            uf.add(key)
        for key in merge_keys[1:]:
            uf.union(merge_keys[0], key)
        item_keys[item_id] = merge_keys
        item_aliases[item_id] = keys.alias_candidates

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        item_id = int(row["id"])
        root = uf.find(item_keys[item_id][0])
        groups[root].append(row)

    entities: list[Entity] = []
    item_to_entity: dict[int, str] = {}
    for group_rows in groups.values():
        all_keys: set[str] = set()
        aliases: set[str] = set()
        refs: list[SourceRef] = []
        for row in group_rows:
            item_id = int(row["id"])
            all_keys.update(item_keys[item_id])
            aliases.update(item_aliases[item_id])
            refs.append(
                SourceRef(
                    item_id=item_id,
                    source=str(row["source"]),
                    external_id=str(row["external_id"]),
                    name=str(row["name"]),
                )
            )

        canonical_key = sorted(all_keys, key=lambda key: (-key_strength(key), key))[0]
        entity_id = entity_id_for_key(canonical_key)
        non_empty_aliases = sorted(
            (alias for alias in aliases if alias),
            key=lambda value: (len(value), value),
        )
        canonical_entity = non_empty_aliases[0] if non_empty_aliases else canonical_key
        entity = Entity(
            entity_id=entity_id,
            canonical_entity=canonical_entity,
            canonical_key=canonical_key,
            key_type=canonical_key.split(":", 1)[0],
            aliases=tuple(sorted(aliases)),
            source_refs=tuple(refs),
        )
        entities.append(entity)
        for ref in refs:
            item_to_entity[ref.item_id] = entity_id

    entities.sort(key=lambda entity: entity.entity_id)
    return ResolutionResult(entities=entities, item_to_entity=item_to_entity)
