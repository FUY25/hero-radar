# Layer 2 Kimi Feed Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Layer 2 Daily Feed: presentation grouping, deterministic scheduling, Kimi Edge Watch Scout, Kimi scoring, deterministic aggregation, bounded Kimi deepdives, Feed API, and designed React Feed UI.

**Architecture:** Layer 2 is additive over the current decision database. It reads the latest successful pre-Layer2 decision run, groups candidate entities for presentation, schedules bounded model work, writes separate Layer 2 tables, and exposes `/api/feed` without mutating deterministic Candidate Pool levels.

**Tech Stack:** Python 3 stdlib (`sqlite3`, `unittest`, `urllib.request`, `argparse`), existing `pipeline.decision.llm_provider` abstraction, Kimi/Moonshot OpenAI-compatible API, SQLite `api_cache`/`llm_cache`, React 19 + Vite, Node `node:test`, existing local HTTP server.

---

## Context

- Spec: `docs/superpowers/specs/2026-05-31-layer2-evidence-feed-design.md`
- Existing decision schema: `pipeline/decision/schema.py`
- Existing candidate context: `pipeline/decision/candidate_context.py`
- Existing LLM provider: `pipeline/decision/llm_provider.py`
- Existing daily runner: `pipeline/run_daily.py`
- Existing server: `pipeline/server.py`
- Existing React model/UI: `web/src/dashboardModel.js`, `web/src/App.jsx`, `web/src/styles.css`
- Existing tests: `tests/test_decision_schema.py`, `tests/test_llm_provider.py`, `tests/test_daily_pipeline.py`, `tests/test_dashboard_data_api.py`, `web/src/dashboardModel.test.mjs`
- Kimi web search docs checked on 2026-05-31: `https://platform.kimi.ai/docs/guide/use-web-search`. The Chat Completions built-in tool is declared as `{"type": "builtin_function", "function": {"name": "$web_search"}}`; pricing docs list `$0.005` per triggered web-search call, plus model token billing.
- Taste skill source installed from `https://www.tasteskill.dev/`, skill `design-taste-frontend`.

## File Structure

- Create `pipeline/decision/layer2_models.py`  
  Dataclasses and JSON/schema validators for Layer 2 group, scout, score, feed item, and deepdive payloads.

- Create `pipeline/decision/layer2_grouping.py`  
  Presentation-only candidate grouping/dedupe. No permanent alias writes.

- Create `pipeline/decision/layer2_context.py`  
  Context assembly for scout/scoring/deepdive, wrapping existing `context_bundle_for_entity()` and evidence/source item reads.

- Create `pipeline/decision/layer2_scheduler.py`  
  Deterministic eligibility, evidence hash, cache lookup, budget ordering, and pending decisions. No semantic rejection.

- Create `pipeline/decision/kimi_provider.py`  
  Kimi/Moonshot OpenAI-compatible provider and Kimi web search request payload support.

- Create `pipeline/decision/layer2_scout.py`  
  Kimi Edge Watch Scout prompt, output validation, cache keying, and persistence.

- Create `pipeline/decision/layer2_scoring.py`  
  Kimi multi-axis scoring prompt, output validation, deterministic score aggregation, cache keying, and persistence.

- Create `pipeline/decision/layer2_deepdive.py`  
  Bounded Kimi deepdive loop, tool registry, limits, cache keying, and persistence.

- Create `pipeline/decision/run_layer2_feed.py`  
  CLI entrypoint to run grouping, scheduler, scout, scoring, deepdive selection, and persistence.

- Modify `pipeline/decision/schema.py`  
  Add additive Layer 2 tables and allow reset of run-scoped Layer 2 tables.

- Modify `pipeline/run_daily.py`  
  Add a `--run-layer2` controlled Layer 2 stage after decision with bounded defaults.

- Modify `pipeline/server.py`  
  Add `/api/feed`, `/api/feed/feedback`, dashboard compact feed embedding, and run command Layer 2 flags.

- Modify `pipeline/config.json`  
  Add `layer2` config with Kimi model names and budgets.

- Modify `web/package.json`, `web/package-lock.json`  
  Add `@phosphor-icons/react` for Feed UI icons if implementation chooses icons in React components.

- Modify `web/src/dashboardModel.js`  
  Normalize Feed API payload and expose Feed view helpers.

- Modify `web/src/App.jsx`  
  Replace locked Daily Feed placeholder with Feed UI.

- Modify `web/src/styles.css`  
  Add signal-card / scored-feed / loading / detail styles, constrained by existing shell.

- Create tests:
  - `tests/test_kimi_provider.py`
  - `tests/test_layer2_grouping.py`
  - `tests/test_layer2_context.py`
  - `tests/test_layer2_scheduler.py`
  - `tests/test_layer2_scout.py`
  - `tests/test_layer2_scoring.py`
  - `tests/test_layer2_deepdive.py`
  - `tests/test_run_layer2_feed.py`
  - `tests/test_feed_api.py`
  - `web/src/feedModel.test.mjs`

## Subagent Ownership

- Worker A, schema/API/runner: `pipeline/decision/schema.py`, `pipeline/run_daily.py`, `pipeline/server.py`, `tests/test_decision_schema.py`, `tests/test_daily_pipeline.py`, `tests/test_feed_api.py`.
- Worker B, provider/context/grouping/scheduler: `pipeline/decision/kimi_provider.py`, `pipeline/decision/layer2_context.py`, `pipeline/decision/layer2_grouping.py`, `pipeline/decision/layer2_scheduler.py`, matching tests.
- Worker C, scout/scoring/deepdive: `pipeline/decision/layer2_scout.py`, `pipeline/decision/layer2_scoring.py`, `pipeline/decision/layer2_deepdive.py`, matching tests.
- Worker D, React Feed UI: `web/src/dashboardModel.js`, `web/src/App.jsx`, `web/src/styles.css`, `web/src/feedModel.test.mjs`, `web/package.json`, `web/package-lock.json`.

All workers must work in `/Users/fuyuming/Documents/Hero radar` on `main`. Do not use `.claude/worktrees/pre-layer2-decision-pipeline`.

## Task 1: Layer 2 Schema

**Files:**
- Modify: `pipeline/decision/schema.py`
- Test: `tests/test_decision_schema.py`

- [ ] **Step 1: Write the failing schema test**

Append to `tests/test_decision_schema.py`:

```python
    def test_init_creates_layer2_feed_tables(self):
        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)

        names = {
            row[0]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }

        self.assertIn("l2_feed_runs", names)
        self.assertIn("l2_candidate_groups", names)
        self.assertIn("l2_scout_results", names)
        self.assertIn("l2_scores", names)
        self.assertIn("deepdive_reports", names)
        self.assertIn("l2_feed_items", names)
        self.assertIn("feed_feedback", names)

    def test_reset_stage_allows_layer2_run_scoped_tables(self):
        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        conn.execute(
            """
            insert into l2_feed_runs(feed_run_id, decision_run_id, started_at, status, config_hash, model_profile_json, note)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            ("l2-run", "decision-run", "2026-05-31T00:00:00Z", "ok", "hash", "{}", ""),
        )
        conn.execute(
            """
            insert into l2_candidate_groups(group_id, feed_run_id, canonical_entity_id, canonical_name, canonical_key, canonical_link, member_entity_ids_json, level, source_families_json, evidence_hash, grouping_reason_json, context_json)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "group:one",
                "l2-run",
                "entity:one",
                "One",
                "github:owner/repo",
                "https://github.com/owner/repo",
                '["entity:one"]',
                "potential",
                '["github"]',
                "evidence-hash",
                "{}",
                "{}",
            ),
        )
        conn.execute(
            """
            insert into l2_scores(feed_run_id, group_id, l2_score, axes_json, primary_reason, topic_tags_json, rationale_short, caveats_json, provider, model, prompt_version, cache_key)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("l2-run", "group:one", 80, "{}", "Workflow Shift", "[]", "Good", "[]", "kimi", "kimi-k2.5", "v1", "cache"),
        )

        reset_decision_stage(
            conn,
            run_id="l2-run",
            tables=["l2_candidate_groups", "l2_scores"],
        )

        self.assertEqual(conn.execute("select count(*) from l2_candidate_groups").fetchone()[0], 0)
        self.assertEqual(conn.execute("select count(*) from l2_scores").fetchone()[0], 0)
        self.assertEqual(conn.execute("select count(*) from l2_feed_runs").fetchone()[0], 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_decision_schema.py -q
```

Expected: FAIL because the Layer 2 tables do not exist and reset does not allow Layer 2 tables.

- [ ] **Step 3: Add Layer 2 tables**

In `pipeline/decision/schema.py`, extend `DECISION_SCHEMA_SQL`:

```python
create table if not exists l2_feed_runs (
    feed_run_id text primary key,
    decision_run_id text not null,
    started_at text not null,
    completed_at text,
    status text not null,
    config_hash text not null,
    model_profile_json text not null,
    note text
);

create table if not exists l2_candidate_groups (
    group_id text primary key,
    feed_run_id text not null,
    canonical_entity_id text not null,
    canonical_name text not null,
    canonical_key text not null,
    canonical_link text,
    member_entity_ids_json text not null,
    level text not null,
    source_families_json text not null,
    evidence_hash text not null,
    grouping_reason_json text not null,
    context_json text not null
);

create table if not exists l2_scout_results (
    id integer primary key autoincrement,
    feed_run_id text not null,
    group_id text not null,
    included_in_scoring integer not null,
    scout_score real not null,
    reason text not null,
    needed_context_json text not null,
    risk text not null,
    confidence real not null,
    provider text not null,
    model text not null,
    prompt_version text not null,
    cache_key text not null,
    unique(feed_run_id, group_id)
);

create table if not exists l2_scores (
    id integer primary key autoincrement,
    feed_run_id text not null,
    group_id text not null,
    l2_score real not null,
    axes_json text not null,
    primary_reason text not null,
    topic_tags_json text not null,
    rationale_short text not null,
    caveats_json text not null,
    provider text not null,
    model text not null,
    prompt_version text not null,
    cache_key text not null,
    unique(feed_run_id, group_id)
);

create table if not exists deepdive_reports (
    id integer primary key autoincrement,
    feed_run_id text not null,
    group_id text not null,
    status text not null,
    summary_json text not null,
    tool_trace_json text not null,
    provider text not null,
    model text not null,
    prompt_version text not null,
    cache_key text not null,
    created_at text not null,
    unique(feed_run_id, group_id)
);

create table if not exists l2_feed_items (
    id integer primary key autoincrement,
    feed_run_id text not null,
    group_id text not null,
    section text not null,
    rank integer not null,
    deepdive_status text not null,
    unique(feed_run_id, group_id, section)
);

create table if not exists feed_feedback (
    id integer primary key autoincrement,
    feed_run_id text not null,
    group_id text not null,
    vote text not null,
    created_at text not null,
    unique(feed_run_id, group_id)
);

create index if not exists idx_l2_groups_run on l2_candidate_groups(feed_run_id);
create index if not exists idx_l2_scores_run_score on l2_scores(feed_run_id, l2_score);
create index if not exists idx_l2_feed_items_run_section on l2_feed_items(feed_run_id, section, rank);
```

Extend the `allowed` set in `reset_decision_stage()`:

```python
        "l2_candidate_groups",
        "l2_scout_results",
        "l2_scores",
        "deepdive_reports",
        "l2_feed_items",
        "feed_feedback",
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python3 -m pytest tests/test_decision_schema.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/decision/schema.py tests/test_decision_schema.py
git commit -m "Add Layer 2 feed schema"
```

## Task 2: Kimi Provider

**Files:**
- Create: `pipeline/decision/kimi_provider.py`
- Modify: `pipeline/decision/llm_provider.py`
- Test: `tests/test_kimi_provider.py`

- [ ] **Step 1: Write the failing provider tests**

Create `tests/test_kimi_provider.py`:

```python
from __future__ import annotations

import json
import unittest
from unittest import mock


class FakeHttpResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class KimiProviderTest(unittest.TestCase):
    def test_kimi_provider_builds_openai_json_payload_without_secret_in_repr(self):
        from pipeline.decision.kimi_provider import KimiProvider

        provider = KimiProvider(
            api_key="secret-value",
            model="kimi-k2.5",
            base_url="https://api.moonshot.ai/v1",
        )
        payload = provider.build_payload(
            system_prompt="Score candidates as JSON.",
            user_payload={"candidate": "owner/repo"},
            temperature=0,
        )

        self.assertEqual(payload["model"], "kimi-k2.5")
        self.assertEqual(payload["response_format"]["type"], "json_object")
        self.assertEqual(payload["temperature"], 0)
        self.assertIn('"candidate": "owner/repo"', payload["messages"][1]["content"])
        self.assertNotIn("secret-value", repr(provider))

    def test_kimi_provider_reads_kimi_or_moonshot_env_key(self):
        from pipeline.decision.kimi_provider import KimiProvider

        with mock.patch.dict("os.environ", {"KIMI_API_KEY": "kimi-secret"}, clear=True):
            self.assertEqual(KimiProvider().api_key, "kimi-secret")
        with mock.patch.dict("os.environ", {"MOONSHOT_API_KEY": "moon-secret"}, clear=True):
            self.assertEqual(KimiProvider().api_key, "moon-secret")

    def test_kimi_provider_completes_json_and_retries_empty_content(self):
        from pipeline.decision.kimi_provider import KimiProvider

        calls = []

        def fake_urlopen(request, timeout):
            calls.append({"url": request.full_url, "timeout": timeout})
            if len(calls) == 1:
                return FakeHttpResponse({"choices": [{"message": {"content": ""}}]})
            return FakeHttpResponse(
                {"choices": [{"message": {"content": json.dumps({"ok": True})}}]}
            )

        provider = KimiProvider(api_key="secret", timeout=1, max_retries=1)
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = provider.complete_json(
                task="layer2_smoke",
                prompt_version="v1",
                input_payload={"hello": "world"},
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(calls), 2)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_kimi_provider.py -q
```

Expected: FAIL because `pipeline.decision.kimi_provider` does not exist.

- [ ] **Step 3: Implement provider**

Create `pipeline/decision/kimi_provider.py`:

```python
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


DEFAULT_KIMI_BASE_URL = "https://api.moonshot.ai/v1"
DEFAULT_KIMI_SCOUT_MODEL = "kimi-k2.5"
DEFAULT_KIMI_SCORING_MODEL = "kimi-k2.5"
DEFAULT_KIMI_DEEPDIVE_MODEL = "kimi-k2.6"


class KimiProvider:
    provider_name = "kimi"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: int = 90,
        max_retries: int = 2,
    ) -> None:
        self.api_key = (
            api_key
            or os.environ.get("KIMI_API_KEY", "")
            or os.environ.get("MOONSHOT_API_KEY", "")
        )
        self.model = model or os.environ.get("KIMI_MODEL", DEFAULT_KIMI_SCORING_MODEL)
        self.base_url = (
            base_url
            or os.environ.get("KIMI_BASE_URL", DEFAULT_KIMI_BASE_URL)
        ).rstrip("/")
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))

    def __repr__(self) -> str:
        return (
            f"KimiProvider(model={self.model!r}, base_url={self.base_url!r}, "
            f"api_key_configured={bool(self.api_key)})"
        )

    def build_payload(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        temperature: float = 0,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        system_content = system_prompt.strip() or "Return strict JSON only."
        if "json" not in system_content.lower():
            system_content = f"{system_content}\nReturn strict JSON only."
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_content},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False, sort_keys=True),
                },
            ],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
        return payload

    def complete_json(
        self,
        *,
        task: str,
        prompt_version: str,
        input_payload: dict[str, Any],
        system_prompt: str = "",
    ) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("KIMI_API_KEY or MOONSHOT_API_KEY is not configured")
        payload = self.build_payload(
            system_prompt=system_prompt,
            user_payload=input_payload,
            temperature=0,
        )
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        last_error: BaseException | None = None
        for _attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                content = body["choices"][0]["message"]["content"]
                if not content:
                    raise RuntimeError("Kimi returned empty JSON content")
                return json.loads(content)
            except (TimeoutError, urllib.error.URLError, RuntimeError, ValueError, KeyError, IndexError) as exc:
                last_error = exc
        raise last_error or RuntimeError("Kimi request failed")
```

No change is required in `llm_provider.py` unless a shared `OpenAICompatibleProvider` is extracted during implementation. If extracting, keep the existing DeepSeek tests passing unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python3 -m pytest tests/test_kimi_provider.py tests/test_llm_provider.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/decision/kimi_provider.py pipeline/decision/llm_provider.py tests/test_kimi_provider.py
git commit -m "Add Kimi provider"
```

## Task 3: Presentation Grouping

**Files:**
- Create: `pipeline/decision/layer2_models.py`
- Create: `pipeline/decision/layer2_grouping.py`
- Test: `tests/test_layer2_grouping.py`

- [ ] **Step 1: Write the failing grouping tests**

Create `tests/test_layer2_grouping.py`:

```python
from __future__ import annotations

import sqlite3
import unittest

from pipeline.decision.schema import init_decision_db, to_json


class Layer2GroupingTest(unittest.TestCase):
    def make_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        conn.executescript(
            """
            create table items (
                id integer primary key,
                source text not null,
                name text not null,
                url text,
                description text,
                metadata_json text not null,
                raw_json text not null
            );
            """
        )
        return conn

    def insert_entity(self, conn, entity_id, name, key, item_ids):
        key_type = key.split(":", 1)[0]
        conn.execute(
            """
            insert into entities(entity_id, canonical_entity, canonical_key, key_type, first_seen, aliases_json, source_item_ids_json)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (entity_id, name, key, key_type, "2026-05-31T00:00:00Z", "[]", to_json(item_ids)),
        )

    def test_groups_same_canonical_link_without_alias_write(self):
        from pipeline.decision.layer2_grouping import build_candidate_groups

        conn = self.make_conn()
        self.insert_entity(conn, "entity:repo", "owner/repo", "github:owner/repo", [])
        self.insert_entity(conn, "entity:npm", "repo", "npm:repo", [])
        conn.execute(
            """
            insert into alias_links(entity_id, source, external_id, alias, confidence, origin, approved, created_at)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("entity:npm", "resolver", "npm:repo", "github:owner/repo", "high", "resolver", 1, "2026-05-31T00:00:00Z"),
        )
        conn.execute(
            "insert into potential_candidates(entity_id, run_id, level, fired_families_json, first_trigger_at) values (?, ?, ?, ?, ?)",
            ("entity:repo", "decision-run", "potential", '["github"]', "2026-05-31T00:00:00Z"),
        )
        conn.execute(
            "insert into potential_candidates(entity_id, run_id, level, fired_families_json, first_trigger_at) values (?, ?, ?, ?, ?)",
            ("entity:npm", "decision-run", "potential", '["package_family"]', "2026-05-31T00:05:00Z"),
        )

        groups = build_candidate_groups(conn, decision_run_id="decision-run")

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].member_entity_ids, ["entity:repo", "entity:npm"])
        self.assertEqual(groups[0].canonical_link, "https://github.com/owner/repo")
        self.assertEqual(
            conn.execute("select count(*) from alias_links").fetchone()[0],
            1,
        )

    def test_keeps_unrelated_name_matches_separate_without_strong_key(self):
        from pipeline.decision.layer2_grouping import build_candidate_groups

        conn = self.make_conn()
        self.insert_entity(conn, "entity:a", "Agent", "name:agent", [])
        self.insert_entity(conn, "entity:b", "Agent", "domain:example.com", [])
        for entity_id in ["entity:a", "entity:b"]:
            conn.execute(
                "insert into edge_watch_candidates(entity_id, run_id, reason_json, source_refs_json, status) values (?, ?, ?, ?, ?)",
                (entity_id, "decision-run", "[]", "[]", "open"),
            )

        groups = build_candidate_groups(conn, decision_run_id="decision-run")

        self.assertEqual(len(groups), 2)
        self.assertEqual(sorted(group.canonical_entity_id for group in groups), ["entity:a", "entity:b"])
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_layer2_grouping.py -q
```

Expected: FAIL because `layer2_grouping` does not exist.

- [ ] **Step 3: Implement models and grouping**

Create `pipeline/decision/layer2_models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


LEVEL_RANK = {"edge_watch": 0, "watch": 1, "potential": 2, "high_potential": 3}


@dataclass(frozen=True)
class CandidateGroup:
    group_id: str
    canonical_entity_id: str
    canonical_name: str
    canonical_key: str
    canonical_link: str
    member_entity_ids: list[str]
    level: str
    source_families: list[str]
    evidence_hash: str = ""
    grouping_reason: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
```

Create `pipeline/decision/layer2_grouping.py`:

```python
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from typing import Any

from pipeline.decision.candidate_context import context_bundle_for_entity, key_to_url
from pipeline.decision.layer2_models import CandidateGroup, LEVEL_RANK
from pipeline.decision.schema import to_json


def build_candidate_groups(conn: sqlite3.Connection, *, decision_run_id: str) -> list[CandidateGroup]:
    rows = _candidate_rows(conn, decision_run_id)
    groups_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups_by_key[_presentation_key(conn, row)].append(row)

    groups: list[CandidateGroup] = []
    for key, members in sorted(groups_by_key.items()):
        canonical = sorted(
            members,
            key=lambda row: (-LEVEL_RANK.get(row["level"], 0), row["canonical_entity"].lower(), row["entity_id"]),
        )[0]
        member_ids = sorted(row["entity_id"] for row in members)
        source_families = sorted({family for row in members for family in row.get("source_families", [])})
        canonical_link = key_to_url(canonical["canonical_key"]) or _approved_alias_link(conn, canonical["entity_id"]) or ""
        if not canonical_link:
            for row in members:
                canonical_link = key_to_url(row["canonical_key"]) or _approved_alias_link(conn, row["entity_id"]) or ""
                if canonical_link:
                    break
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


def persist_candidate_groups(conn: sqlite3.Connection, *, feed_run_id: str, groups: list[CandidateGroup]) -> None:
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


def _candidate_rows(conn: sqlite3.Connection, decision_run_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in conn.execute(
        """
        select pc.entity_id, e.canonical_entity, e.canonical_key, pc.level, pc.fired_families_json
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
            }
        )
    for row in conn.execute(
        """
        select ew.entity_id, e.canonical_entity, e.canonical_key
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
            }
        )
    return rows


def _presentation_key(conn: sqlite3.Connection, row: dict[str, Any]) -> str:
    key = str(row.get("canonical_key") or "")
    link_key = _link_group_key(key)
    if link_key:
        return link_key
    alias_link = _approved_alias_link(conn, row["entity_id"])
    if alias_link:
        return f"link:{alias_link.lower().rstrip('/')}"
    return f"entity:{row['entity_id']}"


def _link_group_key(key: str) -> str:
    if key.startswith("github:"):
        return f"github:{key.split(':', 1)[1].lower().strip('/')}"
    if key.startswith("npm:"):
        return f"npm:{key.split(':', 1)[1].lower().strip()}"
    if key.startswith("domain:"):
        domain = key.split(":", 1)[1].lower().strip("/")
        if _is_content_domain(domain):
            return ""
        return f"domain:{domain}"
    return ""


def _approved_alias_link(conn: sqlite3.Connection, entity_id: str) -> str:
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
    return key_to_url(row[0]) if row else ""


def _is_content_domain(domain: str) -> bool:
    return (
        domain.startswith("blog.")
        or domain.startswith("news.")
        or domain.startswith("newsroom.")
        or domain in {"medium.com", "substack.com", "blogspot.com"}
        or domain.endswith(".medium.com")
        or domain.endswith(".substack.com")
    )


def _json_loads(value: Any, default: Any) -> Any:
    import json

    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python3 -m pytest tests/test_layer2_grouping.py tests/test_candidate_context.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/decision/layer2_models.py pipeline/decision/layer2_grouping.py tests/test_layer2_grouping.py
git commit -m "Add Layer 2 presentation grouping"
```

## Task 4: Layer 2 Context And Scheduler

**Files:**
- Create: `pipeline/decision/layer2_context.py`
- Create: `pipeline/decision/layer2_scheduler.py`
- Test: `tests/test_layer2_context.py`
- Test: `tests/test_layer2_scheduler.py`

- [ ] **Step 1: Write failing context tests**

Create `tests/test_layer2_context.py`:

```python
from __future__ import annotations

import sqlite3
import unittest

from pipeline.decision.layer2_models import CandidateGroup
from pipeline.decision.schema import init_decision_db, to_json


class Layer2ContextTest(unittest.TestCase):
    def test_context_includes_group_members_evidence_and_hash(self):
        from pipeline.decision.layer2_context import assemble_group_context

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        conn.executescript(
            """
            create table items (
                id integer primary key,
                source text not null,
                name text not null,
                url text,
                description text,
                metadata_json text not null,
                raw_json text not null
            );
            """
        )
        conn.execute(
            "insert into items(id, source, name, url, description, metadata_json, raw_json) values (?, ?, ?, ?, ?, ?, ?)",
            (1, "github_trending", "owner/repo", "https://github.com/owner/repo", "Repo description", "{}", "{}"),
        )
        conn.execute(
            """
            insert into entities(entity_id, canonical_entity, canonical_key, key_type, first_seen, aliases_json, source_item_ids_json)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            ("entity:repo", "owner/repo", "github:owner/repo", "github", "2026-05-31T00:00:00Z", "[]", "[1]"),
        )
        conn.execute(
            """
            insert into evidence_rows(entity_id, canonical_entity, alias, source, event_at, metric_name, metric_value, family, rule_id, rule_version, signal_label, historical_safety, note, raw_url_or_ref, run_id)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("entity:repo", "owner/repo", "owner/repo", "github_trending", "2026-05-31T00:00:00Z", "stars_today", "321", "github", "github_daily", "rules-v1", "potential", "snapshot_only", "passed", "item:1", "decision-run"),
        )
        group = CandidateGroup(
            group_id="group:repo",
            canonical_entity_id="entity:repo",
            canonical_name="owner/repo",
            canonical_key="github:owner/repo",
            canonical_link="https://github.com/owner/repo",
            member_entity_ids=["entity:repo"],
            level="potential",
            source_families=["github"],
        )

        enriched = assemble_group_context(conn, decision_run_id="decision-run", group=group)

        self.assertEqual(enriched.context["canonical_name"], "owner/repo")
        self.assertEqual(enriched.context["members"][0]["entity_id"], "entity:repo")
        self.assertEqual(enriched.context["evidence_rows"][0]["metric_name"], "stars_today")
        self.assertTrue(enriched.evidence_hash)
```

- [ ] **Step 2: Write failing scheduler tests**

Create `tests/test_layer2_scheduler.py`:

```python
from __future__ import annotations

import unittest

from pipeline.decision.layer2_models import CandidateGroup


class Layer2SchedulerTest(unittest.TestCase):
    def group(self, group_id: str, level: str) -> CandidateGroup:
        return CandidateGroup(
            group_id=group_id,
            canonical_entity_id=f"entity:{group_id}",
            canonical_name=group_id,
            canonical_key=f"name:{group_id}",
            canonical_link="",
            member_entity_ids=[f"entity:{group_id}"],
            level=level,
            source_families=["hn"],
            evidence_hash=f"hash-{group_id}",
        )

    def test_potential_and_high_go_to_scoring_without_scout(self):
        from pipeline.decision.layer2_scheduler import schedule_layer2_work

        schedule = schedule_layer2_work(
            [self.group("a", "potential"), self.group("b", "high_potential")],
            previous_hashes={},
            max_edge_watch_scout=50,
            max_scored_candidates=150,
        )

        self.assertEqual([group.group_id for group in schedule.score_now], ["b", "a"])
        self.assertEqual(schedule.scout_edge_watch, [])

    def test_edge_watch_goes_to_scout_not_semantic_rejection(self):
        from pipeline.decision.layer2_scheduler import schedule_layer2_work

        schedule = schedule_layer2_work(
            [self.group("edge", "edge_watch")],
            previous_hashes={},
            max_edge_watch_scout=50,
            max_scored_candidates=150,
        )

        self.assertEqual([group.group_id for group in schedule.scout_edge_watch], ["edge"])
        self.assertEqual(schedule.skipped, [])

    def test_same_evidence_hash_is_skipped_mechanically(self):
        from pipeline.decision.layer2_scheduler import schedule_layer2_work

        group = self.group("a", "potential")
        schedule = schedule_layer2_work(
            [group],
            previous_hashes={"group:a": "hash-a"},
            max_edge_watch_scout=50,
            max_scored_candidates=150,
        )

        self.assertEqual(schedule.score_now, [])
        self.assertEqual(schedule.skipped[0]["reason"], "unchanged_evidence_hash")
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_layer2_context.py tests/test_layer2_scheduler.py -q
```

Expected: FAIL because the modules do not exist.

- [ ] **Step 4: Implement context and scheduler**

Create `pipeline/decision/layer2_context.py`:

```python
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from typing import Any

from pipeline.decision.candidate_context import context_bundle_for_entity
from pipeline.decision.layer2_models import CandidateGroup
from pipeline.decision.schema import to_json
from pipeline.server import query_evidence


def assemble_group_context(
    conn: sqlite3.Connection,
    *,
    decision_run_id: str,
    group: CandidateGroup,
) -> CandidateGroup:
    members: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    for entity_id in group.member_entity_ids:
        bundle = context_bundle_for_entity(conn, entity_id=entity_id, run_id=decision_run_id)
        members.append({"entity_id": entity_id, **bundle})
        evidence_rows.extend(query_evidence(conn, entity_id, decision_run_id))
    evidence_rows.sort(key=lambda row: (row.get("event_at") or "", row.get("id") or 0), reverse=True)
    context = {
        "group_id": group.group_id,
        "canonical_name": group.canonical_name,
        "canonical_key": group.canonical_key,
        "canonical_link": group.canonical_link,
        "level": group.level,
        "source_families": group.source_families,
        "members": members,
        "evidence_rows": evidence_rows[:80],
    }
    evidence_hash = hashlib.sha256(
        to_json(
            {
                "member_entity_ids": group.member_entity_ids,
                "evidence": [
                    [row.get("id"), row.get("event_at"), row.get("metric_name"), row.get("metric_value"), row.get("note")]
                    for row in evidence_rows
                ],
            }
        ).encode("utf-8")
    ).hexdigest()
    return replace(group, context=context, evidence_hash=evidence_hash)
```

Create `pipeline/decision/layer2_scheduler.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from pipeline.decision.layer2_models import CandidateGroup, LEVEL_RANK


@dataclass(frozen=True)
class Layer2Schedule:
    score_now: list[CandidateGroup]
    scout_edge_watch: list[CandidateGroup]
    skipped: list[dict[str, str]]
    pending: list[CandidateGroup]


def schedule_layer2_work(
    groups: list[CandidateGroup],
    *,
    previous_hashes: dict[str, str],
    max_edge_watch_scout: int,
    max_scored_candidates: int,
) -> Layer2Schedule:
    score_now: list[CandidateGroup] = []
    edge_watch: list[CandidateGroup] = []
    skipped: list[dict[str, str]] = []
    for group in sorted(groups, key=_priority_key):
        if previous_hashes.get(group.group_id) == group.evidence_hash:
            skipped.append({"group_id": group.group_id, "reason": "unchanged_evidence_hash"})
            continue
        if group.level == "edge_watch":
            edge_watch.append(group)
        else:
            score_now.append(group)
    pending = score_now[max_scored_candidates:] + edge_watch[max_edge_watch_scout:]
    return Layer2Schedule(
        score_now=score_now[:max_scored_candidates],
        scout_edge_watch=edge_watch[:max_edge_watch_scout],
        skipped=skipped,
        pending=pending,
    )


def _priority_key(group: CandidateGroup) -> tuple[int, str]:
    return (-LEVEL_RANK.get(group.level, 0), group.canonical_name.lower())
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
python3 -m pytest tests/test_layer2_context.py tests/test_layer2_scheduler.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pipeline/decision/layer2_context.py pipeline/decision/layer2_scheduler.py tests/test_layer2_context.py tests/test_layer2_scheduler.py
git commit -m "Add Layer 2 context scheduler"
```

## Task 5: Kimi Edge Watch Scout

**Files:**
- Create: `pipeline/decision/layer2_scout.py`
- Test: `tests/test_layer2_scout.py`

- [ ] **Step 1: Write the failing scout tests**

Create `tests/test_layer2_scout.py`:

```python
from __future__ import annotations

import sqlite3
import unittest

from pipeline.decision.layer2_models import CandidateGroup
from pipeline.decision.llm_provider import FakeLLMProvider
from pipeline.decision.schema import init_decision_db


class Layer2ScoutTest(unittest.TestCase):
    def test_scout_validates_and_persists_include_decision(self):
        from pipeline.decision.layer2_scout import scout_edge_watch_groups

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        provider = FakeLLMProvider([
            {
                "include_in_l2_scoring": True,
                "scout_score": 0.73,
                "reason": "Concrete early workflow evidence.",
                "needed_context": ["readme"],
                "risk": "single source",
                "confidence": 0.7,
            }
        ])
        group = CandidateGroup(
            group_id="group:edge",
            canonical_entity_id="entity:edge",
            canonical_name="Edge Project",
            canonical_key="name:edge-project",
            canonical_link="",
            member_entity_ids=["entity:edge"],
            level="edge_watch",
            source_families=["hn"],
            evidence_hash="hash",
            context={"evidence_rows": [{"note": "Show HN project"}]},
        )

        included = scout_edge_watch_groups(
            conn,
            feed_run_id="l2-run",
            groups=[group],
            provider=provider,
            prompt_version="layer2-edge-scout-v1",
        )

        self.assertEqual([row.group_id for row in included], ["group:edge"])
        row = conn.execute("select included_in_scoring, reason, provider, model from l2_scout_results").fetchone()
        self.assertEqual(row, (1, "Concrete early workflow evidence.", "fake", "fake-json"))
        self.assertEqual(provider.calls[0]["task"], "layer2_edge_scout")

    def test_scout_rejects_invalid_provider_shape(self):
        from pipeline.decision.layer2_scout import scout_edge_watch_groups

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        provider = FakeLLMProvider([{"reason": "missing fields"}])
        group = CandidateGroup(
            group_id="group:edge",
            canonical_entity_id="entity:edge",
            canonical_name="Edge Project",
            canonical_key="name:edge-project",
            canonical_link="",
            member_entity_ids=["entity:edge"],
            level="edge_watch",
            source_families=["hn"],
            evidence_hash="hash",
            context={},
        )

        with self.assertRaises(ValueError):
            scout_edge_watch_groups(
                conn,
                feed_run_id="l2-run",
                groups=[group],
                provider=provider,
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_layer2_scout.py -q
```

Expected: FAIL because `layer2_scout` does not exist.

- [ ] **Step 3: Implement scout**

Create `pipeline/decision/layer2_scout.py`:

```python
from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from pipeline.decision.layer2_models import CandidateGroup
from pipeline.decision.schema import to_json


DEFAULT_SCOUT_PROMPT_VERSION = "layer2-edge-scout-v1"


SCOUT_SYSTEM_PROMPT = """
You are the Edge Watch Scout for Hero Radar.
Decide whether an edge_watch candidate deserves Layer 2 scoring.
Do not promote deterministic level. Return strict JSON with:
include_in_l2_scoring boolean, scout_score number 0..1, reason string,
needed_context array of strings, risk string, confidence number 0..1.
"""


def scout_edge_watch_groups(
    conn: sqlite3.Connection,
    *,
    feed_run_id: str,
    groups: list[CandidateGroup],
    provider: Any,
    prompt_version: str = DEFAULT_SCOUT_PROMPT_VERSION,
) -> list[CandidateGroup]:
    included: list[CandidateGroup] = []
    for group in groups:
        payload = {
            "group_id": group.group_id,
            "candidate": group.context,
            "instruction": "Include only if the evidence suggests a concrete product/project/workflow worth semantic scoring.",
        }
        response = provider.complete_json(
            task="layer2_edge_scout",
            prompt_version=prompt_version,
            input_payload=payload,
            system_prompt=SCOUT_SYSTEM_PROMPT,
        )
        normalized = _validate_response(response)
        cache_key = _cache_key(provider.provider_name, provider.model, prompt_version, payload)
        conn.execute(
            """
            insert or replace into l2_scout_results(
              feed_run_id, group_id, included_in_scoring, scout_score, reason,
              needed_context_json, risk, confidence, provider, model, prompt_version, cache_key
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feed_run_id,
                group.group_id,
                1 if normalized["include_in_l2_scoring"] else 0,
                normalized["scout_score"],
                normalized["reason"],
                to_json(normalized["needed_context"]),
                normalized["risk"],
                normalized["confidence"],
                provider.provider_name,
                provider.model,
                prompt_version,
                cache_key,
            ),
        )
        if normalized["include_in_l2_scoring"]:
            included.append(group)
    conn.commit()
    return included


def _validate_response(response: dict[str, Any]) -> dict[str, Any]:
    required = ["include_in_l2_scoring", "scout_score", "reason", "needed_context", "risk", "confidence"]
    missing = [key for key in required if key not in response]
    if missing:
        raise ValueError(f"scout response missing fields: {missing}")
    return {
        "include_in_l2_scoring": bool(response["include_in_l2_scoring"]),
        "scout_score": _clamp_float(response["scout_score"], 0, 1),
        "reason": str(response["reason"])[:600],
        "needed_context": [str(item)[:80] for item in response["needed_context"] if str(item).strip()][:8],
        "risk": str(response["risk"])[:300],
        "confidence": _clamp_float(response["confidence"], 0, 1),
    }


def _clamp_float(value: Any, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"expected numeric value, got {value!r}") from exc
    return max(minimum, min(maximum, number))


def _cache_key(provider: str, model: str, prompt_version: str, payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(to_json(payload).encode("utf-8")).hexdigest()
    return f"{provider}:{model}:{prompt_version}:{digest}"


def default_deepdive_tools(
    conn: sqlite3.Connection,
    *,
    decision_run_id: str,
    enable_kimi_web_search: bool = False,
    web_search_client: Any | None = None,
) -> dict[str, ToolFn]:
    def fetch_cached_readme(arguments: dict[str, Any]) -> dict[str, Any]:
        repo = str(arguments.get("repo") or "").lower().strip().removeprefix("github:")
        row = conn.execute(
            """
            select response_json
            from api_cache
            where source = 'github_readme' and external_id = ? and status = 'ok'
            order by fetched_at desc
            limit 1
            """,
            (repo,),
        ).fetchone()
        return json.loads(row[0]) if row else {"missing": True, "repo": repo}

    def read_evidence_rows(arguments: dict[str, Any]) -> dict[str, Any]:
        entity_id = str(arguments.get("entity_id") or "")
        rows = conn.execute(
            """
            select source, event_at, metric_name, metric_value, family, signal_label, note, raw_url_or_ref
            from evidence_rows
            where run_id = ? and entity_id = ?
            order by event_at desc, id desc
            limit 80
            """,
            (decision_run_id, entity_id),
        ).fetchall()
        return {
            "rows": [
                {
                    "source": row[0],
                    "event_at": row[1],
                    "metric_name": row[2],
                    "metric_value": row[3],
                    "family": row[4],
                    "signal_label": row[5],
                    "note": row[6],
                    "raw_url_or_ref": row[7],
                }
                for row in rows
            ]
        }

    def kimi_web_search(arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        max_results = int(arguments.get("max_results") or 5)
        if not enable_kimi_web_search or web_search_client is None:
            return {"disabled": True, "query": query}
        return web_search_client.search(query=query, max_results=max(1, min(8, max_results)))

    return {
        "fetch_cached_readme": fetch_cached_readme,
        "read_evidence_rows": read_evidence_rows,
        "kimi_web_search": kimi_web_search,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python3 -m pytest tests/test_layer2_scout.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/decision/layer2_scout.py tests/test_layer2_scout.py
git commit -m "Add Layer 2 edge scout"
```

## Task 6: Kimi Scoring And Deterministic Aggregation

**Files:**
- Create: `pipeline/decision/layer2_scoring.py`
- Test: `tests/test_layer2_scoring.py`

- [ ] **Step 1: Write the failing scoring tests**

Create `tests/test_layer2_scoring.py`:

```python
from __future__ import annotations

import sqlite3
import unittest

from pipeline.decision.layer2_models import CandidateGroup
from pipeline.decision.llm_provider import FakeLLMProvider
from pipeline.decision.schema import init_decision_db


class Layer2ScoringTest(unittest.TestCase):
    def test_aggregate_score_uses_weighted_axes_and_penalty(self):
        from pipeline.decision.layer2_scoring import aggregate_l2_score

        score = aggregate_l2_score(
            {
                "momentum": 80,
                "workflow_shift": 90,
                "technical_substance": 70,
                "adoption_path": 60,
                "confidence": 75,
                "derivative_news_penalty": 10,
            }
        )

        self.assertEqual(score, 67.75)

    def test_scores_groups_and_persists_result(self):
        from pipeline.decision.layer2_scoring import score_candidate_groups

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        provider = FakeLLMProvider([
            {
                "axes": {
                    "momentum": 80,
                    "workflow_shift": 90,
                    "technical_substance": 70,
                    "adoption_path": 60,
                    "confidence": 75,
                    "derivative_news_penalty": 10,
                },
                "primary_reason": "Workflow Shift",
                "topic_tags": ["agent workflow"],
                "rationale_short": "Concrete repo-native workflow evidence.",
                "caveats": ["single day signal"],
            }
        ])
        group = CandidateGroup(
            group_id="group:repo",
            canonical_entity_id="entity:repo",
            canonical_name="owner/repo",
            canonical_key="github:owner/repo",
            canonical_link="https://github.com/owner/repo",
            member_entity_ids=["entity:repo"],
            level="potential",
            source_families=["github"],
            evidence_hash="hash",
            context={"evidence_rows": [{"note": "stars"}]},
        )

        scores = score_candidate_groups(conn, feed_run_id="l2-run", groups=[group], provider=provider)

        self.assertEqual(scores[0]["l2_score"], 67.75)
        row = conn.execute("select l2_score, primary_reason, provider, model from l2_scores").fetchone()
        self.assertEqual(row, (67.75, "Workflow Shift", "fake", "fake-json"))
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_layer2_scoring.py -q
```

Expected: FAIL because `layer2_scoring` does not exist.

- [ ] **Step 3: Implement scoring**

Create `pipeline/decision/layer2_scoring.py`:

```python
from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from pipeline.decision.layer2_models import CandidateGroup
from pipeline.decision.schema import to_json


DEFAULT_SCORING_PROMPT_VERSION = "layer2-scoring-v1"


SCORING_SYSTEM_PROMPT = """
You score Hero Radar candidates for today's Feed.
Return strict JSON with axes object:
momentum, workflow_shift, technical_substance, adoption_path, confidence,
derivative_news_penalty.
Positive axes are 0..100. derivative_news_penalty is 0..25.
Also return primary_reason, topic_tags, rationale_short, caveats.
Ground claims in the provided evidence/context.
"""


def aggregate_l2_score(axes: dict[str, Any]) -> float:
    score = (
        0.25 * _axis(axes, "momentum")
        + 0.25 * _axis(axes, "workflow_shift")
        + 0.20 * _axis(axes, "technical_substance")
        + 0.15 * _axis(axes, "adoption_path")
        + 0.15 * _axis(axes, "confidence")
        - _penalty(axes, "derivative_news_penalty")
    )
    return round(max(0.0, min(100.0, score)), 2)


def score_candidate_groups(
    conn: sqlite3.Connection,
    *,
    feed_run_id: str,
    groups: list[CandidateGroup],
    provider: Any,
    prompt_version: str = DEFAULT_SCORING_PROMPT_VERSION,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for group in groups:
        payload = {"group_id": group.group_id, "candidate": group.context}
        response = provider.complete_json(
            task="layer2_scoring",
            prompt_version=prompt_version,
            input_payload=payload,
            system_prompt=SCORING_SYSTEM_PROMPT,
        )
        normalized = _validate_response(response)
        l2_score = aggregate_l2_score(normalized["axes"])
        cache_key = _cache_key(provider.provider_name, provider.model, prompt_version, payload)
        conn.execute(
            """
            insert or replace into l2_scores(
              feed_run_id, group_id, l2_score, axes_json, primary_reason,
              topic_tags_json, rationale_short, caveats_json, provider, model,
              prompt_version, cache_key
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feed_run_id,
                group.group_id,
                l2_score,
                to_json(normalized["axes"]),
                normalized["primary_reason"],
                to_json(normalized["topic_tags"]),
                normalized["rationale_short"],
                to_json(normalized["caveats"]),
                provider.provider_name,
                provider.model,
                prompt_version,
                cache_key,
            ),
        )
        results.append({"group": group, "l2_score": l2_score, **normalized})
    conn.commit()
    return results


def _validate_response(response: dict[str, Any]) -> dict[str, Any]:
    if "axes" not in response:
        raise ValueError("scoring response missing axes")
    axes = {
        "momentum": _axis(response["axes"], "momentum"),
        "workflow_shift": _axis(response["axes"], "workflow_shift"),
        "technical_substance": _axis(response["axes"], "technical_substance"),
        "adoption_path": _axis(response["axes"], "adoption_path"),
        "confidence": _axis(response["axes"], "confidence"),
        "derivative_news_penalty": _penalty(response["axes"], "derivative_news_penalty"),
    }
    return {
        "axes": axes,
        "primary_reason": str(response.get("primary_reason") or "Signal")[:80],
        "topic_tags": [str(item)[:40] for item in response.get("topic_tags") or [] if str(item).strip()][:8],
        "rationale_short": str(response.get("rationale_short") or "")[:800],
        "caveats": [str(item)[:240] for item in response.get("caveats") or [] if str(item).strip()][:8],
    }


def _axis(axes: dict[str, Any], key: str) -> float:
    return _clamp_float(axes.get(key), 0, 100)


def _penalty(axes: dict[str, Any], key: str) -> float:
    return _clamp_float(axes.get(key, 0), 0, 25)


def _clamp_float(value: Any, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"expected numeric value, got {value!r}") from exc
    return max(minimum, min(maximum, number))


def _cache_key(provider: str, model: str, prompt_version: str, payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(to_json(payload).encode("utf-8")).hexdigest()
    return f"{provider}:{model}:{prompt_version}:{digest}"
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python3 -m pytest tests/test_layer2_scoring.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/decision/layer2_scoring.py tests/test_layer2_scoring.py
git commit -m "Add Layer 2 scoring"
```

## Task 7: Deepdive Selection And Bounded Agentic Harness

**Files:**
- Create: `pipeline/decision/layer2_deepdive.py`
- Test: `tests/test_layer2_deepdive.py`

- [ ] **Step 1: Write the failing deepdive tests**

Create `tests/test_layer2_deepdive.py`:

```python
from __future__ import annotations

import json
import sqlite3
import unittest

from pipeline.decision.layer2_models import CandidateGroup
from pipeline.decision.llm_provider import FakeLLMProvider
from pipeline.decision.schema import init_decision_db


class Layer2DeepdiveTest(unittest.TestCase):
    def group(self, group_id: str, level: str) -> CandidateGroup:
        return CandidateGroup(
            group_id=group_id,
            canonical_entity_id=f"entity:{group_id}",
            canonical_name=group_id,
            canonical_key=f"name:{group_id}",
            canonical_link="",
            member_entity_ids=[f"entity:{group_id}"],
            level=level,
            source_families=["github"],
            evidence_hash=f"hash-{group_id}",
            context={"canonical_name": group_id, "readme_excerpt": "README context"},
        )

    def test_select_deepdives_caps_by_score(self):
        from pipeline.decision.layer2_deepdive import select_deepdives

        scored = [
            {"group": self.group("a", "potential"), "l2_score": 70},
            {"group": self.group("b", "high_potential"), "l2_score": 80},
            {"group": self.group("c", "edge_watch"), "l2_score": 90},
        ]

        selected = select_deepdives(scored, max_deepdives=2, min_l2_score=0)

        self.assertEqual([row["group"].group_id for row in selected], ["c", "b"])

    def test_run_deepdives_uses_bounded_plan_tools_and_synthesis(self):
        from pipeline.decision.layer2_deepdive import DeepdiveLimits, run_deepdives

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        provider = FakeLLMProvider([
            {
                "tool_requests": [
                    {"name": "fetch_cached_readme", "arguments": {"repo": "owner/repo"}},
                    {"name": "kimi_web_search", "arguments": {"query": "owner/repo agent workflow"}},
                ]
            },
            {
                "summary": "Project summary",
                "why_now": "Moving today",
                "what_changed": "New workflow",
                "evidence": ["GitHub evidence"],
                "adoption_path": "CLI users",
                "risks": ["early"],
                "open_questions": ["pricing"],
                "recommended_action": "read",
            },
        ])
        calls = []

        def readme_tool(arguments):
            calls.append(("fetch_cached_readme", arguments))
            return {"excerpt": "README says concrete agent workflow."}

        def search_tool(arguments):
            calls.append(("kimi_web_search", arguments))
            return {"results": [{"title": "Discussion", "url": "https://example.com"}]}

        group = self.group("a", "potential")
        run_deepdives(
            conn,
            feed_run_id="l2-run",
            scored=[{"group": group, "l2_score": 90}],
            provider=provider,
            max_deepdives=1,
            min_l2_score=0,
            tools={
                "fetch_cached_readme": readme_tool,
                "kimi_web_search": search_tool,
            },
            limits=DeepdiveLimits(max_tool_calls=2),
        )

        self.assertEqual([name for name, _args in calls], ["fetch_cached_readme", "kimi_web_search"])
        self.assertEqual([call["task"] for call in provider.calls], ["layer2_deepdive_plan", "layer2_deepdive_synthesis"])
        report = conn.execute("select status, summary_json, tool_trace_json, provider from deepdive_reports").fetchone()
        self.assertEqual(report[0], "ok")
        self.assertIn("Project summary", report[1])
        trace = json.loads(report[2])
        self.assertEqual(len(trace), 2)
        self.assertEqual(trace[0]["tool"], "fetch_cached_readme")
        self.assertEqual(report[3], "fake")
        item = conn.execute("select section, rank, deepdive_status from l2_feed_items").fetchone()
        self.assertEqual(item, ("today_focus", 1, "ok"))

    def test_default_tools_use_injected_web_search_client(self):
        from pipeline.decision.layer2_deepdive import default_deepdive_tools

        class SearchClient:
            def __init__(self):
                self.calls = []

            def search(self, *, query, max_results):
                self.calls.append({"query": query, "max_results": max_results})
                return {"results": [{"title": "Result"}]}

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        client = SearchClient()
        tools = default_deepdive_tools(
            conn,
            decision_run_id="decision-run",
            enable_kimi_web_search=True,
            web_search_client=client,
        )

        result = tools["kimi_web_search"]({"query": "owner/repo", "max_results": 3})

        self.assertEqual(result["results"][0]["title"], "Result")
        self.assertEqual(client.calls, [{"query": "owner/repo", "max_results": 3}])
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_layer2_deepdive.py -q
```

Expected: FAIL because `layer2_deepdive` does not exist.

- [ ] **Step 3: Implement deepdive selector and bounded agentic harness**

Create `pipeline/decision/layer2_deepdive.py`:

```python
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable

from pipeline.decision.layer2_models import LEVEL_RANK
from pipeline.decision.schema import to_json, utc_now


DEFAULT_DEEPDIVE_PROMPT_VERSION = "layer2-deepdive-v1"


PLAN_SYSTEM_PROMPT = """
You are planning a bounded Hero Radar project deepdive.
Return strict JSON with tool_requests: an array of {name, arguments}.
Use only tools that are listed in available_tools. Respect max_tool_calls.
"""


SYNTHESIS_SYSTEM_PROMPT = """
You are writing a bounded Hero Radar project deepdive.
Use only candidate context, score context, and tool_trace.
Return strict JSON with summary, why_now, what_changed, evidence,
adoption_path, risks, open_questions, recommended_action.
"""


ToolFn = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class DeepdiveLimits:
    max_tool_calls: int = 8
    max_tool_result_chars: int = 6000


def select_deepdives(
    scored: list[dict[str, Any]],
    *,
    max_deepdives: int,
    min_l2_score: float,
) -> list[dict[str, Any]]:
    eligible = [row for row in scored if float(row.get("l2_score", 0)) >= min_l2_score]
    return sorted(
        eligible,
        key=lambda row: (
            -float(row.get("l2_score", 0)),
            -LEVEL_RANK.get(row["group"].level, 0),
            row["group"].canonical_name.lower(),
        ),
    )[:max(0, int(max_deepdives))]


def run_deepdives(
    conn: sqlite3.Connection,
    *,
    feed_run_id: str,
    scored: list[dict[str, Any]],
    provider: Any,
    max_deepdives: int,
    min_l2_score: float,
    tools: dict[str, ToolFn] | None = None,
    limits: DeepdiveLimits | None = None,
    prompt_version: str = DEFAULT_DEEPDIVE_PROMPT_VERSION,
) -> list[dict[str, Any]]:
    active_tools = tools or {}
    active_limits = limits or DeepdiveLimits()
    selected = select_deepdives(scored, max_deepdives=max_deepdives, min_l2_score=min_l2_score)
    reports: list[dict[str, Any]] = []
    for index, row in enumerate(selected, start=1):
        group = row["group"]
        plan_payload = {
            "group_id": group.group_id,
            "candidate": group.context,
            "score": _score_payload(row),
            "available_tools": sorted(active_tools),
            "limits": {"max_tool_calls": active_limits.max_tool_calls},
        }
        plan = provider.complete_json(
            task="layer2_deepdive_plan",
            prompt_version=prompt_version,
            input_payload=plan_payload,
            system_prompt=PLAN_SYSTEM_PROMPT,
        )
        tool_trace = _run_tool_plan(plan, active_tools, active_limits)
        synthesis_payload = {
            "group_id": group.group_id,
            "candidate": group.context,
            "score": _score_payload(row),
            "tool_trace": tool_trace,
        }
        response = provider.complete_json(
            task="layer2_deepdive_synthesis",
            prompt_version=prompt_version,
            input_payload=synthesis_payload,
            system_prompt=SYNTHESIS_SYSTEM_PROMPT,
        )
        summary = _validate_report(response)
        cache_key = _cache_key(provider.provider_name, provider.model, prompt_version, synthesis_payload)
        conn.execute(
            """
            insert or replace into deepdive_reports(
              feed_run_id, group_id, status, summary_json, tool_trace_json,
              provider, model, prompt_version, cache_key, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feed_run_id,
                group.group_id,
                "ok",
                to_json(summary),
                to_json(tool_trace),
                provider.provider_name,
                provider.model,
                prompt_version,
                cache_key,
                utc_now(),
            ),
        )
        conn.execute(
            """
            insert or replace into l2_feed_items(feed_run_id, group_id, section, rank, deepdive_status)
            values (?, ?, ?, ?, ?)
            """,
            (feed_run_id, group.group_id, "today_focus", index, "ok"),
        )
        reports.append({"group": group, "summary": summary, "tool_trace": tool_trace})
    conn.commit()
    return reports


def _run_tool_plan(
    plan: dict[str, Any],
    tools: dict[str, ToolFn],
    limits: DeepdiveLimits,
) -> list[dict[str, Any]]:
    requests = plan.get("tool_requests")
    if not isinstance(requests, list):
        raise ValueError("deepdive plan must include tool_requests array")
    trace: list[dict[str, Any]] = []
    for request in requests[: max(0, limits.max_tool_calls)]:
        if not isinstance(request, dict):
            continue
        name = str(request.get("name") or "")
        arguments = request.get("arguments") if isinstance(request.get("arguments"), dict) else {}
        if name not in tools:
            trace.append({"tool": name, "arguments": arguments, "status": "unavailable", "result": {}})
            continue
        result = tools[name](arguments)
        trace.append(
            {
                "tool": name,
                "arguments": arguments,
                "status": "ok",
                "result": _trim_result(result, limits.max_tool_result_chars),
            }
        )
    return trace


def _score_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "l2_score": row.get("l2_score"),
        "primary_reason": row.get("primary_reason"),
        "topic_tags": row.get("topic_tags", []),
        "rationale_short": row.get("rationale_short", ""),
        "caveats": row.get("caveats", []),
    }


def _trim_result(result: dict[str, Any], max_chars: int) -> dict[str, Any]:
    text = to_json(result)
    if len(text) <= max_chars:
        return result
    return {"truncated": True, "text": text[:max_chars]}


def _validate_report(response: dict[str, Any]) -> dict[str, Any]:
    required = ["summary", "why_now", "what_changed", "evidence", "adoption_path", "risks", "open_questions", "recommended_action"]
    missing = [key for key in required if key not in response]
    if missing:
        raise ValueError(f"deepdive response missing fields: {missing}")
    return {
        "summary": str(response["summary"])[:2000],
        "why_now": str(response["why_now"])[:1200],
        "what_changed": str(response["what_changed"])[:1200],
        "evidence": [str(item)[:400] for item in response["evidence"]][:10],
        "adoption_path": str(response["adoption_path"])[:1200],
        "risks": [str(item)[:400] for item in response["risks"]][:10],
        "open_questions": [str(item)[:400] for item in response["open_questions"]][:10],
        "recommended_action": str(response["recommended_action"])[:80],
    }


def _cache_key(provider: str, model: str, prompt_version: str, payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(to_json(payload).encode("utf-8")).hexdigest()
    return f"{provider}:{model}:{prompt_version}:{digest}"
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python3 -m pytest tests/test_layer2_deepdive.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/decision/layer2_deepdive.py tests/test_layer2_deepdive.py
git commit -m "Add Layer 2 deepdive harness"
```

## Task 8: Layer 2 Runner And Daily Pipeline Integration

**Files:**
- Create: `pipeline/decision/run_layer2_feed.py`
- Modify: `pipeline/run_daily.py`
- Modify: `pipeline/config.json`
- Test: `tests/test_run_layer2_feed.py`
- Test: `tests/test_daily_pipeline.py`

- [ ] **Step 1: Write the failing runner tests**

Create `tests/test_run_layer2_feed.py`:

```python
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from pipeline.decision.schema import init_decision_db


class Layer2RunnerTest(unittest.TestCase):
    def test_default_feed_run_id_is_stable_prefix(self):
        from pipeline.decision.run_layer2_feed import default_feed_run_id

        self.assertEqual(
            default_feed_run_id("2026-05-31T12:34:56Z"),
            "l2_20260531T123456",
        )

    def test_run_layer2_with_fake_provider_writes_feed_run(self):
        from pipeline.decision.run_layer2_feed import run_layer2_feed
        from pipeline.decision.llm_provider import FakeLLMProvider

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            conn = sqlite3.connect(db_path)
            init_decision_db(conn)
            conn.executescript(
                """
                create table items (
                    id integer primary key,
                    source text not null,
                    name text not null,
                    url text,
                    description text,
                    metadata_json text not null,
                    raw_json text not null
                );
                """
            )
            conn.execute(
                "insert into decision_runs(run_id, source_snapshot_run_id, started_at, completed_at, status, config_hash, rule_version, note) values (?, ?, ?, ?, ?, ?, ?, ?)",
                ("decision-run", "source-run", "2026-05-31T00:00:00Z", "2026-05-31T00:01:00Z", "ok", "hash", "rules-v1", ""),
            )
            conn.execute(
                "insert into entities(entity_id, canonical_entity, canonical_key, key_type, first_seen, aliases_json, source_item_ids_json) values (?, ?, ?, ?, ?, ?, ?)",
                ("entity:repo", "owner/repo", "github:owner/repo", "github", "2026-05-31T00:00:00Z", "[]", "[]"),
            )
            conn.execute(
                "insert into potential_candidates(entity_id, run_id, level, fired_families_json, first_trigger_at) values (?, ?, ?, ?, ?)",
                ("entity:repo", "decision-run", "potential", '["github"]', "2026-05-31T00:00:00Z"),
            )
            conn.commit()
            conn.close()

            provider = FakeLLMProvider([
                {
                    "axes": {
                        "momentum": 80,
                        "workflow_shift": 80,
                        "technical_substance": 80,
                        "adoption_path": 80,
                        "confidence": 80,
                        "derivative_news_penalty": 0,
                    },
                    "primary_reason": "Workflow Shift",
                    "topic_tags": ["agent workflow"],
                    "rationale_short": "Worth reading.",
                    "caveats": [],
                },
                {
                    "tool_requests": []
                },
                {
                    "summary": "Summary",
                    "why_now": "Now",
                    "what_changed": "Changed",
                    "evidence": ["Evidence"],
                    "adoption_path": "Path",
                    "risks": [],
                    "open_questions": [],
                    "recommended_action": "read",
                },
            ])
            summary = run_layer2_feed(
                db_path=db_path,
                decision_run_id="decision-run",
                feed_run_id="l2-test",
                now="2026-05-31T12:00:00Z",
                provider=provider,
                config={"max_deepdives_per_run": 1, "deepdive_min_l2_score": 0},
            )

            self.assertTrue(summary["ok"])
            self.assertEqual(summary["feed_run_id"], "l2-test")
            conn = sqlite3.connect(db_path)
            self.assertEqual(conn.execute("select count(*) from l2_feed_runs").fetchone()[0], 1)
            self.assertEqual(conn.execute("select count(*) from l2_scores").fetchone()[0], 1)
            conn.close()
```

- [ ] **Step 2: Extend failing daily pipeline test**

Append to `tests/test_daily_pipeline.py`:

```python
    def test_daily_pipeline_can_run_layer2_after_decision(self):
        from pipeline.run_daily import run_daily

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            runner = FakeRunner()

            summary = run_daily(
                root=root,
                python="py",
                run_id="decision_daily_test",
                now="2026-05-31T12:00:00Z",
                run_layer2=True,
                layer2_scout_limit=10,
                layer2_scoring_limit=20,
                layer2_deepdive_limit=2,
                runner=runner,
            )

        self.assertTrue(summary["ok"])
        self.assertEqual(len(runner.calls), 3)
        self.assertEqual(
            runner.calls[2]["cmd"],
            [
                "py",
                "-m",
                "pipeline.decision.run_layer2_feed",
                "--decision-run-id",
                "decision_daily_test",
                "--now",
                "2026-05-31T12:00:00Z",
                "--edge-scout-limit",
                "10",
                "--scoring-limit",
                "20",
                "--deepdive-limit",
                "2",
            ],
        )
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_run_layer2_feed.py tests/test_daily_pipeline.py -q
```

Expected: FAIL because runner and daily args do not exist.

- [ ] **Step 4: Implement Layer 2 runner**

Create `pipeline/decision/run_layer2_feed.py` with this public surface:

```python
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from pipeline.decision.kimi_provider import (
    DEFAULT_KIMI_DEEPDIVE_MODEL,
    DEFAULT_KIMI_SCORING_MODEL,
    KimiProvider,
)
from pipeline.decision.layer2_context import assemble_group_context
from pipeline.decision.layer2_deepdive import DeepdiveLimits, default_deepdive_tools, run_deepdives
from pipeline.decision.layer2_grouping import build_candidate_groups, persist_candidate_groups
from pipeline.decision.layer2_scheduler import schedule_layer2_work
from pipeline.decision.layer2_scout import scout_edge_watch_groups
from pipeline.decision.layer2_scoring import score_candidate_groups
from pipeline.decision.schema import init_decision_db, to_json, utc_now


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "hero_radar.sqlite"


def default_feed_run_id(now: str | None = None) -> str:
    value = now or utc_now()
    compact = value.replace("-", "").replace(":", "").rstrip("Z")
    return f"l2_{compact}"


def latest_decision_run(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "select run_id from decision_runs where status = 'ok' order by coalesce(completed_at, started_at) desc limit 1"
    ).fetchone()
    if not row:
        raise RuntimeError("no successful decision run found")
    return str(row[0])


def previous_group_hashes(conn: sqlite3.Connection, decision_run_id: str) -> dict[str, str]:
    row = conn.execute(
        "select feed_run_id from l2_feed_runs where decision_run_id = ? and status = 'ok' order by coalesce(completed_at, started_at) desc limit 1",
        (decision_run_id,),
    ).fetchone()
    if not row:
        return {}
    return {
        str(group_id): str(evidence_hash)
        for group_id, evidence_hash in conn.execute(
            "select group_id, evidence_hash from l2_candidate_groups where feed_run_id = ?",
            (row[0],),
        ).fetchall()
    }


def run_layer2_feed(
    *,
    db_path: Path = DB_PATH,
    decision_run_id: str | None = None,
    feed_run_id: str | None = None,
    now: str | None = None,
    provider: Any | None = None,
    deepdive_provider: Any | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = config or {}
    active_now = now or utc_now()
    active_feed_run_id = feed_run_id or default_feed_run_id(active_now)
    conn = sqlite3.connect(db_path)
    init_decision_db(conn)
    try:
        active_decision_run_id = decision_run_id or latest_decision_run(conn)
        scout_provider = provider or KimiProvider(model=str(cfg.get("edge_scout_model") or DEFAULT_KIMI_SCORING_MODEL))
        scoring_provider = provider or KimiProvider(model=str(cfg.get("scoring_model") or DEFAULT_KIMI_SCORING_MODEL))
        active_deepdive_provider = deepdive_provider or provider or KimiProvider(model=str(cfg.get("deepdive_model") or DEFAULT_KIMI_DEEPDIVE_MODEL))
        model_profile = {
            "scout": getattr(scout_provider, "model", ""),
            "scoring": getattr(scoring_provider, "model", ""),
            "deepdive": getattr(active_deepdive_provider, "model", ""),
        }
        conn.execute(
            """
            insert or replace into l2_feed_runs(feed_run_id, decision_run_id, started_at, status, config_hash, model_profile_json, note)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (active_feed_run_id, active_decision_run_id, active_now, "running", "manual", to_json(model_profile), ""),
        )
        raw_groups = build_candidate_groups(conn, decision_run_id=active_decision_run_id)
        groups = [
            assemble_group_context(conn, decision_run_id=active_decision_run_id, group=group)
            for group in raw_groups
        ]
        persist_candidate_groups(conn, feed_run_id=active_feed_run_id, groups=groups)
        schedule = schedule_layer2_work(
            groups,
            previous_hashes=previous_group_hashes(conn, active_decision_run_id),
            max_edge_watch_scout=int(cfg.get("max_edge_watch_scout", cfg.get("edge_scout_limit", 50))),
            max_scored_candidates=int(cfg.get("max_scored_candidates", cfg.get("scoring_limit", 150))),
        )
        scouted = scout_edge_watch_groups(
            conn,
            feed_run_id=active_feed_run_id,
            groups=schedule.scout_edge_watch,
            provider=scout_provider,
        ) if schedule.scout_edge_watch else []
        scored = score_candidate_groups(
            conn,
            feed_run_id=active_feed_run_id,
            groups=[*schedule.score_now, *scouted],
            provider=scoring_provider,
        )
        for rank, row in enumerate(sorted(scored, key=lambda item: -float(item["l2_score"])), start=1):
            conn.execute(
                "insert or replace into l2_feed_items(feed_run_id, group_id, section, rank, deepdive_status) values (?, ?, ?, ?, ?)",
                (active_feed_run_id, row["group"].group_id, "scored", rank, "not_deepdived"),
            )
        reports = run_deepdives(
            conn,
            feed_run_id=active_feed_run_id,
            scored=scored,
            provider=active_deepdive_provider,
            max_deepdives=int(cfg.get("max_deepdives_per_run", 10)),
            min_l2_score=float(cfg.get("deepdive_min_l2_score", 70)),
            tools=default_deepdive_tools(
                conn,
                decision_run_id=active_decision_run_id,
                enable_kimi_web_search=bool(cfg.get("enable_kimi_web_search", False)),
            ),
            limits=DeepdiveLimits(
                max_tool_calls=(
                    int(cfg.get("max_web_search_calls_per_candidate", 3))
                    + int(cfg.get("max_repo_files_per_candidate", 8))
                    + int(cfg.get("max_pages_per_candidate", 6))
                )
            ),
        )
        conn.execute(
            "update l2_feed_runs set completed_at = ?, status = ?, note = ? where feed_run_id = ?",
            (utc_now(), "ok", to_json({"scored": len(scored), "deepdives": len(reports)}), active_feed_run_id),
        )
        conn.commit()
        return {
            "ok": True,
            "feed_run_id": active_feed_run_id,
            "decision_run_id": active_decision_run_id,
            "groups": len(groups),
            "scored": len(scored),
            "deepdives": len(reports),
        }
    except Exception as exc:
        conn.execute(
            "update l2_feed_runs set completed_at = ?, status = ?, note = ? where feed_run_id = ?",
            (utc_now(), "error", f"{type(exc).__name__}: {exc}", active_feed_run_id),
        )
        conn.commit()
        raise
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Layer 2 Kimi Feed")
    parser.add_argument("--decision-run-id", default=None)
    parser.add_argument("--feed-run-id", default=None)
    parser.add_argument("--now", default=None)
    parser.add_argument("--edge-scout-limit", type=int, default=50)
    parser.add_argument("--scoring-limit", type=int, default=150)
    parser.add_argument("--deepdive-limit", type=int, default=10)
    parser.add_argument("--deepdive-min-l2-score", type=float, default=70)
    parser.add_argument("--scout-model", default=DEFAULT_KIMI_SCORING_MODEL)
    parser.add_argument("--scoring-model", default=DEFAULT_KIMI_SCORING_MODEL)
    parser.add_argument("--deepdive-model", default=DEFAULT_KIMI_DEEPDIVE_MODEL)
    args = parser.parse_args(argv)
    summary = run_layer2_feed(
        decision_run_id=args.decision_run_id,
        feed_run_id=args.feed_run_id,
        now=args.now,
        config={
            "max_edge_watch_scout": args.edge_scout_limit,
            "max_scored_candidates": args.scoring_limit,
            "max_deepdives_per_run": args.deepdive_limit,
            "deepdive_min_l2_score": args.deepdive_min_l2_score,
            "edge_scout_model": args.scout_model,
            "scoring_model": args.scoring_model,
            "deepdive_model": args.deepdive_model,
        },
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 5: Integrate `pipeline/run_daily.py`**

Add `run_layer2` args to `run_daily()`, create `layer2_command()`, and append it after the decision stage only when `run_layer2=True`.

Command shape:

```python
[
    python,
    "-m",
    "pipeline.decision.run_layer2_feed",
    "--decision-run-id",
    active_run_id,
    "--now",
    active_now,
    "--edge-scout-limit",
    str(layer2_scout_limit),
    "--scoring-limit",
    str(layer2_scoring_limit),
    "--deepdive-limit",
    str(layer2_deepdive_limit),
]
```

Add CLI flags:

```text
--run-layer2
--layer2-scout-limit
--layer2-scoring-limit
--layer2-deepdive-limit
--layer2-deepdive-min-l2-score
--layer2-scout-model
--layer2-scoring-model
--layer2-deepdive-model
```

- [ ] **Step 6: Add default config**

Modify `pipeline/config.json`:

```json
  "layer2": {
    "enabled": false,
    "edge_scout_model": "kimi-k2.5",
    "scoring_model": "kimi-k2.5",
    "deepdive_model": "kimi-k2.6",
    "max_edge_watch_scout": 50,
    "max_scored_candidates": 150,
    "max_deepdives_per_run": 10,
    "deepdive_min_l2_score": 70,
    "enable_kimi_web_search": true,
    "max_web_search_calls_per_candidate": 3,
    "max_repo_files_per_candidate": 8,
    "max_pages_per_candidate": 6,
    "llm_concurrency": 4
  }
```

- [ ] **Step 7: Run tests to verify they pass**

Run:

```bash
python3 -m pytest tests/test_run_layer2_feed.py tests/test_daily_pipeline.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add pipeline/decision/run_layer2_feed.py pipeline/run_daily.py pipeline/config.json tests/test_run_layer2_feed.py tests/test_daily_pipeline.py
git commit -m "Add Layer 2 feed runner"
```

## Task 9: Feed API And Feedback

**Files:**
- Modify: `pipeline/server.py`
- Test: `tests/test_feed_api.py`

- [ ] **Step 1: Write the failing API tests**

Create `tests/test_feed_api.py`:

```python
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class FeedApiTest(unittest.TestCase):
    def make_db(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        from pipeline.decision.schema import init_decision_db

        temp = tempfile.TemporaryDirectory()
        db_path = Path(temp.name) / "hero.sqlite"
        conn = sqlite3.connect(db_path)
        init_decision_db(conn)
        conn.execute(
            "insert into l2_feed_runs(feed_run_id, decision_run_id, started_at, completed_at, status, config_hash, model_profile_json, note) values (?, ?, ?, ?, ?, ?, ?, ?)",
            ("l2-run", "decision-run", "2026-05-31T00:00:00Z", "2026-05-31T00:01:00Z", "ok", "hash", json.dumps({"scout": "kimi-k2.5"}), ""),
        )
        conn.execute(
            "insert into l2_candidate_groups(group_id, feed_run_id, canonical_entity_id, canonical_name, canonical_key, canonical_link, member_entity_ids_json, level, source_families_json, evidence_hash, grouping_reason_json, context_json) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("group:repo", "l2-run", "entity:repo", "owner/repo", "github:owner/repo", "https://github.com/owner/repo", '["entity:repo"]', "potential", '["github"]', "hash", "{}", json.dumps({"evidence_rows": []})),
        )
        conn.execute(
            "insert into l2_scores(feed_run_id, group_id, l2_score, axes_json, primary_reason, topic_tags_json, rationale_short, caveats_json, provider, model, prompt_version, cache_key) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("l2-run", "group:repo", 88, json.dumps({"momentum": 80}), "Workflow Shift", '["agent workflow"]', "Worth reading.", "[]", "kimi", "kimi-k2.5", "v1", "cache"),
        )
        conn.execute(
            "insert into deepdive_reports(feed_run_id, group_id, status, summary_json, tool_trace_json, provider, model, prompt_version, cache_key, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("l2-run", "group:repo", "ok", json.dumps({"summary": "Deep summary"}), "[]", "kimi", "kimi-k2.6", "v1", "cache", "2026-05-31T00:02:00Z"),
        )
        conn.execute(
            "insert into l2_feed_items(feed_run_id, group_id, section, rank, deepdive_status) values (?, ?, ?, ?, ?)",
            ("l2-run", "group:repo", "today_focus", 1, "ok"),
        )
        conn.commit()
        conn.close()
        return temp, db_path

    def test_query_feed_payload_returns_today_focus(self):
        import pipeline.server as server

        temp, db_path = self.make_db()
        self.addCleanup(temp.cleanup)
        with mock.patch.object(server, "DB_PATH", db_path):
            payload = server.query_feed_payload()

        self.assertEqual(payload["feed_run_id"], "l2-run")
        self.assertEqual(payload["today_focus"][0]["group_id"], "group:repo")
        self.assertEqual(payload["today_focus"][0]["l2_score"], 88)
        self.assertEqual(payload["today_focus"][0]["deepdive"]["summary"], "Deep summary")

    def test_record_feed_feedback_upserts_vote(self):
        import pipeline.server as server

        temp, db_path = self.make_db()
        self.addCleanup(temp.cleanup)
        with mock.patch.object(server, "DB_PATH", db_path):
            server.record_feed_feedback({"feed_run_id": "l2-run", "group_id": "group:repo", "vote": "up"})
            server.record_feed_feedback({"feed_run_id": "l2-run", "group_id": "group:repo", "vote": "down"})

        conn = sqlite3.connect(db_path)
        row = conn.execute("select vote from feed_feedback").fetchone()
        conn.close()
        self.assertEqual(row[0], "down")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_feed_api.py -q
```

Expected: FAIL because `query_feed_payload` and `record_feed_feedback` do not exist.

- [ ] **Step 3: Implement API helpers and routes**

In `pipeline/server.py`, add:

```python
def query_latest_feed_run(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        """
        select feed_run_id
        from l2_feed_runs
        where status = 'ok'
        order by coalesce(completed_at, started_at) desc, started_at desc
        limit 1
        """
    ).fetchone()
    return row[0] if row else None


def query_feed_payload(feed_run_id: str | None = None) -> dict[str, Any]:
    conn = connect_decision_db()
    try:
        active_feed_run_id = feed_run_id or query_latest_feed_run(conn) or ""
        if not active_feed_run_id:
            return {
                "feed_run_id": "",
                "decision_run_id": "",
                "generated_at": "",
                "model_profile": {},
                "today_focus": [],
                "scored_list": [],
                "pending": {"edge_watch_scout": 0, "deepdive": 0},
            }
        run = conn.execute(
            "select decision_run_id, completed_at, started_at, model_profile_json from l2_feed_runs where feed_run_id = ?",
            (active_feed_run_id,),
        ).fetchone()
        items = [_feed_item(row) for row in conn.execute(
            """
            select fi.section, fi.rank, fi.deepdive_status, g.group_id, g.canonical_entity_id,
                   g.canonical_name, g.canonical_key, g.canonical_link, g.member_entity_ids_json,
                   g.level, g.source_families_json, g.context_json,
                   s.l2_score, s.axes_json, s.primary_reason, s.topic_tags_json,
                   s.rationale_short, s.caveats_json,
                   d.summary_json, ff.vote
            from l2_feed_items fi
            join l2_candidate_groups g on g.feed_run_id = fi.feed_run_id and g.group_id = fi.group_id
            left join l2_scores s on s.feed_run_id = fi.feed_run_id and s.group_id = fi.group_id
            left join deepdive_reports d on d.feed_run_id = fi.feed_run_id and d.group_id = fi.group_id
            left join feed_feedback ff on ff.feed_run_id = fi.feed_run_id and ff.group_id = fi.group_id
            where fi.feed_run_id = ?
            order by fi.section, fi.rank
            """,
            (active_feed_run_id,),
        ).fetchall()]
        return {
            "feed_run_id": active_feed_run_id,
            "decision_run_id": run[0],
            "generated_at": run[1] or run[2],
            "model_profile": json_loads(run[3], {}),
            "today_focus": [item for item in items if item["section"] == "today_focus"],
            "scored_list": [item for item in items if item["section"] == "scored"],
            "pending": {"edge_watch_scout": 0, "deepdive": 0},
        }
    finally:
        conn.close()


def _feed_item(row: tuple[Any, ...]) -> dict[str, Any]:
    context = json_loads(row[11], {})
    return {
        "section": row[0],
        "rank": row[1],
        "deepdive_status": row[2],
        "group_id": row[3],
        "canonical_entity_id": row[4],
        "canonical_name": row[5],
        "canonical_key": row[6],
        "canonical_link": row[7],
        "entity_ids": json_loads(row[8], []),
        "level": row[9],
        "source_families": json_loads(row[10], []),
        "context": context,
        "l2_score": row[12],
        "axes": json_loads(row[13], {}),
        "primary_reason": row[14],
        "topic_tags": json_loads(row[15], []),
        "rationale_short": row[16],
        "caveats": json_loads(row[17], []),
        "deepdive": json_loads(row[18], {}) if row[18] else None,
        "feedback": row[19],
    }


def record_feed_feedback(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    feed_run_id = str(payload.get("feed_run_id") or "")
    group_id = str(payload.get("group_id") or "")
    vote = str(payload.get("vote") or "")
    if vote not in {"up", "down", "clear"}:
        raise ValueError("vote must be up, down, or clear")
    conn = connect_decision_db()
    try:
        if vote == "clear":
            conn.execute("delete from feed_feedback where feed_run_id = ? and group_id = ?", (feed_run_id, group_id))
        else:
            conn.execute(
                """
                insert into feed_feedback(feed_run_id, group_id, vote, created_at)
                values (?, ?, ?, ?)
                on conflict(feed_run_id, group_id) do update set
                    vote = excluded.vote,
                    created_at = excluded.created_at
                """,
                (feed_run_id, group_id, vote, dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")),
            )
        conn.commit()
        return {"ok": True, "vote": "" if vote == "clear" else vote}
    finally:
        conn.close()
```

Add GET route:

```python
if path == "/api/feed":
    query = parse_qs(parsed.query)
    requested_feed_run_id = (query.get("feed_run_id") or [""])[0] or None
    json_response(self, query_feed_payload(requested_feed_run_id), cors=True)
    return
```

Add POST route:

```python
if path == "/api/feed/feedback":
    try:
        json_response(self, record_feed_feedback(read_request_json(self)), cors=True)
    except Exception as exc:
        json_response(self, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=400, cors=True)
    return
```

Add compact feed to `query_dashboard_data_payload()`:

```python
payload["feed"] = query_feed_payload()
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python3 -m pytest tests/test_feed_api.py tests/test_dashboard_data_api.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/server.py tests/test_feed_api.py tests/test_dashboard_data_api.py
git commit -m "Add Layer 2 feed API"
```

## Task 10: React Feed Data Model

**Files:**
- Modify: `web/src/dashboardModel.js`
- Create: `web/src/feedModel.test.mjs`

- [ ] **Step 1: Write the failing Feed model tests**

Create `web/src/feedModel.test.mjs`:

```javascript
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  feedRows,
  feedRunSummary,
  normalizeFeedPayload,
  scoreTone,
} from './dashboardModel.js';

const payload = {
  feed_run_id: 'l2-run',
  decision_run_id: 'decision-run',
  generated_at: '2026-05-31T12:00:00Z',
  model_profile: { scout: 'kimi-k2.5', scoring: 'kimi-k2.5', deepdive: 'kimi-k2.6' },
  today_focus: [
    {
      group_id: 'group:repo',
      canonical_name: 'owner/repo',
      canonical_key: 'github:owner/repo',
      canonical_link: 'https://github.com/owner/repo',
      level: 'potential',
      l2_score: 88,
      primary_reason: 'Workflow Shift',
      topic_tags: ['agent workflow'],
      rationale_short: 'Worth reading.',
      source_families: ['github'],
      deepdive_status: 'ok',
      deepdive: { summary: 'Deep summary' },
      context: {
        members: [
          {
            evidence_bullets: [
              { label: 'GH +321 stars / 24h', display_label: 'GitHub: +321 stars in 24h', origin_type: 'deterministic_rule' },
            ],
            source_links: [
              { item_id: 1, channel: 'github_trending', channel_label: 'GitHub Trending', window: '24h' },
            ],
            context_preview: 'Repo description',
          },
        ],
      },
    },
  ],
  scored_list: [],
  pending: { edge_watch_scout: 2, deepdive: 1 },
};

test('normalizeFeedPayload keeps run summary and item evidence', () => {
  const normalized = normalizeFeedPayload(payload);

  assert.equal(normalized.feed_run_id, 'l2-run');
  assert.equal(normalized.today_focus[0].title, 'owner/repo');
  assert.equal(normalized.today_focus[0].evidence_bullets[0].display_label, 'GitHub: +321 stars in 24h');
  assert.equal(normalized.today_focus[0].source_links[0].channel_label, 'GitHub Trending');
});

test('feedRows merges today focus and scored list with section markers', () => {
  const rows = feedRows(normalizeFeedPayload(payload));

  assert.deepEqual(rows.map((row) => [row.group_id, row.section]), [['group:repo', 'today_focus']]);
});

test('feedRunSummary formats model profile without secrets', () => {
  const summary = feedRunSummary(normalizeFeedPayload(payload));

  assert.equal(summary.models, 'scout kimi-k2.5 · scoring kimi-k2.5 · deepdive kimi-k2.6');
});

test('scoreTone maps numeric score to stable UI tone', () => {
  assert.equal(scoreTone(90), 'hot');
  assert.equal(scoreTone(75), 'warm');
  assert.equal(scoreTone(55), 'steady');
  assert.equal(scoreTone(30), 'quiet');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd web && npm test -- feedModel.test.mjs
```

Expected: FAIL because the Feed model helpers do not exist.

- [ ] **Step 3: Implement Feed model helpers**

In `web/src/dashboardModel.js`, add exports:

```javascript
export function normalizeFeedPayload(payload = {}) {
  const normalizeItem = (item, section) => {
    const members = Array.isArray(item?.context?.members) ? item.context.members : [];
    const evidence = members.flatMap((member) => Array.isArray(member.evidence_bullets) ? member.evidence_bullets : []);
    const sourceLinks = members.flatMap((member) => normalizeCandidateSourceLinks(member.source_links));
    return {
      ...item,
      section,
      title: String(item?.canonical_name || item?.canonical_key || item?.group_id || ''),
      l2_score: Number(item?.l2_score || 0),
      topic_tags: Array.isArray(item?.topic_tags) ? item.topic_tags : [],
      evidence_bullets: evidence,
      source_links: sourceLinks,
      context_preview: members.find((member) => member.context_preview)?.context_preview || '',
      deepdive: item?.deepdive || null,
    };
  };
  return {
    feed_run_id: String(payload?.feed_run_id || ''),
    decision_run_id: String(payload?.decision_run_id || ''),
    generated_at: String(payload?.generated_at || ''),
    model_profile: payload?.model_profile || {},
    today_focus: (payload?.today_focus || []).map((item) => normalizeItem(item, 'today_focus')),
    scored_list: (payload?.scored_list || []).map((item) => normalizeItem(item, 'scored')),
    pending: payload?.pending || { edge_watch_scout: 0, deepdive: 0 },
  };
}

export function feedRows(feed) {
  return [
    ...(feed?.today_focus || []),
    ...(feed?.scored_list || []),
  ];
}

export function feedRunSummary(feed) {
  const profile = feed?.model_profile || {};
  return {
    run: feed?.feed_run_id || '',
    decision: feed?.decision_run_id || '',
    generated: feed?.generated_at || '',
    models: [
      profile.scout ? `scout ${profile.scout}` : '',
      profile.scoring ? `scoring ${profile.scoring}` : '',
      profile.deepdive ? `deepdive ${profile.deepdive}` : '',
    ].filter(Boolean).join(' · '),
  };
}

export function scoreTone(score) {
  const value = Number(score || 0);
  if (value >= 85) return 'hot';
  if (value >= 70) return 'warm';
  if (value >= 50) return 'steady';
  return 'quiet';
}
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
cd web && npm test
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/dashboardModel.js web/src/feedModel.test.mjs
git commit -m "Add Feed UI model helpers"
```

## Task 11: Designed Daily Feed UI

**Files:**
- Modify: `web/package.json`
- Modify: `web/package-lock.json`
- Modify: `web/src/App.jsx`
- Modify: `web/src/styles.css`
- Test: `web/src/feedModel.test.mjs`
- Test: browser screenshot via in-app Browser or Playwright

- [ ] **Step 1: Install icon dependency**

Run:

```bash
cd web && npm install @phosphor-icons/react
```

Expected: `web/package.json` and `web/package-lock.json` add `@phosphor-icons/react`.

- [ ] **Step 2: Write a failing render smoke test in model layer**

Append to `web/src/feedModel.test.mjs`:

```javascript
import { feedEmptyState } from './dashboardModel.js';

test('feedEmptyState distinguishes missing feed from empty scored run', () => {
  assert.equal(feedEmptyState({ feed_run_id: '', today_focus: [], scored_list: [] }), 'missing');
  assert.equal(feedEmptyState({ feed_run_id: 'l2-run', today_focus: [], scored_list: [] }), 'empty');
});
```

- [ ] **Step 3: Run test to verify it fails**

Run:

```bash
cd web && npm test -- feedModel.test.mjs
```

Expected: FAIL because `feedEmptyState` does not exist.

- [ ] **Step 4: Add empty state helper**

In `web/src/dashboardModel.js`:

```javascript
export function feedEmptyState(feed) {
  if (!feed?.feed_run_id) return 'missing';
  if (!(feed.today_focus || []).length && !(feed.scored_list || []).length) return 'empty';
  return '';
}
```

- [ ] **Step 5: Replace locked Daily Feed placeholder**

In `web/src/App.jsx`:

1. Import Feed helpers and Phosphor icons:

```javascript
import {
  ArrowSquareOut,
  ChartLineUp,
  Lightning,
  Sparkle,
  ThumbsDown,
  ThumbsUp,
} from '@phosphor-icons/react';
```

Add to existing dashboard model import:

```javascript
  feedEmptyState,
  feedRunSummary,
  normalizeFeedPayload,
  scoreTone,
```

2. Add components near the current Feed component:

```javascript
function DailyFeedView({ payload }) {
  const feed = normalizeFeedPayload(payload.feed || {});
  const emptyState = feedEmptyState(feed);
  if (emptyState) {
    return (
      <section className="daily-feed-shell">
        <div className="feed-run-strip">
          <span>每日 Feed</span>
          <span>{emptyState === 'missing' ? '还没有 Layer 2 run' : '当前 run 没有 scored items'}</span>
        </div>
      </section>
    );
  }
  const summary = feedRunSummary(feed);
  return (
    <section className="daily-feed-shell">
      <div className="feed-run-strip">
        <div>
          <strong>{summary.run}</strong>
          <span>{summary.decision}</span>
        </div>
        <div>
          <span>{summary.generated}</span>
          <span>{summary.models}</span>
        </div>
      </div>
      <section className="today-focus-grid" aria-label="Today Focus">
        {feed.today_focus.map((item) => (
          <FeedSignalCard key={item.group_id} item={item} />
        ))}
      </section>
      <section className="scored-feed-list" aria-label="Scored Feed">
        {feed.scored_list.map((item) => (
          <ScoredFeedRow key={item.group_id} item={item} />
        ))}
      </section>
    </section>
  );
}

function FeedSignalCard({ item }) {
  const tone = scoreTone(item.l2_score);
  return (
    <article className={`feed-signal-card ${tone}`}>
      <div className="signal-card-topline">
        <span className="score-rail">{Math.round(item.l2_score)}</span>
        <span>{item.primary_reason}</span>
        <Sparkle size={16} weight="duotone" aria-hidden="true" />
      </div>
      <h2>{item.title}</h2>
      <p>{item.rationale_short || item.context_preview}</p>
      <div className="feed-tags">
        {(item.topic_tags || []).slice(0, 4).map((tag) => <span key={tag}>{tag}</span>)}
      </div>
      {item.deepdive ? <p className="deepdive-summary">{item.deepdive.summary}</p> : null}
      <FeedEvidence item={item} />
      <FeedLinks item={item} />
      <div className="feed-feedback" aria-label="Feed feedback">
        <button type="button" title="有用"><ThumbsUp size={16} /></button>
        <button type="button" title="没用"><ThumbsDown size={16} /></button>
      </div>
    </article>
  );
}

function ScoredFeedRow({ item }) {
  return (
    <article className={`scored-feed-row ${scoreTone(item.l2_score)}`}>
      <span className="score-rail small">{Math.round(item.l2_score)}</span>
      <div>
        <strong>{item.title}</strong>
        <p>{item.rationale_short || item.context_preview}</p>
      </div>
      <span>{item.primary_reason}</span>
      <FeedLinks item={item} />
    </article>
  );
}

function FeedEvidence({ item }) {
  return (
    <div className="feed-evidence">
      {(item.evidence_bullets || []).slice(0, 3).map((bullet) => (
        <span key={`${item.group_id}:${bullet.display_label || bullet.label}`}>
          {bullet.display_label || bullet.label}
        </span>
      ))}
    </div>
  );
}

function FeedLinks({ item }) {
  const links = item.source_links || [];
  return (
    <div className="feed-links">
      {item.canonical_link ? (
        <a href={item.canonical_link} target="_blank" rel="noreferrer">
          <ArrowSquareOut size={15} aria-hidden="true" /> 打开
        </a>
      ) : null}
      {links.slice(0, 3).map((link) => (
        <button type="button" key={`${item.group_id}:${link.item_id}:${link.channel}`}>
          <ChartLineUp size={15} aria-hidden="true" /> {link.channel_label}
        </button>
      ))}
    </div>
  );
}
```

3. In the existing Feed tab component, replace the locked daily branch:

```javascript
tab === 'daily'
  ? <DailyFeedView payload={payload} />
  : <CandidatePoolView ... />
```

Preserve the existing Candidate Pool table branch.

- [ ] **Step 6: Add Feed styles**

In `web/src/styles.css`, add:

```css
.daily-feed-shell {
  display: grid;
  gap: 18px;
  padding: 16px 0 40px;
}

.feed-run-strip {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 12px 14px;
  background: color-mix(in srgb, var(--surface) 92%, transparent);
}

.feed-run-strip div {
  display: grid;
  gap: 4px;
}

.feed-run-strip span {
  color: var(--muted);
  font-size: 12px;
}

.today-focus-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 14px;
}

.feed-signal-card {
  position: relative;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 16px;
  background:
    linear-gradient(135deg, color-mix(in srgb, var(--surface) 96%, transparent), var(--surface)),
    repeating-linear-gradient(90deg, transparent 0 18px, color-mix(in srgb, var(--text) 4%, transparent) 18px 19px);
  box-shadow: 0 14px 40px color-mix(in srgb, var(--text) 8%, transparent);
}

.feed-signal-card::after {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: linear-gradient(110deg, transparent 0%, color-mix(in srgb, var(--accent) 12%, transparent) 45%, transparent 70%);
  transform: translateX(-120%);
  animation: signal-sheen 7s ease-in-out infinite;
}

@keyframes signal-sheen {
  0%, 70% { transform: translateX(-120%); }
  100% { transform: translateX(120%); }
}

.signal-card-topline,
.feed-tags,
.feed-evidence,
.feed-links,
.feed-feedback {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.score-rail {
  display: inline-grid;
  place-items: center;
  min-width: 42px;
  height: 42px;
  border-radius: 999px;
  color: var(--text);
  background: color-mix(in srgb, var(--accent) 14%, var(--surface));
  border: 1px solid color-mix(in srgb, var(--accent) 32%, var(--border));
  font-weight: 700;
}

.score-rail.small {
  min-width: 34px;
  height: 34px;
  font-size: 12px;
}

.feed-tags span,
.feed-evidence span {
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 4px 8px;
  font-size: 12px;
  color: var(--muted);
  background: var(--surface);
}

.deepdive-summary {
  color: var(--text);
}

.feed-links a,
.feed-links button,
.feed-feedback button {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 5px 9px;
  background: var(--surface);
  color: var(--text);
  text-decoration: none;
}

.scored-feed-list {
  display: grid;
  border-top: 1px solid var(--border);
}

.scored-feed-row {
  display: grid;
  grid-template-columns: auto minmax(220px, 1fr) minmax(120px, auto) auto;
  gap: 12px;
  align-items: center;
  border-bottom: 1px solid var(--border);
  padding: 12px 0;
}

.scored-feed-row p {
  margin: 3px 0 0;
  color: var(--muted);
}

@media (prefers-reduced-motion: reduce) {
  .feed-signal-card::after {
    animation: none;
  }
}

@media (max-width: 760px) {
  .feed-run-strip,
  .scored-feed-row {
    grid-template-columns: 1fr;
    display: grid;
  }
}
```

Use existing CSS variables. If `--surface`, `--border`, `--text`, `--muted`, or `--accent` differ in this codebase, map to the existing variable names instead of adding a second palette.

- [ ] **Step 7: Run tests and build**

Run:

```bash
cd web && npm test && npm run build
```

Expected: PASS.

- [ ] **Step 8: Browser QA**

Start or reuse the app server:

```bash
python3 pipeline/server.py --host 127.0.0.1 --port 8787
cd web && npm run dev
```

Open:

```text
http://127.0.0.1:5173/?section=feed&feed=daily
```

Verify:

```text
Daily Feed no longer shows locked placeholder.
Missing-feed empty state is clear.
With test/real feed data, Today Focus cards render and text does not overlap.
Score pills, links, evidence pills, and feedback buttons are readable.
Animation is subtle and disabled by prefers-reduced-motion.
Candidate Pool tab still works.
```

- [ ] **Step 9: Commit**

```bash
git add web/package.json web/package-lock.json web/src/App.jsx web/src/styles.css web/src/dashboardModel.js web/src/feedModel.test.mjs
git commit -m "Add designed Daily Feed UI"
```

## Task 12: Settings And Run Controls

**Files:**
- Modify: `web/src/dashboardModel.js`
- Modify: `web/src/App.jsx`
- Modify: `pipeline/server.py`
- Test: `web/src/dashboardModel.test.mjs`
- Test: `tests/test_dashboard_data_api.py`

- [ ] **Step 1: Write failing settings tests**

Append to `web/src/dashboardModel.test.mjs`:

```javascript
test('settingsPanelDefs includes Layer 2 settings when config has layer2', () => {
  const panels = settingsPanelDefs({
    channels: [],
    source_errors: {},
    config_meta: { api_status: { kimi: {} } },
    config: {
      layer2: {
        enabled: true,
        edge_scout_model: 'kimi-k2.5',
        scoring_model: 'kimi-k2.5',
        deepdive_model: 'kimi-k2.6',
      },
    },
  });

  assert.ok(panels.some((panel) => panel.id === 'settings_layer2'));
});
```

Append to `tests/test_dashboard_data_api.py`:

```python
    def test_server_run_command_maps_layer2_options_to_daily_pipeline(self) -> None:
        import pipeline.server as server

        with mock.patch.object(server, "PYTHON", "py"):
            with mock.patch.object(server, "ROOT", Path("/repo")):
                command = server.build_run_command({
                    "run_layer2": True,
                    "layer2_scout_limit": 10,
                    "layer2_scoring_limit": 20,
                    "layer2_deepdive_limit": 2,
                })

        self.assertIn("--run-layer2", command)
        self.assertIn("--layer2-scout-limit", command)
        self.assertIn("10", command)
        self.assertIn("--layer2-scoring-limit", command)
        self.assertIn("20", command)
        self.assertIn("--layer2-deepdive-limit", command)
        self.assertIn("2", command)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_dashboard_data_api.py -q
cd web && npm test -- dashboardModel.test.mjs
```

Expected: FAIL because Layer 2 settings/run options are not exposed.

- [ ] **Step 3: Implement server run flags**

In `pipeline/server.py`, map:

```python
if options.get("run_layer2"):
    cmd.append("--run-layer2")
```

Extend integer options:

```python
        "layer2_scout_limit": "--layer2-scout-limit",
        "layer2_scoring_limit": "--layer2-scoring-limit",
        "layer2_deepdive_limit": "--layer2-deepdive-limit",
```

Extend string options:

```python
        "layer2_scout_model": "--layer2-scout-model",
        "layer2_scoring_model": "--layer2-scoring-model",
        "layer2_deepdive_model": "--layer2-deepdive-model",
```

- [ ] **Step 4: Implement settings panel model/UI**

In `web/src/dashboardModel.js`, add a `settings_layer2` panel in `settingsPanelDefs()` when `payload.config.layer2` exists:

```javascript
{
  id: 'settings_layer2',
  label: 'Layer 2 Feed',
  count: 0,
  description: 'Kimi scout/scoring/deepdive model and budget settings.',
}
```

In `web/src/App.jsx`, add Chinese labels for:

```text
启用 Layer 2 Feed
Edge Watch scout 模型
Scoring 模型
Deepdive 模型
Edge Watch scout 上限
Scoring 上限
每日 deepdive 上限
Deepdive 最低 L2 分
Kimi web search
```

The Run button payload should include:

```javascript
{
  run_layer2: Boolean(config.layer2?.enabled),
  layer2_scout_limit: Number(config.layer2?.max_edge_watch_scout || 50),
  layer2_scoring_limit: Number(config.layer2?.max_scored_candidates || 150),
  layer2_deepdive_limit: Number(config.layer2?.max_deepdives_per_run || 10),
  layer2_scout_model: String(config.layer2?.edge_scout_model || 'kimi-k2.5'),
  layer2_scoring_model: String(config.layer2?.scoring_model || 'kimi-k2.5'),
  layer2_deepdive_model: String(config.layer2?.deepdive_model || 'kimi-k2.6'),
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
python3 -m pytest tests/test_dashboard_data_api.py -q
cd web && npm test
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pipeline/server.py tests/test_dashboard_data_api.py web/src/dashboardModel.js web/src/App.jsx
git commit -m "Add Layer 2 settings controls"
```

## Task 13: Evals And Small Kimi Smoke

**Files:**
- Create: `pipeline/decision/run_layer2_evals.py`
- Test: `tests/test_layer2_evals.py`

- [ ] **Step 1: Write failing eval tests**

Create `tests/test_layer2_evals.py`:

```python
from __future__ import annotations

import unittest


class Layer2EvalTest(unittest.TestCase):
    def test_eval_fixture_scores_project_above_news(self):
        from pipeline.decision.run_layer2_evals import rank_eval_cases

        cases = [
            {"name": "Generic AI funding news", "l2_score": 42, "expected": "news"},
            {"name": "Repo-native agent workflow", "l2_score": 84, "expected": "project"},
        ]

        result = rank_eval_cases(cases)

        self.assertEqual(result["top"]["expected"], "project")
        self.assertTrue(result["ok"])
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_layer2_evals.py -q
```

Expected: FAIL because `run_layer2_evals` does not exist.

- [ ] **Step 3: Implement eval scaffold**

Create `pipeline/decision/run_layer2_evals.py`:

```python
from __future__ import annotations

import argparse
import json
import os
from typing import Any

from pipeline.decision.kimi_provider import KimiProvider


def rank_eval_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    ranked = sorted(cases, key=lambda row: -float(row.get("l2_score", 0)))
    top = ranked[0] if ranked else {}
    return {"ok": bool(top) and top.get("expected") == "project", "top": top, "ranked": ranked}


def run_smoke(model: str = "kimi-k2.5") -> dict[str, Any]:
    if not (os.environ.get("KIMI_API_KEY") or os.environ.get("MOONSHOT_API_KEY")):
        return {"ok": False, "skipped": True, "reason": "Kimi key not configured"}
    provider = KimiProvider(model=model, timeout=45, max_retries=1)
    response = provider.complete_json(
        task="layer2_eval_smoke",
        prompt_version="layer2-eval-smoke-v1",
        system_prompt="Return strict JSON with ok boolean and score number.",
        input_payload={
            "candidate": {
                "name": "Repo-native agent workflow",
                "evidence": ["GitHub repo with README", "HN product discussion"],
            }
        },
    )
    return {"ok": bool(response.get("ok", True)), "skipped": False, "shape": sorted(response.keys())}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Layer 2 evals")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--model", default="kimi-k2.5")
    args = parser.parse_args()
    result = run_smoke(args.model) if args.smoke else rank_eval_cases([
        {"name": "Generic AI funding news", "l2_score": 42, "expected": "news"},
        {"name": "Repo-native agent workflow", "l2_score": 84, "expected": "project"},
    ])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") or result.get("skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests and bounded Kimi smoke**

Run:

```bash
python3 -m pytest tests/test_layer2_evals.py -q
python3 -m pipeline.decision.run_layer2_evals
python3 -m pipeline.decision.run_layer2_evals --smoke --model kimi-k2.5
```

Expected:

```text
pytest passes.
non-smoke eval exits 0.
smoke either exits 0 with a bounded Kimi JSON shape summary or reports skipped because Kimi key is not configured.
```

- [ ] **Step 5: Commit**

```bash
git add pipeline/decision/run_layer2_evals.py tests/test_layer2_evals.py
git commit -m "Add Layer 2 eval harness"
```

## Task 14: End-to-End Verification

**Files:**
- No new files required.

- [ ] **Step 1: Run backend tests**

Run:

```bash
python3 -m pytest tests -q
```

Expected: PASS.

- [ ] **Step 2: Run frontend tests and build**

Run:

```bash
cd web && npm test && npm run build
```

Expected: PASS.

- [ ] **Step 3: Run small fake-provider Layer 2 fixture path**

Run:

```bash
python3 -m pytest tests/test_run_layer2_feed.py tests/test_feed_api.py -q
```

Expected: PASS and verifies DB writes plus API shape.

- [ ] **Step 4: Run bounded real Kimi smoke**

Run:

```bash
python3 -m pipeline.decision.run_layer2_evals --smoke --model kimi-k2.5
```

Expected: Bounded JSON-shape result or explicit skipped output if Kimi key is not configured in the current shell.

- [ ] **Step 5: Run local app**

Run backend:

```bash
python3 pipeline/server.py --host 127.0.0.1 --port 8787
```

Run frontend:

```bash
cd web && VITE_API_BASE=http://127.0.0.1:8787 npm run dev
```

Open:

```text
http://127.0.0.1:5173/?section=feed&feed=daily
```

Verify:

```text
Daily Feed loads from /api/feed or embedded dashboard feed.
Candidate Pool still works.
Sources table still works.
Settings still works and exposes Layer 2 controls.
No token or secret is printed in UI, server response, logs, or test output.
```

- [ ] **Step 6: Commit final fixes**

If any verification fix was needed:

```bash
git add <changed-files>
git commit -m "Verify Layer 2 feed pipeline"
```

If no changes were needed, do not create an empty commit.

## Self-Review

- Spec coverage: The plan covers schema, Kimi provider, presentation grouping, Layer2 eligibility/scheduler, Edge Watch Scout, scoring/aggregation, deepdive selection/harness, Feed API, settings, designed Feed UI, evals, and verification.
- Scope guard: The plan does not implement Layer 3 chatbot, rule/prompt editor, hosted auth, OS cron installation, or permanent LLM-only entity merge.
- Provider routing: Layer 2 tasks use Kimi. Existing DeepSeek classifier code is not routed into Feed scoring/deepdive.
- TDD check: Every implementation task begins with a failing test, then minimal implementation, passing test, and commit.
- UI check: The Feed UI plan preserves the existing workspace shell, uses tasteskill constraints, avoids generic AI gradients, avoids nested cards, and keeps Candidate Pool as a table.
