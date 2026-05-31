# Pre-Layer2 Decision Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the FULLY DETERMINISTIC pre-Layer2 pipeline: entity resolution, deterministic candidate pool, edge_watch/backfill queues, bounded precise backfill, and a minimal Candidate Pool API/UI that exposes the pipeline output without doing Layer 2 feed selection. No LLM is used in this slice.

**Architecture:** Add an additive `pipeline/decision/` package that reads existing `items` and `snapshots` rows from `data/hero_radar.sqlite` and writes new decision tables. Layer 1 outputs `potential_candidates`, `edge_watch_candidates`, `backfill_jobs`, and `evidence_rows`. A minimal React app and local API expose the Candidate Pool only; Daily Feed card selection remains out of scope for this plan.

**X classifier is OUT of this slice.** The X social source classifier (LLM, per spec §4.4 two-stage processing) moves to the Layer-2 plan, where the two-stage triage/tier flow and the pass that consumes `x_tier` evidence are designed together. Keeping this slice LLM-free removes the provider/prompt/secret surface and a dead path (nothing here produces or consumes `x_tier`).

**Tech Stack:** Python 3 standard library, SQLite, `unittest`, existing `pipeline/server.py`, React + Vite for the minimal web shell. No LLM provider in this slice.

---

## Scope

Included:

- Layer 0 Stage A deterministic entity resolution, with stable cross-run entity ids.
- Layer 1 deterministic source votes, verified cross-source, evidence rows, candidate pool, edge_watch, and backfill jobs.
- Run tracking, `api_cache`, and idempotent daily-run structure.
- Bounded precise backfill framework for GitHub stargazers/repo metadata, with cache and fake-client tests.
- Local API endpoints for candidate pool inspection.
- Minimal React app shell with `Feed -> Candidate Pool` connected; `Daily Feed` shows a locked empty state because Layer 2 is not part of this plan.

Excluded:

- X social source classifier (LLM). Moves to the Layer-2 plan (spec §4.4 two-stage processing). No LLM provider, prompt, or `llm_cache` in this slice.
- npm downloads backfill. Not implemented here; npm `backfill_jobs` are NOT enqueued in this slice (would sit pending). Add with the Layer-2 plan.
- Layer 2 Daily Feed selection, card priority editorial decisions, `today_focus/secondary/backlog/suppress` assignment.
- Layer 2 bounded Kimi deepdive.
- Layer 3 chatbot / Explore agent.
- Rule/prompt editing workflow.
- Cron / scheduled automation. The code must be idempotent and cache-aware now; the scheduler is Plan G.

## File Structure

Create:

- `pipeline/decision/__init__.py`  
  Package marker and version string.
- `pipeline/decision/schema.py`  
  Decision tables, JSON helpers, run lifecycle helpers, idempotent table cleanup.
- `pipeline/decision/entity_resolution.py`  
  Stage A key extraction, domain/name safeguards, union-find clustering.
- `pipeline/decision/rules.py`  
  Rules loading, per-source level votes, verified cross-source, evidence creation, edge_watch/backfill job selection.
- `pipeline/decision/run_decision.py`  
  CLI and orchestration for Stage A + deterministic Layer 1. No LLM calls.
- `pipeline/decision/cache.py`  
  `api_cache` helpers and stable input hashes.
- `pipeline/decision/backfill.py`  
  Bounded backfill job runner for GitHub (stargazers/repo metadata) with cache.
- `pipeline/rules.json`  
  Source-specific V1 thresholds.
- `tests/test_decision_schema.py`
- `tests/test_entity_resolution.py`
- `tests/test_rules_engine.py`
- `tests/test_decision_runner.py`
- `tests/test_backfill_cache.py`
- `tests/test_candidate_api.py`
- `tests/test_hf_card_link.py`
- `web/package.json`
- `web/index.html`
- `web/src/main.jsx`
- `web/src/App.jsx`
- `web/src/styles.css`

Modify:

- `pipeline/server.py`  
  Add `GET /api/candidates`, `GET /api/evidence`, `GET /api/entity/{entity_id}`, and static serving for the React build.
- `README.md`  
  Add commands for the decision pipeline, backfill, candidate API, and web shell.
- `.gitignore`  
  Add `web/node_modules/` and `web/dist/` if not already ignored.

Do not modify (except as noted):

- Existing source adapters in `pipeline/run_pipeline.py`, EXCEPT the small HF
  card github-link enrichment in Task 9 (it only adds `metadata["repository"]` to
  HF items; it does not change existing fields or dashboard output).
- Existing source dashboard semantics.

---

### Task 1: Decision Schema, Run Tracking, And Cache Tables

**Files:**

- Create: `pipeline/decision/__init__.py`
- Create: `pipeline/decision/schema.py`
- Test: `tests/test_decision_schema.py`

- [ ] **Step 1: Write the schema tests**

Create `tests/test_decision_schema.py`:

```python
import sqlite3
import unittest

from pipeline.decision.schema import (
    begin_decision_run,
    finish_decision_run,
    init_decision_db,
    reset_decision_stage,
)


class DecisionSchemaTest(unittest.TestCase):
    def test_init_creates_expected_tables(self):
        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)

        rows = conn.execute(
            "select name from sqlite_master where type = 'table' order by name"
        ).fetchall()
        names = {row[0] for row in rows}

        self.assertIn("decision_runs", names)
        self.assertIn("entities", names)
        self.assertIn("alias_links", names)
        self.assertIn("potential_candidates", names)
        self.assertIn("edge_watch_candidates", names)
        self.assertIn("backfill_jobs", names)
        self.assertIn("entity_mentions", names)
        self.assertIn("evidence_rows", names)
        self.assertIn("api_cache", names)

    def test_run_lifecycle_is_idempotent_by_run_id(self):
        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)

        begin_decision_run(
            conn,
            run_id="decision_20260531",
            source_snapshot_run_id="source_1",
            config_hash="config-a",
            rule_version="rules-v1",
        )
        begin_decision_run(
            conn,
            run_id="decision_20260531",
            source_snapshot_run_id="source_1",
            config_hash="config-a",
            rule_version="rules-v1",
        )

        count = conn.execute("select count(*) from decision_runs").fetchone()[0]
        self.assertEqual(count, 1)

        finish_decision_run(conn, run_id="decision_20260531", status="ok", note="done")
        row = conn.execute(
            "select status, note from decision_runs where run_id = ?",
            ("decision_20260531",),
        ).fetchone()
        self.assertEqual(row, ("ok", "done"))

    def test_reset_stage_removes_run_scoped_outputs(self):
        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)

        conn.execute(
            "insert into potential_candidates(entity_id, run_id, level, fired_families_json, first_trigger_at) values (?, ?, ?, ?, ?)",
            ("entity:one", "run-a", "potential", "[]", "2026-05-31T00:00:00Z"),
        )
        conn.execute(
            "insert into evidence_rows(entity_id, canonical_entity, alias, source, event_at, metric_name, metric_value, family, rule_id, rule_version, signal_label, historical_safety, note, raw_url_or_ref, run_id) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "entity:one",
                "One",
                "One",
                "github_trending",
                "2026-05-31T00:00:00Z",
                "stars_today",
                "1200",
                "github",
                "github_trending_daily_potential",
                "rules-v1",
                "early_trigger",
                "snapshot_only",
                "passed",
                "item:1",
                "run-a",
            ),
        )

        reset_decision_stage(conn, run_id="run-a", tables=["potential_candidates", "evidence_rows"])

        self.assertEqual(conn.execute("select count(*) from potential_candidates").fetchone()[0], 0)
        self.assertEqual(conn.execute("select count(*) from evidence_rows").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the failing schema tests**

Run:

```bash
python3 -m unittest tests/test_decision_schema.py -v
```

Expected: `ModuleNotFoundError: No module named 'pipeline.decision'`.

- [ ] **Step 3: Create the decision package and schema helpers**

Create `pipeline/decision/__init__.py`:

```python
"""Decision-layer package for Hero Radar."""

DECISION_SCHEMA_VERSION = "decision-v1"
```

Create `pipeline/decision/schema.py`:

```python
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from typing import Iterable, Sequence


DECISION_SCHEMA_SQL = """
create table if not exists decision_runs (
    run_id text primary key,
    source_snapshot_run_id text,
    started_at text not null,
    completed_at text,
    status text not null,
    config_hash text not null,
    rule_version text not null,
    note text
);

create table if not exists entities (
    entity_id text primary key,
    canonical_entity text not null,
    canonical_key text not null,
    key_type text not null,
    first_seen text not null,
    aliases_json text not null,
    source_item_ids_json text not null
);

create table if not exists alias_links (
    id integer primary key autoincrement,
    entity_id text not null,
    source text not null,
    external_id text not null,
    alias text not null,
    confidence text not null,
    origin text not null,
    approved integer not null default 0,
    created_at text not null
);

create table if not exists entity_merge_proposals (
    id integer primary key autoincrement,
    run_id text not null,
    orphan text not null,
    target_entity_id text,
    confidence real not null,
    reason text not null,
    status text not null,
    created_at text not null
);

create table if not exists potential_candidates (
    id integer primary key autoincrement,
    entity_id text not null,
    run_id text not null,
    level text not null,
    fired_families_json text not null,
    first_trigger_at text not null,
    unique(run_id, entity_id)
);

create table if not exists edge_watch_candidates (
    id integer primary key autoincrement,
    entity_id text not null,
    run_id text not null,
    reason_json text not null,
    source_refs_json text not null,
    status text not null,
    unique(run_id, entity_id)
);

create table if not exists backfill_jobs (
    id integer primary key autoincrement,
    entity_id text not null,
    run_id text not null,
    source text not null,
    reason text not null,
    status text not null,
    requested_at text not null,
    completed_at text,
    result_ref text,
    unique(run_id, entity_id, source, reason)
);

create table if not exists entity_mentions (
    id integer primary key autoincrement,
    entity_id text not null,
    run_id text not null,
    window text not null,
    distinct_authors integer not null,
    credible_authors integer not null,
    mention_count integer not null,
    mention_acceleration real,
    source_refs_json text not null,
    unique(run_id, entity_id, window)
);

create table if not exists evidence_rows (
    id integer primary key autoincrement,
    entity_id text not null,
    canonical_entity text not null,
    alias text,
    source text not null,
    event_at text not null,
    relative_to_reference text,
    metric_name text not null,
    metric_value text not null,
    family text not null,
    rule_id text not null,
    rule_version text not null,
    signal_label text not null,
    historical_safety text not null,
    note text not null,
    raw_url_or_ref text,
    run_id text not null
);

create table if not exists api_cache (
    cache_key text primary key,
    source text not null,
    external_id text not null,
    window text not null,
    input_hash text not null,
    response_json text not null,
    status text not null,
    fetched_at text not null,
    expires_at text,
    error text
);

create index if not exists idx_entities_key on entities(key_type, canonical_key);
create index if not exists idx_evidence_run_entity on evidence_rows(run_id, entity_id);
create index if not exists idx_candidates_run_level on potential_candidates(run_id, level);
create index if not exists idx_edge_watch_run on edge_watch_candidates(run_id);
create index if not exists idx_backfill_run_status on backfill_jobs(run_id, status);
"""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def to_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def init_decision_db(conn: sqlite3.Connection) -> None:
    conn.executescript(DECISION_SCHEMA_SQL)


def begin_decision_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    source_snapshot_run_id: str | None,
    config_hash: str,
    rule_version: str,
) -> None:
    conn.execute(
        """
        insert into decision_runs(run_id, source_snapshot_run_id, started_at, status, config_hash, rule_version, note)
        values (?, ?, ?, 'running', ?, ?, '')
        on conflict(run_id) do update set
            source_snapshot_run_id = excluded.source_snapshot_run_id,
            status = 'running',
            config_hash = excluded.config_hash,
            rule_version = excluded.rule_version
        """,
        (run_id, source_snapshot_run_id, utc_now(), config_hash, rule_version),
    )
    conn.commit()


def finish_decision_run(conn: sqlite3.Connection, *, run_id: str, status: str, note: str = "") -> None:
    conn.execute(
        "update decision_runs set completed_at = ?, status = ?, note = ? where run_id = ?",
        (utc_now(), status, note, run_id),
    )
    conn.commit()


def reset_decision_stage(conn: sqlite3.Connection, *, run_id: str, tables: Sequence[str]) -> None:
    allowed = {
        "potential_candidates",
        "edge_watch_candidates",
        "backfill_jobs",
        "entity_mentions",
        "evidence_rows",
    }
    for table in tables:
        if table not in allowed:
            raise ValueError(f"refusing to reset unknown run-scoped table: {table}")
        conn.execute(f"delete from {table} where run_id = ?", (run_id,))
    conn.commit()
```

- [ ] **Step 4: Run the schema tests**

Run:

```bash
python3 -m unittest tests/test_decision_schema.py -v
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add pipeline/decision/__init__.py pipeline/decision/schema.py tests/test_decision_schema.py
git commit -m "feat: add decision schema"
```

---

### Task 2: Stage A Entity Resolution

**Files:**

- Create: `pipeline/decision/entity_resolution.py`
- Test: `tests/test_entity_resolution.py`

- [ ] **Step 1: Write entity-resolution tests**

Create `tests/test_entity_resolution.py`:

```python
import unittest

from pipeline.decision.entity_resolution import (
    extract_keys,
    normalize_name_key,
    resolve_entities,
)


class EntityResolutionTest(unittest.TestCase):
    def test_extracts_github_repo_from_url_and_text(self):
        row = {
            "id": 1,
            "source": "hn_algolia",
            "external_id": "hn-1",
            "name": "Show HN: Demo",
            "url": "https://news.ycombinator.com/item?id=1",
            "description": "Repo https://github.com/Owner/Repo?tab=readme",
            "metadata": {},
        }

        keys = extract_keys(row)

        self.assertIn("github:owner/repo", keys.github_repo_keys)

    def test_shared_domains_are_not_project_domain_keys(self):
        row = {
            "id": 2,
            "source": "product_hunt",
            "external_id": "ph-1",
            "name": "Demo",
            "url": "https://producthunt.com/posts/demo",
            "description": "",
            "metadata": {"website": "https://demo.vercel.app"},
        }

        keys = extract_keys(row)

        self.assertEqual(keys.domain_keys, set())

    def test_specific_domain_key_is_allowed(self):
        row = {
            "id": 3,
            "source": "product_hunt",
            "external_id": "ph-2",
            "name": "Demo",
            "url": "https://producthunt.com/posts/demo",
            "description": "",
            "metadata": {"website": "https://openclaw.dev"},
        }

        keys = extract_keys(row)

        self.assertEqual(keys.domain_keys, {"domain:openclaw.dev"})

    def test_generic_name_key_is_alias_only(self):
        self.assertIsNone(normalize_name_key("agent"))
        self.assertIsNone(normalize_name_key("MCP"))
        self.assertEqual(normalize_name_key("Claude Code Router"), "name:claude-code-router")

    def test_resolve_entities_unions_by_strong_github_key(self):
        rows = [
            {
                "id": 10,
                "source": "github_trending",
                "external_id": "owner/repo",
                "name": "owner/repo",
                "url": "https://github.com/owner/repo",
                "description": "",
                "metadata": {},
            },
            {
                "id": 11,
                "source": "hn_algolia",
                "external_id": "hn-11",
                "name": "Show HN: Repo",
                "url": "https://github.com/owner/repo",
                "description": "",
                "metadata": {},
            },
        ]

        result = resolve_entities(rows, first_seen="2026-05-31T00:00:00Z")

        self.assertEqual(len(result.entities), 1)
        entity = result.entities[0]
        self.assertEqual(entity.canonical_key, "github:owner/repo")
        self.assertEqual({ref.item_id for ref in entity.source_refs}, {10, 11})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the failing entity tests**

Run:

```bash
python3 -m unittest tests/test_entity_resolution.py -v
```

Expected: `ModuleNotFoundError` or import failure for `pipeline.decision.entity_resolution`.

- [ ] **Step 3: Implement Stage A entity resolution**

Create `pipeline/decision/entity_resolution.py`:

```python
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
        ra = self.find(a)
        rb = self.find(b)
        if ra != rb:
            self.parent[rb] = ra


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
    name_key = normalize_name_key(str(row.get("name") or ""))
    alias_candidates = {str(row.get("name") or "").strip()} if row.get("name") else set()
    return ExtractedKeys(github_keys, domain_keys, name_key, alias_candidates)


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
            orphan_key = f"item:{item_id}"
            merge_keys = [orphan_key]
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
        non_empty_aliases = sorted((alias for alias in aliases if alias), key=lambda value: (len(value), value))
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
```

- [ ] **Step 4: Run entity tests**

Run:

```bash
python3 -m unittest tests/test_entity_resolution.py -v
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add pipeline/decision/entity_resolution.py tests/test_entity_resolution.py
git commit -m "feat: add deterministic entity resolution"
```

---

### Task 3: Rules Engine, Evidence Rows, Candidates, Edge Watch, And Backfill Queue

**Files:**

- Create: `pipeline/rules.json`
- Create: `pipeline/decision/rules.py`
- Test: `tests/test_rules_engine.py`

- [ ] **Step 1: Write rules-engine tests**

Create `tests/test_rules_engine.py`:

```python
import unittest

from pipeline.decision.entity_resolution import resolve_entities
from pipeline.decision.rules import evaluate_entities


class RulesEngineTest(unittest.TestCase):
    def test_github_trending_daily_potential(self):
        rows = [
            {
                "id": 1,
                "source": "github_trending",
                "external_id": "owner/repo",
                "name": "owner/repo",
                "url": "https://github.com/owner/repo",
                "description": "repo",
                "metadata": {"period": "daily", "window": "24h", "period_stars": 1200, "stars_total": 3000},
                "fetched_at": "2026-05-31T00:00:00Z",
            }
        ]
        resolution = resolve_entities(rows, first_seen="2026-05-31T00:00:00Z")

        result = evaluate_entities(
            rows,
            resolution,
            run_id="run-1",
            rule_version="rules-v1",
            now="2026-05-31T00:00:00Z",
        )

        self.assertEqual(len(result.potential_candidates), 1)
        self.assertEqual(result.potential_candidates[0].level, "potential")
        self.assertEqual(result.evidence_rows[0].metric_name, "stars_today")

    def test_two_verified_weak_signals_create_potential(self):
        rows = [
            {
                "id": 1,
                "source": "hn_firebase",
                "external_id": "hn-1",
                "name": "Repo on HN",
                "url": "https://github.com/owner/repo",
                "description": "",
                "metadata": {"score": 75, "comments": 12, "list": "topstories"},
                "fetched_at": "2026-05-31T00:00:00Z",
            },
            {
                "id": 2,
                "source": "product_hunt",
                "external_id": "ph-1",
                "name": "Repo",
                "url": "https://producthunt.com/posts/repo",
                "description": "",
                "metadata": {"daily_rank": 8, "website": "https://github.com/owner/repo"},
                "fetched_at": "2026-05-31T00:00:00Z",
            },
        ]
        resolution = resolve_entities(rows, first_seen="2026-05-31T00:00:00Z")

        result = evaluate_entities(
            rows,
            resolution,
            run_id="run-1",
            rule_version="rules-v1",
            now="2026-05-31T00:00:00Z",
        )

        self.assertEqual(result.potential_candidates[0].level, "potential")
        evidence_rule_ids = {row.rule_id for row in result.evidence_rows}
        self.assertIn("verified_cross_source_two_weak_48h", evidence_rule_ids)

    def test_repofomo_watch_without_acceleration_becomes_edge_watch(self):
        rows = [
            {
                "id": 3,
                "source": "github_movers_repofomo",
                "external_id": "repofomo:owner/repo",
                "name": "owner/repo",
                "url": "https://github.com/owner/repo",
                "description": "",
                "metadata": {
                    "stars_7d": 350,
                    "stars_30d": 2000,
                    "stars_60d": 4500,
                    "stars_total": 9000,
                },
                "fetched_at": "2026-05-31T00:00:00Z",
            }
        ]
        resolution = resolve_entities(rows, first_seen="2026-05-31T00:00:00Z")

        result = evaluate_entities(
            rows,
            resolution,
            run_id="run-1",
            rule_version="rules-v1",
            now="2026-05-31T00:00:00Z",
        )

        self.assertEqual(result.potential_candidates, [])
        self.assertEqual(len(result.edge_watch_candidates), 1)
        self.assertEqual(result.edge_watch_candidates[0].entity_id, resolution.entities[0].entity_id)

    def test_three_strict_hn_stories_create_potential(self):
        rows = [
            {
                "id": 20 + i,
                "source": "hn_algolia",
                "external_id": f"7d:agent:{1000 + i}",
                "name": f"Show HN: Repo story {i}",
                "url": "https://github.com/owner/repo",
                "description": "",
                "metadata": {
                    "window": "7d",
                    "query_label": "agent",
                    "points": 60,
                    "created_at": "2026-05-28T00:00:00Z",
                    "story_id": str(1000 + i),
                },
                "fetched_at": "2026-05-31T00:00:00Z",
            }
            for i in range(3)
        ]
        resolution = resolve_entities(rows, first_seen="2026-05-31T00:00:00Z")

        result = evaluate_entities(
            rows,
            resolution,
            run_id="run-1",
            rule_version="rules-v1",
            now="2026-05-31T00:00:00Z",
        )

        # all three stories link to github:owner/repo -> one entity, deduped count = 3
        self.assertEqual(len(resolution.entities), 1)
        self.assertEqual(result.potential_candidates[0].level, "potential")
        self.assertIn("strict_story_count_7d", {row.metric_name for row in result.evidence_rows})

    def test_two_huggingface_resources_48h_create_potential(self):
        # HF resources link to the entity only deterministically: via a github/domain
        # link in the card, or exact full-name match. Here both cards link the repo.
        rows = [
            {
                "id": 30,
                "source": "huggingface_spaces",
                "external_id": "user/clawdbot-demo",
                "name": "user/clawdbot-demo",
                "url": "https://huggingface.co/spaces/user/clawdbot-demo",
                "description": "",
                "metadata": {"created_at": "2026-05-30T10:00:00Z", "likes": 5,
                             "repository": "https://github.com/owner/repo"},
                "fetched_at": "2026-05-31T00:00:00Z",
            },
            {
                "id": 31,
                "source": "huggingface_models",
                "external_id": "lab/clawdbot-demo",
                "name": "lab/clawdbot-demo",
                "url": "https://huggingface.co/lab/clawdbot-demo",
                "description": "",
                "metadata": {"created_at": "2026-05-30T18:00:00Z", "likes": 2,
                             "repository": "https://github.com/owner/repo"},
                "fetched_at": "2026-05-31T00:00:00Z",
            },
        ]
        resolution = resolve_entities(rows, first_seen="2026-05-31T00:00:00Z")

        result = evaluate_entities(
            rows,
            resolution,
            run_id="run-1",
            rule_version="rules-v1",
            now="2026-05-31T00:00:00Z",
        )

        # both cards link github:owner/repo -> one entity, 2 HF resources in 48h
        self.assertEqual(len(resolution.entities), 1)
        self.assertEqual(result.potential_candidates[0].level, "potential")
        self.assertIn("hf_resources_48h", {row.metric_name for row in result.evidence_rows})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the failing rules tests**

Run:

```bash
python3 -m unittest tests/test_rules_engine.py -v
```

Expected: import failure for `pipeline.decision.rules`.

- [ ] **Step 3: Add initial V1 rules data**

Create `pipeline/rules.json`:

```json
{
  "version": "rules-v1",
  "levels": ["none", "watch", "potential", "high_potential"],
  "github_trending": {
    "daily_stars": {
      "watch": 300,
      "potential": 1000,
      "high_potential": 3000
    }
  },
  "trending_repos": {
    "stars_velocity": {
      "watch": 300,
      "potential": 800,
      "high_potential": 2500
    },
    "forks_velocity": {
      "watch": 50,
      "potential": 75,
      "high_potential": 300
    }
  },
  "repofomo": {
    "stars_7d_watch": 200,
    "stars_7d_potential_if_accelerating": 1000,
    "stars_7d_high": 5000,
    "new_forks_high": 300
  },
  "hn": {
    "front_page_score_watch": 50,
    "front_page_score_potential": 100,
    "strict_story_count_7d_watch": 1,
    "strict_story_count_7d_potential": 3
  },
  "product_hunt": {
    "rank_potential": 5,
    "rank_watch": 10
  },
  "huggingface": {
    "exact_resources_48h_potential": 2,
    "single_resource_watch": 1
  },
  "verified_cross_source": {
    "weak_signals_48h_potential": 2
  },
  "github_search_backfill": {
    "min_stars_per_day": 50,
    "pushed_within_days": 14,
    "created_within_days": 180
  },
  "backfill_max_jobs": 40
}
```

Note: the rules engine matches on REAL source names (`github_trending`,
`github_movers_trending_repos`, `github_movers_repofomo`, `hn_firebase`,
`product_hunt`, `github_search`). github_search has `metadata.stars`,
`metadata.created_at`, `metadata.pushed_at` (NOT a velocity field), so the cheap
pre-filter computes `stars_per_day = stars / days_since_created`.

- [ ] **Step 4: Implement rules engine**

Create `pipeline/decision/rules.py` with dataclasses for `EvidenceRow`, `PotentialCandidate`, `EdgeWatchCandidate`, `BackfillJob`, and `RuleEvaluationResult`.

The public API is:

```text
load_rules(path: Path | None = None) -> dict[str, Any]

evaluate_entities(
    rows: list[dict[str, Any]],
    resolution: ResolutionResult,
    run_id: str,
    rule_version: str,
    now: str,
    rules: dict[str, Any] | None = None,
    extra_github_signals: dict[str, dict[str, float]] | None = None,
) -> RuleEvaluationResult
```

`extra_github_signals` maps entity_id to a dict such as
`{"stars_24h": 1200.0, "stars_7d": 4200.0}` from
precise backfill. In PASS 2 these vote with the SAME github velocity thresholds
(300/1000/3000) as the boards, so an off-board repo with a precise stars_24h >= 1000
becomes Potential. In PASS 1 it is None (board-only).

`load_rules` reads `pipeline/rules.json` when `path` is not provided. `evaluate_entities` groups source rows by `ResolutionResult.entities`, creates evidence rows for every triggered rule, promotes entities into `potential_candidates` or `edge_watch_candidates`, and emits bounded `github_stargazers` backfill jobs for GitHub entities that need precise metrics before Layer 2.

The implementation must follow this behavior exactly:

IMPORTANT: source names match the REAL adapters in `data/hero_radar.sqlite`. There
is no `github_movers` source and no `metadata.source_kind`. The two boards are
distinct sources: `github_movers_trending_repos` and `github_movers_repofomo`.
Total-stars field is `metadata.stars_total` (NOT `total_stars`).

```text
github_trending:
  source == "github_trending". Only metadata.period == "daily" votes by daily_stars.
  metric_name = stars_today (from metadata.period_stars).
  Dedup: the same repo appears under multiple scope_language rows ("all", "python",
  "typescript", and other language-specific rows);
  take the max period_stars per repo before voting so evidence is not double-counted.
  historical_safety = snapshot_only.

trending_repos:
  source == "github_movers_trending_repos". Only metadata.period == "daily".
  Use metadata.stars_velocity and metadata.forks_velocity.
  metric_name = stars_velocity or forks_velocity.
  historical_safety = snapshot_only.

repofomo:
  source == "github_movers_repofomo".
  stars_7d >= 200 gives watch only.
  potential requires stars_7d >= 1000 AND stars_7d/7 > stars_30d/30 > stars_60d/60.
  high_potential requires stars_7d >= 5000 or new_forks >= 300.

hn_firebase:
  source == "hn_firebase". metadata.score >= 50 gives weak watch.
  score >= 100 gives potential when entity is strongly linked (github/domain key).

hn_algolia:
  source == "hn_algolia". STRICT matching only: a story counts for an entity only if
  it landed in the entity's cluster via a github/domain URL (Stage A link). Generic
  keyword-only stories cluster as orphans and are NOT counted (this slice has no
  Stage B, so bare-name HN stories do not merge -- acceptable).
  Count distinct stories in the entity cluster with created_at within 7d, DEDUPED by
  HN objectID (the same story appears under the 24h/7d/30d windows).
  1 strict story = watch; >= strict_story_count_7d_potential (3) in 7d = potential.
  metric_name = strict_story_count_7d. event_at = story created_at (as_of_safe);
  points/comments are partial_as_of.

product_hunt:
  source == "product_hunt". daily_rank <= 10 or weekly_rank <= 10 gives weak watch.
  daily_rank <= 5 or weekly_rank <= 5 gives potential.

huggingface:
  sources huggingface_models / huggingface_datasets / huggingface_spaces.
  Count distinct HF resources in the entity cluster with createdAt within 48h. An HF
  resource joins an entity ONLY deterministically: (i) its card metadata carries a
  github/domain link to the entity (Stage A), or (ii) its full id exactly matches an
  entity name_key. Different-uploader same-project resources (KALLLA/clawdbot vs
  acpr123/clawdbot) do NOT merge here -- that needs Stage B (Layer 2).
  single_resource_watch (1) in 48h = watch; exact_resources_48h_potential (2) = potential.
  HF alone tops out at potential (ecosystem echo); high needs verified corroboration.
  metric_name = hf_resources_48h. event_at = createdAt (as_of_safe); likes/downloads
  are snapshot_only.
  Real-data link: Task 9 enriches the top-N trending HF resources with
  `metadata["repository"]` = the github link found in the card/README, which
  Stage A reads, so those HF resources attach to the github entity deterministically.
  Resources with no discoverable github link still only attach via exact name_key;
  cross-uploader same-project merging is left to Stage B (Layer 2).

verified_cross_source:
  Two weak source-family evidence rows on the same entity within 48h create potential.
  Only entities with github or domain canonical keys count as verified.

edge_watch:
  watch-level entities that did not become potential are written to edge_watch_candidates.

backfill_jobs (BOUNDED -- the whole design depends on this staying small):
  1. github canonical entities at potential/high_potential -> github_stargazers job
     (precise confirmation for board movers).
  2. github_search OFF-BOARD shortlist via the cheap stars_per_day pre-filter (NO
     backfill in this step): an entity qualifies only if ALL of:
       - stars_per_day = stars / days_since_created >= min_stars_per_day (50), AND
       - pushed_at within pushed_within_days (14) [active, not dead], AND
       - created_at within created_within_days (180) [young; lifetime avg ~ recent], AND
       - NOT already on any github board this run (off-board only).
     On 2026-05-31 this takes github_search 846 -> ~109 candidates.
  3. Rank all qualifying jobs by signal strength (board potential first, then
     off-board stars_per_day desc) and HARD CAP at backfill_max_jobs (40).
  npm backfill is OUT of this slice: do NOT enqueue npm_downloads jobs here.

Note: stars_per_day is a LIFETIME-average velocity proxy, used only to choose WHO
to backfill cheaply. The backfill then computes the PRECISE recent stars_24h/7d that
actually decides the level (see the two-pass runner in Task 4).
```

Use ordinal level ordering:

```python
LEVEL_ORDER = {"none": 0, "watch": 1, "potential": 2, "high_potential": 3}
```

- [ ] **Step 5: Run rules tests**

Run:

```bash
python3 -m unittest tests/test_rules_engine.py -v
```

Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add pipeline/rules.json pipeline/decision/rules.py tests/test_rules_engine.py
git commit -m "feat: add deterministic decision rules"
```

---

### Task 4: Deterministic Decision Runner And Candidate Export

**Files:**

- Create: `pipeline/decision/run_decision.py`
- Test: `tests/test_decision_runner.py`
- Modify: `README.md`

- [ ] **Step 1: Write runner tests**

Create `tests/test_decision_runner.py`:

```python
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from pipeline.decision.run_decision import run_decision
from pipeline.decision.schema import init_decision_db


def seed_source_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        create table snapshots (
            id integer primary key autoincrement,
            run_id text not null,
            source text not null,
            fetched_at text not null,
            status text not null,
            item_count integer not null,
            error text
        );
        create table items (
            id integer primary key autoincrement,
            run_id text not null,
            snapshot_id integer not null,
            source text not null,
            external_id text not null,
            name text not null,
            url text,
            fetched_at text not null,
            heat real,
            velocity real,
            acceleration real,
            source_rank integer,
            description text,
            metadata_json text not null,
            raw_json text not null
        );
        """
    )
    conn.execute(
        "insert into snapshots(run_id, source, fetched_at, status, item_count, error) values (?, ?, ?, ?, ?, ?)",
        ("source-run-1", "github_trending", "2026-05-31T00:00:00Z", "ok", 1, None),
    )
    snapshot_id = conn.execute("select id from snapshots").fetchone()[0]
    conn.execute(
        """
        insert into items(run_id, snapshot_id, source, external_id, name, url, fetched_at, metadata_json, raw_json)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "source-run-1",
            snapshot_id,
            "github_trending",
            "owner/repo",
            "owner/repo",
            "https://github.com/owner/repo",
            "2026-05-31T00:00:00Z",
            json.dumps({"period": "daily", "window": "24h", "period_stars": 1200, "stars_total": 3000}),
            "{}",
        ),
    )
    conn.commit()


class DecisionRunnerTest(unittest.TestCase):
    def test_runner_writes_entities_candidates_and_export(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            export_path = Path(tmpdir) / "candidates.json"
            conn = sqlite3.connect(db_path)
            seed_source_tables(conn)
            init_decision_db(conn)
            conn.close()

            summary = run_decision(
                db_path=db_path,
                run_id="decision-run-1",
                export_json_path=export_path,
                now="2026-05-31T00:00:00Z",
            )

            self.assertEqual(summary["potential_candidates"], 1)
            self.assertTrue(export_path.exists())

            payload = json.loads(export_path.read_text())
            self.assertEqual(payload["run_id"], "decision-run-1")
            self.assertEqual(payload["candidates"][0]["level"], "potential")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the failing runner tests**

Run:

```bash
python3 -m unittest tests/test_decision_runner.py -v
```

Expected: import failure for `pipeline.decision.run_decision`.

- [ ] **Step 3: Implement the decision runner**

Create `pipeline/decision/run_decision.py` with:

```text
read_latest_items(conn):
  Select rows from the latest snapshot for each source.
  Decode metadata_json/raw_json into dictionaries.

reconcile_entity_ids(conn, resolution):
  STABLE cross-run identity (fixes entity_id drift). The resolver's entity_id is
  content-derived from the current canonical_key, which can change run to run as a
  cluster gains/loses keys. Before writing, for each resolved entity look up
  alias_links for ANY of its keys/aliases; if a prior entity_id already owns an
  overlapping key, REUSE that entity_id instead of the freshly hashed one. Only
  mint a new entity_id when no prior key overlaps. Per-account human_status,
  watching, and "new since last visit" all key on entity_id, so it must be stable.
  Record the chosen keys/aliases into alias_links.

write_entities(conn, resolution, reconciled_ids, first_seen):
  Upsert ONLY entities referenced by this run's potential_candidates,
  edge_watch_candidates, evidence_rows, backfill_jobs, or entity_mentions.
  Do NOT persist the ~15k orphan (item:N) entities that triggered nothing; they are
  noise and would balloon the entities table every run.

write_evidence(conn, result):
  Insert evidence_rows for run_id after clearing existing run_id rows.

write_candidates(conn, result):
  Insert potential_candidates, edge_watch_candidates, backfill_jobs.

export_candidates(conn, run_id, path):
  Write JSON with run_id, generated_at, candidates, edge_watch, backfill_jobs.

run_decision(db_path, run_id, export_json_path, now, github_client=None):
  init_decision_db
  begin_decision_run
  reset run-scoped output tables ONCE at the start (not between passes)
  read latest items
  resolve_entities; reconcile_entity_ids (reuse stable ids)

  PASS 1 (board / cheap sources):
    evaluate_entities over items -> preliminary candidates, edge_watch,
    and a backfill shortlist (board-potential confirmations + off-board
    github_search via the stars_per_day pre-filter, capped at backfill_max_jobs).
    Persist the shortlist into backfill_jobs (status pending) so the backfill
    runner can read them.

  BACKFILL (only if github_client is provided; see Task 6):
    run_backfill_jobs(conn, run_id, github_client, now) reads the pending jobs,
    computes precise stars_24h / stars_7d per repo, writes github_backfill evidence,
    and returns a map entity_id -> {stars_24h, stars_7d} for PASS 2.

  PASS 2 (consume backfill):
    re-evaluate, feeding each backfilled stars_24h/stars_7d as an extra GitHub
    source vote using the SAME github velocity thresholds (300/1000/3000). This is
    where off-board movers get PROMOTED and board movers get confirmed. Entities
    still short of Potential stay in edge_watch.

  write referenced entities + final evidence + candidates (once)
  export candidates
  finish_decision_run

Notes:
  - Keep both passes in memory; write evidence/candidates ONCE at the end so the
    initial reset never wipes backfill or pass-2 results.
  - evaluate_entities gains an optional `extra_github_signals` arg: a map
    entity_id -> {stars_24h, stars_7d} that pass 2 treats as a github source vote.
  - When github_client is None (unit tests, or a no-network run), run_decision does
    PASS 1 only and skips backfill; the deterministic board pool is still produced.
```

The CLI adds `--backfill` (uses `GITHUB_TOKEN`); without it, the runner does the
deterministic board-only pass so it stays runnable offline and in CI.

Default `run_id` when `--run-id` is omitted: `decision_YYYYMMDD` (UTC date), so a
same-day re-run is idempotent. Re-running resets this run_id's output tables once at
the start and recomputes both passes fresh. Within a single run, the only writes
after the initial reset are: backfill_jobs (after PASS 1), then the final
entities/evidence/candidates (after PASS 2). All deterministic; no LLM.

The CLI must support:

```bash
python3 -m pipeline.decision.run_decision \
  --db data/hero_radar.sqlite \
  --run-id decision_20260531 \
  --export-json data/exports/candidates_latest.json
```

- [ ] **Step 4: Run runner tests**

Run:

```bash
python3 -m unittest tests/test_decision_runner.py -v
```

Expected: `OK`.

- [ ] **Step 5: Run against local data without model calls**

Run:

```bash
python3 -m pipeline.decision.run_decision \
  --db data/hero_radar.sqlite \
  --export-json data/exports/candidates_latest.json
```

Expected:

```text
Decision run complete
entities: a positive integer greater than zero for a populated local database
potential_candidates: a non-negative integer
edge_watch_candidates: a non-negative integer
backfill_jobs: a non-negative integer
export: data/exports/candidates_latest.json
```

- [ ] **Step 6: Update README**

Add this section to `README.md`:

~~~markdown
## Decision Pipeline Slice

Run the deterministic pre-Layer2 decision pipeline:

```bash
python3 -m pipeline.decision.run_decision \
  --db data/hero_radar.sqlite \
  --export-json data/exports/candidates_latest.json
```

This reads the latest source snapshots, performs Stage A entity resolution,
evaluates deterministic source rules, writes `potential_candidates`,
`edge_watch_candidates`, `backfill_jobs`, and `evidence_rows`, then exports
`data/exports/candidates_latest.json`.

This command does not call any LLM and does not run Layer 2 Daily Feed selection.
```
~~~

- [ ] **Step 7: Commit**

```bash
git add pipeline/decision/run_decision.py tests/test_decision_runner.py README.md
git commit -m "feat: add deterministic decision runner"
```

---

### Task 5: X Source Classifier — MOVED OUT OF THIS SLICE

The LLM X classifier (provider, prompt, `x_classifier.py`) is removed from this plan
and moves to the Layer-2 plan, implemented per spec §4.4 (Stage 0 deterministic
pre-extract; Stage 1 batched cheap-model triage; Stage 2 bounded per-entity tier).
Reason: this slice has no X classifier to produce `x_tier` and no Layer-2 pass to
consume it, so building it here would be a dead, costly path. This slice stays
fully deterministic (the only external calls are GitHub REST for backfill).

---

### Task 6: Precise Backfill And API Cache

**Files:**

- Create: `pipeline/decision/cache.py`
- Create: `pipeline/decision/backfill.py`
- Test: `tests/test_backfill_cache.py`
- Modify: `README.md`

- [ ] **Step 1: Write backfill/cache tests**

Create `tests/test_backfill_cache.py`:

```python
import json
import sqlite3
import unittest

from pipeline.decision.backfill import run_backfill_jobs
from pipeline.decision.schema import init_decision_db


class FakeGitHubClient:
    def repo_metadata(self, full_name):
        return {"full_name": full_name, "stargazers_count": 1500, "forks_count": 120}

    def stargazers_since(self, full_name, since_iso):
        return [
            {"user": "a", "starred_at": "2026-05-30T12:00:00Z"},
            {"user": "b", "starred_at": "2026-05-30T13:00:00Z"},
        ]


class BackfillCacheTest(unittest.TestCase):
    def test_backfill_writes_api_cache_and_evidence(self):
        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        conn.execute(
            """
            insert into entities(entity_id, canonical_entity, canonical_key, key_type, first_seen, aliases_json, source_item_ids_json)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            ("entity:repo", "owner/repo", "github:owner/repo", "github", "2026-05-31T00:00:00Z", "[]", "[]"),
        )
        conn.execute(
            """
            insert into backfill_jobs(entity_id, run_id, source, reason, status, requested_at)
            values (?, ?, ?, ?, ?, ?)
            """,
            ("entity:repo", "run-1", "github_stargazers", "potential_candidate", "pending", "2026-05-31T00:00:00Z"),
        )
        conn.commit()

        summary = run_backfill_jobs(
            conn,
            run_id="run-1",
            github_client=FakeGitHubClient(),
            now="2026-05-31T00:00:00Z",
        )

        self.assertEqual(summary["completed"], 1)
        cache_count = conn.execute("select count(*) from api_cache").fetchone()[0]
        evidence_count = conn.execute("select count(*) from evidence_rows where source = 'github_backfill'").fetchone()[0]
        self.assertEqual(cache_count, 1)
        self.assertGreaterEqual(evidence_count, 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run failing backfill tests**

Run:

```bash
python3 -m unittest tests/test_backfill_cache.py -v
```

Expected: import failure for `pipeline.decision.backfill`.

- [ ] **Step 3: Implement cache helpers**

Create `pipeline/decision/cache.py`:

```python
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from pipeline.decision.schema import utc_now


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def api_cache_key(*, source: str, external_id: str, window: str, input_hash: str) -> str:
    return f"api:{source}:{external_id}:{window}:{input_hash}"


def get_api_cache(conn: sqlite3.Connection, cache_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "select response_json, status from api_cache where cache_key = ?",
        (cache_key,),
    ).fetchone()
    if not row or row[1] != "ok":
        return None
    return json.loads(row[0])


def put_api_cache(
    conn: sqlite3.Connection,
    *,
    cache_key: str,
    source: str,
    external_id: str,
    window: str,
    input_hash: str,
    response: dict[str, Any],
    status: str = "ok",
    error: str | None = None,
) -> None:
    conn.execute(
        """
        insert into api_cache(cache_key, source, external_id, window, input_hash, response_json, status, fetched_at, expires_at, error)
        values (?, ?, ?, ?, ?, ?, ?, ?, null, ?)
        on conflict(cache_key) do update set
            response_json = excluded.response_json,
            status = excluded.status,
            fetched_at = excluded.fetched_at,
            error = excluded.error
        """,
        (
            cache_key,
            source,
            external_id,
            window,
            input_hash,
            json.dumps(response, ensure_ascii=False, sort_keys=True),
            status,
            utc_now(),
            error,
        ),
    )
    conn.commit()
```

- [ ] **Step 4: Implement backfill runner**

Create `pipeline/decision/backfill.py` with:

GitHub stargazers reality (the `stargazers_since` implementation is the tricky part):

```text
- Must send header  Accept: application/vnd.github.star+json  to get starred_at.
- The list endpoint paginates OLDEST-first. To get the last 24h you must page from
  the LAST page BACKWARD (compute last page from repo stargazers_count / per_page),
  not filter from the front.
- Cap pages per repo (e.g. <= 5 recent pages). For very large/hot repos GitHub also
  rejects pages past ~400; in that case the 24h count is a LOWER BOUND. Mark the
  evidence note accordingly. (The benchmark hit this exact page cap on OpenClaw.)
- Always send GITHUB_TOKEN; without it the rate limit makes this unusable.
```

```text
GitHubClient:
  repo_metadata(full_name) -> dict
  stargazers_since(full_name, since_iso) -> list[dict]   # star+json, reverse-paged, page-capped
  Uses GITHUB_TOKEN when available.

run_backfill_jobs(conn, run_id, github_client, now):
  Select pending backfill_jobs for run_id.
  For github_stargazers:
    read entity canonical_key github:owner/repo
    compute input hash with full_name and now date
    use api_cache
    on miss call repo_metadata and stargazers_since (last ~7d so both windows derive
      from one fetch)
    write api_cache
    derive precise github signals from starred_at:
      stars_24h = count starred_at within [now-24h, now]
      stars_7d  = count starred_at within [now-7d, now]   (needed by the level rule
                  and by run_decision PASS 2 extra_github_signals)
    write evidence_rows:
      metric_name = github_stars_24h
      metric_name = github_stars_7d
      metric_name = github_forks_total
      family = github
      source = github_backfill
      historical_safety = as_of_safe for starred_at, partial_as_of for repo totals
    return {entity_id: {"stars_24h": 1200.0, "stars_7d": 4200.0}}-shaped data
    for the runner's PASS 2, using actual counts from the completed job
    mark job completed
  On error:
    mark job failed with error text
```

- [ ] **Step 5: Run backfill tests**

Run:

```bash
python3 -m unittest tests/test_backfill_cache.py -v
```

Expected: `OK`.

- [ ] **Step 6: Update README**

Add this command:

~~~markdown
Run pending precise backfill jobs for a decision run:

```bash
python3 -m pipeline.decision.backfill \
  --db data/hero_radar.sqlite \
  --run-id decision_20260531
```

Backfill only runs on `backfill_jobs`; it does not scan every repo.
```
~~~

- [ ] **Step 7: Commit**

```bash
git add pipeline/decision/cache.py pipeline/decision/backfill.py tests/test_backfill_cache.py README.md
git commit -m "feat: add precise backfill cache"
```

---

### Task 7: Candidate API Endpoints

**Files:**

- Modify: `pipeline/server.py`
- Test: `tests/test_candidate_api.py`

- [ ] **Step 1: Write API tests**

Create `tests/test_candidate_api.py`:

```python
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from pipeline.decision.schema import init_decision_db


class CandidateApiShapeTest(unittest.TestCase):
    def test_candidate_query_shape_from_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            conn = sqlite3.connect(db_path)
            init_decision_db(conn)
            conn.execute(
                """
                insert into entities(entity_id, canonical_entity, canonical_key, key_type, first_seen, aliases_json, source_item_ids_json)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                ("entity:repo", "owner/repo", "github:owner/repo", "github", "2026-05-31T00:00:00Z", json.dumps(["owner/repo"]), "[]"),
            )
            conn.execute(
                """
                insert into potential_candidates(entity_id, run_id, level, fired_families_json, first_trigger_at)
                values (?, ?, ?, ?, ?)
                """,
                ("entity:repo", "run-1", "potential", json.dumps(["github"]), "2026-05-31T00:00:00Z"),
            )
            conn.commit()

            rows = conn.execute(
                """
                select pc.entity_id, e.canonical_entity, pc.level, pc.fired_families_json
                from potential_candidates pc
                join entities e on e.entity_id = pc.entity_id
                where pc.run_id = ?
                """,
                ("run-1",),
            ).fetchall()

            payload = [
                {
                    "entity_id": row[0],
                    "canonical_entity": row[1],
                    "level": row[2],
                    "fired_families": json.loads(row[3]),
                }
                for row in rows
            ]

            self.assertEqual(payload[0]["canonical_entity"], "owner/repo")
            self.assertEqual(payload[0]["level"], "potential")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run API shape test**

Run:

```bash
python3 -m unittest tests/test_candidate_api.py -v
```

Expected: `OK`. This test validates the SQL shape before wiring HTTP.

- [ ] **Step 3: Add candidate read helpers to `pipeline/server.py`**

Modify `pipeline/server.py`:

```text
Add DB_PATH = ROOT / "data" / "hero_radar.sqlite".

Add query_latest_decision_run(conn):
  Prefer the latest status='ok' run; fall back to the latest 'running' only if no
  'ok' run exists (do not surface a half-written run when a completed one is available).

Add query_candidates(conn, run_id):
  Return potential_candidates joined to entities plus edge_watch_candidates.

Add query_evidence(conn, entity_id, run_id):
  Return evidence_rows for the entity and run.

Add query_entity(conn, entity_id, run_id):
  Return entity, candidate status, evidence, and backfill jobs.

entity_id is the STABLE id minted by Stage A: `entity:<sha1-16>` (one colon, no
slash) -- NOT the canonical_key like `github:owner/repo`. Endpoints look up by this
entity_id. Accept canonical_key as an alternate lookup if convenient, but the
canonical handle in URLs is the `entity:<hash>` form so path parsing is unambiguous.
```

- [ ] **Step 4: Add HTTP endpoints to `pipeline/server.py`**

Add these routes in `do_GET`:

```text
GET /api/candidates
  Empty-data response shape:
    {
      "run_id": "decision_20260531",
      "candidates": [],
      "edge_watch": []
    }

GET /api/evidence?entity_id=entity:3f9a1c2b7d4e5a60
  (entity_id is URL-encoded; the colon is fine, there is no slash)
  Empty-data response shape:
    {
      "run_id": "decision_20260531",
      "entity_id": "entity:3f9a1c2b7d4e5a60",
      "evidence": []
    }

GET /api/entity/entity:3f9a1c2b7d4e5a60
  Empty-data response shape:
    {
      "run_id": "decision_20260531",
      "entity": {"entity_id": "entity:3f9a1c2b7d4e5a60"},
      "candidate": null,
      "edge_watch": null,
      "backfill_jobs": [],
      "evidence": []
    }
```

Use `urllib.parse.urlparse` and `urllib.parse.parse_qs`; do not add a web framework.

CORS: the React dev server (vite, port 5173) fetches the API cross-origin (port
8787), so the browser will BLOCK responses without CORS headers. Add to every
`/api/*` response: `Access-Control-Allow-Origin: *` (dev) and handle `OPTIONS`
preflight with 204 + the same header. (Alternative: a vite dev proxy; pick one.)

- [ ] **Step 5: Run API tests and syntax check**

Run:

```bash
python3 -m unittest tests/test_candidate_api.py -v
python3 -m py_compile pipeline/server.py
```

Expected: `OK` and no `py_compile` output.

- [ ] **Step 6: Manual API smoke**

Run:

```bash
python3 pipeline/server.py --port 8787
```

In another shell:

```bash
curl -s http://127.0.0.1:8787/api/candidates | python3 -m json.tool | head -40
```

Expected: JSON with `run_id`, `candidates`, and `edge_watch`. Stop the server with `Ctrl-C`.

- [ ] **Step 7: Commit**

```bash
git add pipeline/server.py tests/test_candidate_api.py
git commit -m "feat: expose candidate pool api"
```

---

### Task 8: Minimal React Shell With Candidate Pool

**Files:**

- Create: `web/package.json`
- Create: `web/index.html`
- Create: `web/src/main.jsx`
- Create: `web/src/App.jsx`
- Create: `web/src/styles.css`
- Modify: `.gitignore`
- Modify: `README.md`

- [ ] **Step 1: Add ignored web build outputs**

Append to `.gitignore`:

```gitignore
web/node_modules/
web/dist/
```

- [ ] **Step 2: Create `web/package.json`**

```json
{
  "scripts": {
    "dev": "vite --host 127.0.0.1 --port 5173",
    "build": "vite build",
    "preview": "vite preview --host 127.0.0.1 --port 4173"
  },
  "dependencies": {
    "@vitejs/plugin-react": "^5.0.0",
    "vite": "^7.0.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0"
  },
  "devDependencies": {}
}
```

- [ ] **Step 3: Create the HTML entry**

Create `web/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Hero Radar</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
```

- [ ] **Step 4: Create React entry**

Create `web/src/main.jsx`:

```jsx
import React from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';
import App from './App.jsx';

createRoot(document.getElementById('root')).render(<App />);
```

- [ ] **Step 5: Create the Candidate Pool UI**

Create `web/src/App.jsx`:

```jsx
import React, { useEffect, useMemo, useState } from 'react';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8787';

function levelLabel(level) {
  if (level === 'high_potential') return 'High Potential';
  if (level === 'potential') return 'Potential';
  if (level === 'edge_watch') return 'Edge Watch';
  return level || 'Unknown';
}

function App() {
  const [view, setView] = useState('daily');
  const [payload, setPayload] = useState({ run_id: '', candidates: [], edge_watch: [] });
  const [levelFilter, setLevelFilter] = useState('all');
  const [error, setError] = useState('');

  useEffect(() => {
    fetch(`${API_BASE}/api/candidates`)
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((data) => setPayload(data))
      .catch((err) => setError(String(err.message || err)));
  }, []);

  const candidateRows = useMemo(() => {
    const base = [
      ...(payload.candidates || []).map((row) => ({ ...row, pool_type: row.level })),
      ...(payload.edge_watch || []).map((row) => ({ ...row, level: 'edge_watch', pool_type: 'edge_watch' })),
    ];
    if (levelFilter === 'all') return base;
    return base.filter((row) => row.level === levelFilter);
  }, [payload, levelFilter]);

  return (
    <main className="app-shell">
      <aside className="rail">
        <div className="brand">HR</div>
        {/* Decided nav (spec §10): Explore | Feed | Sources | Settings.
            This slice only wires Feed; the rest are disabled placeholders. */}
        <button className="nav-button" disabled title="Layer 3, not in this slice">Explore</button>
        <button className="nav-button active">Feed</button>
        <button className="nav-button" disabled title="raw source dashboard">Sources</button>
        <button className="nav-button" disabled>Settings</button>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <h1>Hero Radar</h1>
            <p>Run {payload.run_id || 'not loaded'}</p>
          </div>
        </header>

        <div className="inner-tabs">
          <button className={view === 'daily' ? 'active' : ''} onClick={() => setView('daily')}>
            Daily Feed
          </button>
          <button className={view === 'pool' ? 'active' : ''} onClick={() => setView('pool')}>
            Candidate Pool
          </button>
        </div>

        {error ? <div className="error">Failed to load candidates: {error}</div> : null}

        {view === 'daily' ? (
          <section className="empty-state">
            <h2>Daily Feed is not built in this slice.</h2>
            <p>Layer 2 will select cards for today_focus, secondary, backlog, and suppress. Use Candidate Pool to inspect the Layer 1 output.</p>
          </section>
        ) : (
          <section className="panel">
            <div className="panel-head">
              <div>
                <h2>Candidate Pool</h2>
                <p>Transparent pre-Layer2 output: Potential, High Potential, and Edge Watch.</p>
              </div>
              <select value={levelFilter} onChange={(event) => setLevelFilter(event.target.value)}>
                <option value="all">All levels</option>
                <option value="high_potential">High Potential</option>
                <option value="potential">Potential</option>
                <option value="edge_watch">Edge Watch</option>
              </select>
            </div>

            <table>
              <thead>
                <tr>
                  <th>Entity</th>
                  <th>Level</th>
                  <th>Signals</th>
                  <th>First trigger</th>
                </tr>
              </thead>
              <tbody>
                {candidateRows.map((row) => (
                  <tr key={`${row.pool_type}:${row.entity_id}`}>
                    <td>
                      <strong>{row.canonical_entity || row.entity_id}</strong>
                      <code>{row.entity_id}</code>
                    </td>
                    <td><span className={`badge ${row.level}`}>{levelLabel(row.level)}</span></td>
                    <td>{(row.fired_families || row.reasons || []).join(', ')}</td>
                    <td>{row.first_trigger_at || ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}
      </section>
    </main>
  );
}

export default App;
```

- [ ] **Step 6: Create minimal Notion-like styling**

Create `web/src/styles.css`:

```css
:root {
  color: #1f1f1f;
  background: #f7f6f2;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
}

button, select {
  font: inherit;
}

.app-shell {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
}

.rail {
  border-right: 1px solid #e2dfd8;
  background: #fbfaf7;
  padding: 14px 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.brand {
  width: 36px;
  height: 36px;
  border: 1px solid #d8d4ca;
  border-radius: 8px;
  display: grid;
  place-items: center;
  font-weight: 700;
  margin: 0 auto 12px;
}

.nav-button {
  border: 1px solid transparent;
  background: transparent;
  border-radius: 8px;
  padding: 8px 6px;
  cursor: pointer;
}

.nav-button.active {
  border-color: #d8d4ca;
  background: #fff;
}

.workspace {
  padding: 24px 32px;
}

.topbar {
  display: flex;
  justify-content: space-between;
  border-bottom: 1px solid #e2dfd8;
  padding-bottom: 16px;
}

h1, h2, p {
  margin: 0;
}

p {
  color: #6f6a60;
}

.inner-tabs {
  margin: 18px 0;
  display: inline-flex;
  border: 1px solid #d8d4ca;
  border-radius: 8px;
  background: #fff;
  padding: 3px;
}

.inner-tabs button {
  border: 0;
  background: transparent;
  border-radius: 6px;
  padding: 7px 12px;
  cursor: pointer;
}

.inner-tabs button.active {
  background: #ece9e1;
}

.panel, .empty-state, .error {
  background: #fff;
  border: 1px solid #e2dfd8;
  border-radius: 8px;
  padding: 18px;
}

.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: start;
  gap: 16px;
  margin-bottom: 14px;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}

th, td {
  border-bottom: 1px solid #ece9e1;
  padding: 10px 8px;
  text-align: left;
  vertical-align: top;
}

td code {
  display: block;
  margin-top: 4px;
  color: #8a8377;
  font-size: 12px;
}

.badge {
  display: inline-block;
  border-radius: 999px;
  padding: 3px 8px;
  background: #ece9e1;
}

.badge.high_potential {
  background: #d8eadc;
}

.badge.potential {
  background: #e5edf8;
}

.badge.edge_watch {
  background: #f2ead6;
}

.error {
  margin-bottom: 12px;
  border-color: #e4b8b8;
  color: #8a2e2e;
}
```

- [ ] **Step 7: Install and build web shell**

Run:

```bash
cd web
npm install
npm run build
```

Expected: Vite build succeeds and writes `web/dist/`.

- [ ] **Step 8: Update README**

Add:

~~~markdown
## Candidate Pool Web Shell

Start the local API:

```bash
python3 pipeline/server.py --port 8787
```

Start the React shell:

```bash
cd web
npm install
VITE_API_BASE=http://127.0.0.1:8787 npm run dev
```

Open `http://127.0.0.1:5173/`. The `Daily Feed` internal tab is an empty state in
this slice. `Candidate Pool` reads `/api/candidates` and shows the pre-Layer2
candidate universe.
```
~~~

- [ ] **Step 9: Commit**

```bash
git add .gitignore README.md web/package.json web/index.html web/src/main.jsx web/src/App.jsx web/src/styles.css
git commit -m "feat: add candidate pool web shell"
```

---

### Task 9: HF Card GitHub-Link Enrichment (real-data yield for the HF rule)

Makes the huggingface rule actually fire on real data by attaching HF resources to
the github entity. Independent of the decision tasks (their tests inject the link),
so this can run in parallel.

**Files:**

- Modify: `pipeline/run_pipeline.py` (only `collect_huggingface`)
- Test: `tests/test_hf_card_link.py`

- [ ] **Step 1: Write the extractor test**

Test a pure helper `extract_github_repo_from_card(text: str) -> str | None`:
- returns `https://github.com/owner/repo` from a README containing that URL,
- ignores `github.com/owner/repo/issues/3` extra path (returns the owner/repo root),
- returns `None` when there is no github link.

- [ ] **Step 2: Implement bounded enrichment**

```text
extract_github_repo_from_card(text): regex github.com/{owner}/{repo}, normalize to
  the repo root, return URL or None.

In collect_huggingface, AFTER building the resource list, enrich only the top
N (config huggingface.card_enrich_limit, default 50) resources per resource type
by trendingScore:
  fetch https://huggingface.co/{id}/raw/main/README.md  (best-effort, short timeout)
  on success, extract a github link; if found, set metadata["repository"] = link.
  Cache nothing here (it is the daily collection run); skip failures silently.
Do NOT change any existing metadata field or the dashboard contract; only ADD
metadata["repository"] when a link is found. Keep total extra fetches <= ~150/run.
```

Stage A already reads `metadata["repository"]`, so enriched HF resources now cluster
with their github entity and the huggingface rule (section Task 3) can fire.

- [ ] **Step 3: Run tests**

```bash
python3 -m unittest tests/test_hf_card_link.py -v
```

Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add pipeline/run_pipeline.py tests/test_hf_card_link.py
git commit -m "feat: enrich HF items with card github link"
```

---

## End-To-End Verification

- [ ] **Step 1: Run all Python tests**

```bash
python3 -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 2: Run source collection export-only smoke**

```bash
python3 pipeline/run_pipeline.py --export-only
```

Expected: existing dashboard export still succeeds.

- [ ] **Step 3: Run deterministic decision pipeline**

Board-only pass (offline, no GitHub API):

```bash
python3 -m pipeline.decision.run_decision \
  --db data/hero_radar.sqlite \
  --export-json data/exports/candidates_latest.json
```

Full two-pass with precise off-board backfill (needs `GITHUB_TOKEN`):

```bash
GITHUB_TOKEN=$GITHUB_TOKEN python3 -m pipeline.decision.run_decision \
  --db data/hero_radar.sqlite \
  --export-json data/exports/candidates_latest.json \
  --backfill
```

Expected: nonzero `entities`; `potential_candidates`, `edge_watch_candidates`, and
`backfill_jobs` counts printed. With `--backfill`, off-board github_search movers
that pass precise stars_24h thresholds are promoted into the pool.

- [ ] **Step 4: Confirm no LLM is invoked**

This slice is fully deterministic (backfill is GitHub REST, no LLM). There is no X
classifier and no LLM provider call in the pipeline or in automated tests.

- [ ] **Step 5: Build web shell**

```bash
cd web
npm install
npm run build
```

Expected: Vite build succeeds.

---

## Self-Review Checklist

- [ ] No Layer 2 Daily Feed selection is implemented in this plan.
- [ ] No bounded Kimi deepdive is implemented in this plan.
- [ ] No chatbot or `/api/chat` work is implemented in this plan.
- [ ] No cron/job runner is implemented in this plan.
- [ ] No LLM is used in this slice (X classifier moved to the Layer-2 plan).
- [ ] Rules match REAL source names (`github_movers_trending_repos`, `github_movers_repofomo`, etc.); no `github_movers` / `source_kind`.
- [ ] entity_id is reused across runs by key overlap (stable for per-account state).
- [ ] Only entities referenced by candidates/edge_watch/evidence/mentions are persisted.
- [ ] Backfill only runs on `backfill_jobs`, is capped at `backfill_max_jobs`, and enqueues no npm jobs.
- [ ] Candidate Pool remains visible even when Daily Feed is empty.
- [ ] Backfill external calls have cache keys before real API use. HF card enrichment is a bounded daily collection add-on and is capped by `huggingface.card_enrich_limit`.
