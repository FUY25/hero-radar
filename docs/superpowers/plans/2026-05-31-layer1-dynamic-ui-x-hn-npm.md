# Layer 1 Dynamic UI, X, HN, And NPM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static dashboard with a dynamic React UI that preserves the old source dashboard behavior, then add bounded LLM-assisted HN/X source classifiers and npm registry backfill as Layer 1 evidence producers.

**Architecture:** Keep Layer 2 Feed selection, chatbot, Kimi deepdive, cron, and rule editing out of this slice. Add server-side dashboard data APIs over the existing SQLite/source scoring code, then migrate the existing dashboard UI behavior into React without fallback static HTML. Add shared versioned LLM cache/provider code used by HN and X classifiers; classifiers only write auditable evidence rows, merge proposals, alias links, and backfill jobs that the deterministic rules engine consumes.

**Tech Stack:** Python stdlib (`unittest`, `sqlite3`, `urllib.request`, `http.server`), existing `pipeline/run_pipeline.py` source scoring helpers, React/Vite, Node `node:test`, DeepSeek OpenAI-compatible API for bounded smoke tests, existing SQLite `data/hero_radar.sqlite`.

---

## Source Documents Read

- `/Users/fuyuming/Documents/Hero radar/docs/decision-layer-v1.md`: source of truth for Layer 0/1/2 boundaries, X §4.4, npm rules, LLM harness, and UI placement.
- `/Users/fuyuming/Documents/Hero radar/docs/superpowers/plans/2026-05-31-pre-layer2-decision-pipeline.md`: source of truth for what is already implemented and what was explicitly moved out.
- DeepSeek official API docs, checked on 2026-05-31: OpenAI-compatible base URL is `https://api.deepseek.com`; current model names are `deepseek-v4-flash` and `deepseek-v4-pro`; `deepseek-chat` and `deepseek-reasoner` are legacy names scheduled for deprecation on 2026-07-24. JSON mode requires `response_format: {"type":"json_object"}` and the prompt must explicitly ask for JSON.

## Scope Boundaries

Included:

- Dynamic `/api/dashboard-data` for the current source dashboard payload.
- React migration of old source tabs/table/detail/settings layout and interactions with the same data semantics.
- Existing `Feed` top-level section with `Daily Feed` locked and `Candidate Pool` dynamic inside Feed.
- Shared LLM provider/cache/prompt-version infrastructure.
- HN projectness/entity extraction classifier for HN-derived candidates.
- X two-stage LLM classifier that emits `x_social` evidence.
- npm package metadata/download backfill that emits `package_family` evidence.

Excluded:

- Layer 2 Daily Feed selection and buckets (`today_focus`, `secondary`, `backlog`, `suppress`).
- Layer 2 user-facing card analysis, Kimi/Moonshot deepdive, chatbot, cron, and rule editor UI.
- Auto-merging fuzzy HN/X proposals. This slice writes proposals; only deterministic high-confidence links may update `alias_links`.
- Full historical backfills over all rows. Every external/API/LLM operation is bounded by explicit limits.

## File Ownership For Parallel Agents

- **Dashboard/API/UI agent:** `pipeline/dashboard_data.py`, `pipeline/server.py`, `tests/test_dashboard_data_api.py`, all `web/src/*`, `web/package.json`.
- **LLM/HN/X agent:** `pipeline/decision/llm_cache.py`, `pipeline/decision/llm_provider.py`, `pipeline/decision/hn_classifier.py`, `pipeline/decision/x_classifier.py`, `tests/test_llm_cache.py`, `tests/test_llm_provider.py`, `tests/test_hn_classifier.py`, `tests/test_x_classifier.py`.
- **NPM/rules agent:** `pipeline/decision/npm_backfill.py`, `tests/test_npm_backfill.py`, focused changes in `pipeline/decision/rules.py`, `pipeline/rules.json`.
- **Coordinator only:** `pipeline/decision/schema.py` and `pipeline/decision/run_decision.py`, because these files are shared integration points.

All agents must work in `/Users/fuyuming/Documents/Hero radar` on `main`; do not use `.claude/worktrees/pre-layer2-decision-pipeline`.

## Data Contracts

### `/api/dashboard-data`

Response shape:

```json
{
  "run_id": "20260531T073537Z",
  "fetched_at": "2026-05-31T07:35:37Z",
  "source_errors": {"github_trending": null},
  "channel_counts": {"github_trending": 239},
  "channels": [{"id": "github_trending", "label": "GitHub Trending", "count": 239, "description": "..."}],
  "settings_channels": [{"id": "settings_source_health", "label": "Source Health", "count": 13, "description": "..."}],
  "window_counts": {"24h": 291, "current": 2238},
  "config": {"github_trending": {"periods": ["daily"]}},
  "config_meta": {"default_schedule": "24h", "cron_enabled": false, "takes_effect": "next pipeline run", "api_status": {"github": {"configured": true}}},
  "items": [{"item_id": 1, "channel": "github_trending", "name": "owner/repo", "native_metric": {"label": "本窗口新增 star", "value": 120}}],
  "candidates": {"run_id": "decision_20260531", "candidates": [], "edge_watch": []}
}
```

Rules:

- No secrets in `config_meta.api_status`; only boolean `configured` and env var name.
- `items` must match the old dashboard `export_dashboard_v3` item semantics: `channel`, `channel_label`, `source`, `external_id`, `native_metric`, `facts`, `metadata`, `raw`, `window`, `rank`, `channel_rank`, `window_rank`.
- `settings_channels` and settings rows must match old Settings source health/search terms behavior.
- Source rows come from latest successful snapshot per source, not from static `data/exports/dashboard.html`.

### LLM Cache

Table `llm_cache`:

```sql
create table if not exists llm_cache (
    cache_key text primary key,
    provider text not null,
    model text not null,
    prompt_version text not null,
    task text not null,
    input_hash text not null,
    request_json text not null,
    response_json text not null,
    status text not null,
    created_at text not null,
    expires_at text,
    error text
);
```

`cache_key = sha256(provider + model + prompt_version + task + input_hash)`.

### HN Classifier Output

```json
{
  "item_id": 101,
  "projectness": "project",
  "confidence": 0.91,
  "canonical_name": "Clawdbot",
  "deterministic_links": [{"type": "github", "key": "github:owner/clawdbot", "url": "https://github.com/owner/clawdbot"}],
  "proposed_links": [{"type": "domain", "key": "domain:clawdbot.dev", "url": "https://clawdbot.dev", "confidence": 0.72}],
  "summary": "Show HN launch for an AI coding assistant."
}
```

Allowed `projectness`: `project`, `package`, `company_product`, `news_article`, `topic_discussion`, `research_paper`, `unknown`.

### X Classifier Output

Stage 1 batched triage output:

```json
{
  "triage": [
    {
      "tweet_id": "t1",
      "about_concrete_project": true,
      "closer_look": true,
      "project_refs": [
        {
          "entity_key": "github:owner/repo",
          "entity_name": "owner/repo",
          "entity_confidence": "linked",
          "confidence": 0.86
        }
      ],
      "expression_strength": "recommendation",
      "evidence_quote": "new agent repo",
      "reason": "The tweet links a concrete repo and recommends it."
    }
  ]
}
```

Stage 2 per-entity tier output:

```json
{
  "entity_key": "github:owner/repo",
  "x_tier": "potential",
  "entity_confidence": "linked",
  "x_expression_strength": "recommendation",
  "cited_tweet_ids": ["t1", "t2"],
  "rationale": "Two credible seed authors independently mention a concrete repo within 24h.",
  "cross_source_notes": ["GitHub evidence already exists"]
}
```

Allowed `x_tier`: `none`, `watch`, `potential`, `high`.

Rules consumption:

- Stage 1 only filters and normalizes; it does not set levels.
- Stage 2 writes evidence rows. The deterministic rules consume accepted `x_tier` evidence as source-family votes.
- `x_tier` is ignored if `cited_tweet_ids` is empty.
- `potential` requires `entity_confidence` in `linked` or `exact_handle`; fuzzy name-only X output can only become watch unless verified cross-source evidence is already present.
- `high` requires citations plus a linked/exact entity and either a larger independent-author burst or verified non-X corroboration.

### NPM Backfill Output

`npm_backfill.py` writes `evidence_rows` with:

- `source = npm_registry`
- `family = package_family`
- `metric_name` in `daily_downloads`, `downloads_7d`, `downloads_30d`, `npm_repository_link`
- `raw_url_or_ref` set to the npm package URL or repository URL

Rules consumption:

- `daily_downloads >= 10000` and rising versus the 7-day average can reach `potential`.
- `daily_downloads >= 100000` can reach `high_potential`.
- If 7-day data is missing in a test/fake client, the implementation treats the single daily value as enough to write evidence but not enough to prove sustained growth beyond the configured daily threshold.

### Classifier Evidence Injection

The deterministic rule evaluator must not call an LLM inline. HN/X/npm producers run before the final rules pass and provide classifier/backfill outputs as data. Implementation can do either of these, but tests must cover the chosen path:

- Write synthetic rows into the in-memory rows passed to `evaluate_entities`, with `source`/`metadata` representing the classifier output.
- Or extend `evaluate_entities` with an explicit `classifier_evidence` argument loaded from DB.

Do not rely on DB evidence rows being magically consumed by `evaluate_entities`; this plan requires an explicit data path.

## Task 1: Dynamic Dashboard Data API

**Files:**
- Create: `pipeline/dashboard_data.py`
- Modify: `pipeline/server.py`
- Test: `tests/test_dashboard_data_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dashboard_data_api.py` with:

```python
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class DashboardDataApiTest(unittest.TestCase):
    def make_db(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp = tempfile.TemporaryDirectory()
        db_path = Path(temp.name) / "hero.sqlite"
        conn = sqlite3.connect(db_path)
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
                url text not null,
                fetched_at text not null,
                heat real,
                velocity real,
                acceleration real,
                source_rank integer,
                description text,
                metadata_json text not null,
                raw_json text not null
            );
            create table scores (
                run_id text not null,
                item_id integer not null,
                rank integer not null,
                score real not null,
                components_json text not null
            );
            """
        )
        conn.execute(
            "insert into snapshots(run_id, source, fetched_at, status, item_count, error) values (?, ?, ?, ?, ?, ?)",
            ("source-run", "github_trending", "2026-05-31T10:00:00Z", "ok", 1, None),
        )
        snapshot_id = conn.execute("select id from snapshots").fetchone()[0]
        conn.execute(
            """
            insert into items(run_id, snapshot_id, source, external_id, name, url, fetched_at, heat, velocity, acceleration, source_rank, description, metadata_json, raw_json)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "source-run",
                snapshot_id,
                "github_trending",
                "daily:owner/repo",
                "owner/repo",
                "https://github.com/owner/repo",
                "2026-05-31T10:00:00Z",
                None,
                None,
                None,
                1,
                "Repo description",
                json.dumps({"period": "daily", "period_stars": 321, "stars_total": 999}, ensure_ascii=False),
                json.dumps({"full_name": "owner/repo"}, ensure_ascii=False),
            ),
        )
        conn.commit()
        conn.close()
        return temp, db_path

    def test_build_dashboard_data_uses_latest_source_rows_and_settings(self):
        from pipeline.dashboard_data import build_dashboard_data

        temp, db_path = self.make_db()
        self.addCleanup(temp.cleanup)
        config = {
            "github_trending": {"periods": ["daily"], "languages": [""]},
            "github_search": {"queries": [{"label": "agent", "query": "agent stars:>20"}]},
            "hn": {"algolia_queries": [{"label": "agent", "query": "agent"}]},
            "npm": {"queries": [{"label": "mcp", "query": "mcp"}]},
            "apify": {"enabled": False, "x_keyword_queries": ["agent workflow"]},
        }

        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "present"}, clear=False):
            payload = build_dashboard_data(db_path=db_path, config=config)

        self.assertEqual(payload["run_id"], "source-run")
        self.assertEqual(payload["fetched_at"], "2026-05-31T10:00:00Z")
        self.assertEqual(payload["channel_counts"]["github_trending"], 1)
        self.assertEqual(payload["channels"][0]["id"], "github_trending")
        self.assertEqual(payload["items"][0]["name"], "owner/repo")
        self.assertEqual(payload["items"][0]["native_metric"]["value"], 321)
        self.assertIn("settings_source_health", payload["channel_counts"])
        self.assertTrue(payload["config_meta"]["api_status"]["github"]["configured"])
        self.assertNotIn("present", json.dumps(payload, ensure_ascii=False))

    def test_server_exposes_dashboard_data_endpoint(self):
        import pipeline.server as server

        temp, db_path = self.make_db()
        self.addCleanup(temp.cleanup)
        with mock.patch.object(server, "DB_PATH", db_path):
            with mock.patch.object(server, "read_json", return_value={
                "github_trending": {"periods": ["daily"], "languages": [""]},
                "github_search": {"queries": []},
                "hn": {"algolia_queries": []},
                "npm": {"queries": []},
                "apify": {"enabled": False, "x_keyword_queries": []},
            }):
                payload = server.query_dashboard_data_payload()

        self.assertEqual(payload["run_id"], "source-run")
        self.assertEqual(payload["items"][0]["channel"], "github_trending")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_dashboard_data_api -v
```

Expected: FAIL because `pipeline.dashboard_data` and `query_dashboard_data_payload` do not exist.

- [ ] **Step 3: Write minimal implementation**

Create `pipeline/dashboard_data.py` with these functions:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from pipeline.run_pipeline import (
    CHANNEL_ORDER,
    SOURCE_DASHBOARD_HIDDEN_CHANNELS,
    api_status_payload,
    channel_description,
    channel_label,
    latest_source_errors,
    rank_latest_by_item_source,
    settings_rows_from_config,
)


def latest_run_meta(conn: sqlite3.Connection) -> dict[str, str]:
    row = conn.execute(
        """
        select run_id, fetched_at
        from snapshots
        where status = 'ok'
        order by id desc
        limit 1
        """
    ).fetchone()
    return {"run_id": row[0], "fetched_at": row[1]} if row else {"run_id": "", "fetched_at": ""}


def build_dashboard_data(*, db_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        meta = latest_run_meta(conn)
        scored = rank_latest_by_item_source(conn, meta["run_id"]) if meta["run_id"] else []
        source_errors = latest_source_errors(conn) if meta["run_id"] else {}
        display_rows = scored + settings_rows_from_config(config, source_errors, meta["fetched_at"])
        channel_counts: dict[str, int] = {}
        window_counts: dict[str, int] = {}
        for row in display_rows:
            channel_counts[row["channel"]] = channel_counts.get(row["channel"], 0) + 1
            window = row.get("window") or "current"
            window_counts[window] = window_counts.get(window, 0) + 1

        channels = [
            {
                "id": channel,
                "label": channel_label(channel),
                "count": channel_counts.get(channel, 0),
                "description": channel_description(channel),
            }
            for channel in CHANNEL_ORDER
            if channel_counts.get(channel, 0) and channel not in SOURCE_DASHBOARD_HIDDEN_CHANNELS
        ]
        settings_channels = [
            {
                "id": channel,
                "label": channel_label(channel),
                "count": channel_counts.get(channel, 0),
                "description": channel_description(channel),
            }
            for channel in ("settings_source_health", "settings_search_terms", "x_seed_accounts")
            if channel_counts.get(channel, 0)
        ]
        return {
            "run_id": meta["run_id"],
            "fetched_at": meta["fetched_at"],
            "source_errors": source_errors,
            "channel_counts": channel_counts,
            "channels": channels,
            "settings_channels": settings_channels,
            "window_counts": window_counts,
            "config": config,
            "config_meta": {
                "default_schedule": "24h",
                "cron_enabled": False,
                "takes_effect": "next pipeline run",
                "api_status": api_status_payload(),
            },
            "items": display_rows,
        }
    finally:
        conn.close()
```

Modify `pipeline/server.py`:

```python
from pipeline.dashboard_data import build_dashboard_data
```

Add:

```python
def query_dashboard_data_payload() -> dict[str, Any]:
    payload = build_dashboard_data(db_path=DB_PATH, config=read_json(CONFIG_PATH))
    with connect_decision_db() as conn:
        run_id = query_latest_decision_run(conn) or ""
        payload["candidates"] = query_candidates(conn, run_id) if run_id else {"run_id": "", "candidates": [], "edge_watch": []}
    return payload
```

In `HeroRadarHandler.do_GET`, before `/api/candidates`:

```python
if path == "/api/dashboard-data":
    json_response(self, query_dashboard_data_payload(), cors=True)
    return
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python3 -m unittest tests.test_dashboard_data_api -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/dashboard_data.py pipeline/server.py tests/test_dashboard_data_api.py
git commit -m "feat: add dynamic dashboard data api"
```

## Task 2: React Dashboard Model Tests

**Files:**
- Create: `web/src/dashboardModel.js`
- Create: `web/src/dashboardModel.test.mjs`
- Modify: `web/package.json`

- [ ] **Step 1: Write the failing test**

Create `web/src/dashboardModel.test.mjs`:

```javascript
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  activeChannelList,
  detailRowsForItem,
  filterAndSortRows,
  initialDashboardState,
  visibleWindowsForChannel,
} from './dashboardModel.js';

const payload = {
  channels: [
    { id: 'github_trending', label: 'GitHub Trending', count: 2 },
    { id: 'hn_search', label: 'HN Search', count: 1 },
  ],
  settings_channels: [
    { id: 'settings_source_health', label: 'Source Health', count: 1 },
  ],
  items: [
    { item_id: 1, channel: 'github_trending', name: 'b/repo', description: 'B', window: '24h', channel_rank: 2, window_rank: 2, native_metric: { value: 10 }, metadata: { period_stars: 10 }, raw: {} },
    { item_id: 2, channel: 'github_trending', name: 'a/repo', description: 'A', window: '7d', channel_rank: 1, window_rank: 1, native_metric: { value: 20 }, metadata: { period_stars: 20 }, raw: {} },
    { item_id: -1, channel: 'settings_source_health', name: 'github', description: '正常', window: 'current', channel_rank: 1, metadata: { status: '正常' }, raw: {} },
  ],
};

test('initialDashboardState starts on first source channel', () => {
  const state = initialDashboardState(payload);
  assert.equal(state.section, 'sources');
  assert.equal(state.activeChannel, 'github_trending');
  assert.equal(state.activeSettings, 'settings_source_health');
});

test('activeChannelList switches between sources and settings', () => {
  assert.deepEqual(activeChannelList(payload, 'sources').map((row) => row.id), ['github_trending', 'hn_search']);
  assert.deepEqual(activeChannelList(payload, 'settings').map((row) => row.id), ['settings_source_health']);
});

test('visibleWindowsForChannel returns stable window order', () => {
  assert.deepEqual(visibleWindowsForChannel(payload.items, 'github_trending'), ['24h', '7d']);
});

test('filterAndSortRows filters by channel and window and supports name sort', () => {
  const rows = filterAndSortRows(payload.items, {
    activeChannel: 'github_trending',
    activeWindow: 'all',
    query: '',
    sort: 'name',
    sortDir: 'asc',
  });
  assert.deepEqual(rows.map((row) => row.name), ['a/repo', 'b/repo']);
});

test('detailRowsForItem exposes metadata and raw fields for detail panel', () => {
  const rows = detailRowsForItem(payload.items[0]);
  assert.deepEqual(rows.map((row) => row.key), ['metadata.period_stars', 'raw']);
});
```

Modify `web/package.json` scripts:

```json
{
  "scripts": {
    "dev": "vite --host 127.0.0.1 --port 5173",
    "build": "vite build",
    "test": "node --test src/*.test.mjs",
    "preview": "vite preview --host 127.0.0.1 --port 4173"
  }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd web && npm test
```

Expected: FAIL because `dashboardModel.js` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `web/src/dashboardModel.js`:

```javascript
const WINDOW_ORDER = new Map([['24h', 0], ['7d', 1], ['30d', 2], ['30d+', 3], ['current', 4]]);

export function initialDashboardState(payload) {
  return {
    section: 'sources',
    activeChannel: payload.channels?.[0]?.id || '',
    activeSettings: payload.settings_channels?.[0]?.id || '',
    activeWindow: 'all',
    query: '',
    sort: 'native',
    sortDir: 'asc',
    selectedItemId: null,
    railCollapsed: false,
    theme: 'light',
  };
}

export function activeChannelList(payload, section) {
  return section === 'settings' ? (payload.settings_channels || []) : (payload.channels || []);
}

export function visibleWindowsForChannel(items, channel) {
  const windows = new Set();
  for (const item of items || []) {
    if (item.channel === channel) windows.add(item.window || 'current');
  }
  return [...windows].sort((a, b) => (WINDOW_ORDER.get(a) ?? 99) - (WINDOW_ORDER.get(b) ?? 99));
}

function searchableText(row) {
  return [row.name, row.description, row.external_id, ...(row.facts || [])].join(' ').toLowerCase();
}

export function filterAndSortRows(items, state) {
  const query = (state.query || '').trim().toLowerCase();
  const rows = (items || []).filter((item) => {
    if (item.channel !== state.activeChannel) return false;
    if (state.activeWindow && state.activeWindow !== 'all' && (item.window || 'current') !== state.activeWindow) return false;
    return !query || searchableText(item).includes(query);
  });
  const dir = state.sortDir === 'desc' ? -1 : 1;
  return rows.sort((a, b) => {
    if (state.sort === 'name') return String(a.name || '').localeCompare(String(b.name || '')) * dir;
    if (state.sort === 'metric') return ((Number(a.native_metric?.value) || 0) - (Number(b.native_metric?.value) || 0)) * dir;
    return ((a.window_rank || a.channel_rank || 0) - (b.window_rank || b.channel_rank || 0)) * dir;
  });
}

export function detailRowsForItem(item) {
  if (!item) return [];
  const rows = [];
  for (const [key, value] of Object.entries(item.metadata || {})) {
    rows.push({ key: `metadata.${key}`, value });
  }
  rows.push({ key: 'raw', value: item.raw || {} });
  return rows;
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
git add web/package.json web/src/dashboardModel.js web/src/dashboardModel.test.mjs
git commit -m "test: add dashboard model behavior"
```

## Task 3: React 1:1 Dashboard Migration Shell

**Files:**
- Modify: `web/src/App.jsx`
- Modify: `web/src/styles.css`
- Test: `web/src/dashboardModel.test.mjs`

- [ ] **Step 1: Write the failing test**

Append to `web/src/dashboardModel.test.mjs`:

```javascript
import { candidateRowsForFeed, workspaceSections } from './dashboardModel.js';

test('workspaceSections keeps old top-level surfaces and feed candidate tab', () => {
  assert.deepEqual(workspaceSections().map((row) => row.id), ['explore', 'feed', 'sources', 'settings']);
});

test('candidateRowsForFeed merges potential and edge watch rows', () => {
  const candidates = {
    candidates: [{ entity_id: 'entity:1', canonical_entity: 'Repo', level: 'potential', fired_families: ['github'] }],
    edge_watch: [{ entity_id: 'entity:2', canonical_entity: 'Topic', reasons: ['hn'], status: 'open' }],
  };
  assert.deepEqual(candidateRowsForFeed(candidates).map((row) => row.level), ['potential', 'edge_watch']);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd web && npm test
```

Expected: FAIL because `workspaceSections` and `candidateRowsForFeed` do not exist.

- [ ] **Step 3: Write minimal implementation**

Add to `web/src/dashboardModel.js`:

```javascript
export function workspaceSections() {
  return [
    { id: 'explore', label: 'Explore', enabled: false },
    { id: 'feed', label: 'Feed', enabled: true },
    { id: 'sources', label: 'Sources', enabled: true },
    { id: 'settings', label: 'Settings', enabled: true },
  ];
}

export function candidateRowsForFeed(candidates) {
  return [
    ...(candidates?.candidates || []).map((row) => ({ ...row, pool_type: row.level })),
    ...(candidates?.edge_watch || []).map((row) => ({ ...row, level: 'edge_watch', pool_type: 'edge_watch' })),
  ];
}
```

Replace `web/src/App.jsx` with a React implementation that:

- Fetches `/api/dashboard-data` once from `VITE_API_BASE`.
- Uses `workspaceSections()` for the rail.
- Defaults to `Sources` after data loads.
- Preserves old source tabs, settings subrail, channel tabs, search, window filter, sort selector, table, and detail panel.
- Keeps Feed with inner tabs `Daily Feed` and `Candidate Pool`; Daily Feed remains locked because Layer 2 is out of scope.
- Does not read `data/exports/dashboard.html`.

`web/src/styles.css` should be migrated from `export_dashboard_v3` styles in `pipeline/run_pipeline.py`, preserving the old Notion-like layout, rail collapse, settings subrail, table density, detail block, light/dark variables, and responsive behavior. Keep class names aligned where practical: `.app-shell`, `.rail`, `.workspace-tabs`, `.settings-subrail`, `.channel-tabs`, `.table-wrap`, `.detail-block`, `.settings-panel`.

- [ ] **Step 4: Run tests and build**

Run:

```bash
cd web && npm test && npm run build
```

Expected: PASS and Vite build succeeds.

- [ ] **Step 5: Visual smoke**

Run:

```bash
python3 pipeline/server.py --host 127.0.0.1 --port 8787
cd web && VITE_API_BASE=http://127.0.0.1:8787 npm run dev
```

Open `http://127.0.0.1:5173/` and verify:

- Rail has Explore, Feed, Sources, Settings.
- Sources tab shows old source channel tabs/table/detail behavior.
- Settings tab shows subrail and source health/search term tables.
- Feed tab has Daily Feed locked and Candidate Pool table.

- [ ] **Step 6: Commit**

```bash
git add web/src/App.jsx web/src/styles.css web/src/dashboardModel.js web/src/dashboardModel.test.mjs
git commit -m "feat: migrate source dashboard to react"
```

## Task 4: LLM Cache Schema And Helpers

**Files:**
- Modify: `pipeline/decision/schema.py`
- Create: `pipeline/decision/llm_cache.py`
- Test: `tests/test_llm_cache.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_llm_cache.py`:

```python
from __future__ import annotations

import sqlite3
import unittest

from pipeline.decision.schema import init_decision_db


class LlmCacheTest(unittest.TestCase):
    def test_cache_key_is_stable_and_prompt_version_scoped(self):
        from pipeline.decision.llm_cache import cache_key_for

        key_a = cache_key_for(provider="deepseek", model="deepseek-v4-flash", prompt_version="x-v1", task="x_stage1", input_payload={"b": 2, "a": 1})
        key_b = cache_key_for(provider="deepseek", model="deepseek-v4-flash", prompt_version="x-v1", task="x_stage1", input_payload={"a": 1, "b": 2})
        key_c = cache_key_for(provider="deepseek", model="deepseek-v4-flash", prompt_version="x-v2", task="x_stage1", input_payload={"a": 1, "b": 2})
        self.assertEqual(key_a, key_b)
        self.assertNotEqual(key_a, key_c)

    def test_get_or_store_llm_cache_round_trips_json(self):
        from pipeline.decision.llm_cache import get_cached_response, store_cached_response

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        key = store_cached_response(
            conn,
            provider="deepseek",
            model="deepseek-v4-flash",
            prompt_version="hn-v1",
            task="hn_classifier",
            input_payload={"item_id": 1},
            request_payload={"messages": []},
            response_payload={"projectness": "project"},
            status="ok",
        )

        cached = get_cached_response(conn, key)
        self.assertEqual(cached["response_json"]["projectness"], "project")
        self.assertEqual(cached["prompt_version"], "hn-v1")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_llm_cache -v
```

Expected: FAIL because `pipeline.decision.llm_cache` and the table do not exist.

- [ ] **Step 3: Write minimal implementation**

Add the `llm_cache` table SQL from the Data Contracts section to `DECISION_SCHEMA_SQL`.

Create `pipeline/decision/llm_cache.py`:

```python
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from pipeline.decision.schema import utc_now


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def input_hash_for(input_payload: Any) -> str:
    return hashlib.sha256(canonical_json(input_payload).encode("utf-8")).hexdigest()


def cache_key_for(*, provider: str, model: str, prompt_version: str, task: str, input_payload: Any) -> str:
    raw = "|".join([provider, model, prompt_version, task, input_hash_for(input_payload)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def store_cached_response(
    conn: sqlite3.Connection,
    *,
    provider: str,
    model: str,
    prompt_version: str,
    task: str,
    input_payload: Any,
    request_payload: Any,
    response_payload: Any,
    status: str,
    error: str | None = None,
) -> str:
    key = cache_key_for(provider=provider, model=model, prompt_version=prompt_version, task=task, input_payload=input_payload)
    conn.execute(
        """
        insert into llm_cache(cache_key, provider, model, prompt_version, task, input_hash, request_json, response_json, status, created_at, expires_at, error)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, null, ?)
        on conflict(cache_key) do update set
            response_json = excluded.response_json,
            status = excluded.status,
            error = excluded.error
        """,
        (
            key,
            provider,
            model,
            prompt_version,
            task,
            input_hash_for(input_payload),
            canonical_json(request_payload),
            canonical_json(response_payload),
            status,
            utc_now(),
            error,
        ),
    )
    conn.commit()
    return key


def get_cached_response(conn: sqlite3.Connection, cache_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        select cache_key, provider, model, prompt_version, task, input_hash, request_json, response_json, status, created_at, expires_at, error
        from llm_cache
        where cache_key = ?
        """,
        (cache_key,),
    ).fetchone()
    if not row:
        return None
    return {
        "cache_key": row[0],
        "provider": row[1],
        "model": row[2],
        "prompt_version": row[3],
        "task": row[4],
        "input_hash": row[5],
        "request_json": json.loads(row[6]),
        "response_json": json.loads(row[7]),
        "status": row[8],
        "created_at": row[9],
        "expires_at": row[10],
        "error": row[11],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python3 -m unittest tests.test_llm_cache -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/decision/schema.py pipeline/decision/llm_cache.py tests/test_llm_cache.py
git commit -m "feat: add llm cache"
```

## Task 5: DeepSeek Provider With Fake Provider Tests

**Files:**
- Create: `pipeline/decision/llm_provider.py`
- Test: `tests/test_llm_provider.py`

- [ ] **Step 1: Verify current DeepSeek API docs**

Search/open official docs only:

```bash
python3 - <<'PY'
print("Use official docs at https://api-docs.deepseek.com/ before coding provider defaults.")
PY
```

Implementation must use the current official base URL/model names verified at execution time. Per the source documents read for this plan, default to `deepseek-v4-flash` for cheap batched pipeline classification and pass `deepseek-v4-pro` explicitly for per-entity project/tier judgment.

- [ ] **Step 2: Write the failing test**

Create `tests/test_llm_provider.py`:

```python
from __future__ import annotations

import unittest


class LlmProviderTest(unittest.TestCase):
    def test_fake_provider_returns_json_objects_in_order(self):
        from pipeline.decision.llm_provider import FakeLLMProvider

        provider = FakeLLMProvider([{"ok": True}, {"ok": False}])
        self.assertEqual(provider.complete_json(task="a", prompt_version="v1", input_payload={"n": 1})["ok"], True)
        self.assertEqual(provider.complete_json(task="b", prompt_version="v1", input_payload={"n": 2})["ok"], False)

    def test_deepseek_provider_builds_openai_compatible_payload_without_secret_in_repr(self):
        from pipeline.decision.llm_provider import DeepSeekProvider

        provider = DeepSeekProvider(api_key="secret-value", model="deepseek-v4-flash", base_url="https://api.deepseek.com")
        payload = provider.build_payload(
            system_prompt="Return JSON.",
            user_payload={"hello": "world"},
            temperature=0,
        )
        self.assertEqual(payload["model"], "deepseek-v4-flash")
        self.assertEqual(payload["response_format"]["type"], "json_object")
        self.assertNotIn("secret-value", repr(provider))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_llm_provider -v
```

Expected: FAIL because `llm_provider.py` does not exist.

- [ ] **Step 4: Write minimal implementation**

Create `pipeline/decision/llm_provider.py`:

```python
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Protocol


class LLMProvider(Protocol):
    provider_name: str
    model: str

    def complete_json(self, *, task: str, prompt_version: str, input_payload: dict[str, Any], system_prompt: str = "") -> dict[str, Any]:
        ...


class FakeLLMProvider:
    provider_name = "fake"
    model = "fake-json"

    def __init__(self, responses: list[dict[str, Any]]):
        self._responses = list(responses)

    def complete_json(self, *, task: str, prompt_version: str, input_payload: dict[str, Any], system_prompt: str = "") -> dict[str, Any]:
        if not self._responses:
            raise RuntimeError("FakeLLMProvider has no responses left")
        return self._responses.pop(0)


class DeepSeekProvider:
    provider_name = "deepseek"

    def __init__(self, *, api_key: str | None = None, model: str | None = None, base_url: str | None = None, timeout: int = 60):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.model = model or os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
        self.base_url = (base_url or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")).rstrip("/")
        self.timeout = timeout

    def __repr__(self) -> str:
        return f"DeepSeekProvider(model={self.model!r}, base_url={self.base_url!r}, api_key_configured={bool(self.api_key)})"

    def build_payload(self, *, system_prompt: str, user_payload: dict[str, Any], temperature: float = 0) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt or "Return strict JSON only."},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, sort_keys=True)},
            ],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }

    def complete_json(self, *, task: str, prompt_version: str, input_payload: dict[str, Any], system_prompt: str = "") -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured")
        payload = self.build_payload(system_prompt=system_prompt, user_payload=input_payload, temperature=0)
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        return json.loads(content)
```

- [ ] **Step 5: Run tests**

Run:

```bash
python3 -m unittest tests.test_llm_provider -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pipeline/decision/llm_provider.py tests/test_llm_provider.py
git commit -m "feat: add llm provider abstraction"
```

## Task 6: HN Projectness And Entity Extraction Classifier

**Files:**
- Create: `pipeline/decision/hn_classifier.py`
- Modify: `pipeline/decision/schema.py`
- Test: `tests/test_hn_classifier.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_hn_classifier.py`:

```python
from __future__ import annotations

import json
import sqlite3
import unittest

from pipeline.decision.llm_provider import FakeLLMProvider
from pipeline.decision.schema import init_decision_db


class HnClassifierTest(unittest.TestCase):
    def make_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
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
                url text not null,
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
        conn.execute("insert into snapshots(run_id, source, fetched_at, status, item_count, error) values (?, ?, ?, ?, ?, ?)", ("run", "hn_firebase", "2026-05-31T00:00:00Z", "ok", 1, None))
        snapshot_id = conn.execute("select id from snapshots").fetchone()[0]
        conn.execute(
            """
            insert into items(run_id, snapshot_id, source, external_id, name, url, fetched_at, heat, velocity, acceleration, source_rank, description, metadata_json, raw_json)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run",
                snapshot_id,
                "hn_firebase",
                "123",
                "Show HN: Clawdbot",
                "https://news.ycombinator.com/item?id=123",
                "2026-05-31T00:00:00Z",
                None,
                None,
                None,
                1,
                "Launch post",
                json.dumps({"score": 160, "hn_url": "https://news.ycombinator.com/item?id=123"}, ensure_ascii=False),
                "{}",
            ),
        )
        conn.commit()
        return conn

    def test_hn_classifier_writes_projectness_evidence_and_alias_link(self):
        from pipeline.decision.hn_classifier import run_hn_classifier

        conn = self.make_conn()
        provider = FakeLLMProvider([
            {
                "item_id": 1,
                "projectness": "project",
                "confidence": 0.93,
                "canonical_name": "Clawdbot",
                "deterministic_links": [{"type": "github", "key": "github:owner/clawdbot", "url": "https://github.com/owner/clawdbot"}],
                "proposed_links": [],
                "summary": "Show HN launch for Clawdbot.",
            }
        ])

        summary = run_hn_classifier(conn, run_id="decision_run", provider=provider, limit=5, now="2026-05-31T00:00:00Z")

        self.assertEqual(summary["classified"], 1)
        evidence = conn.execute("select source, family, metric_name, metric_value, note from evidence_rows").fetchone()
        self.assertEqual(evidence[:4], ("hn_llm_classifier", "hn", "hn_projectness", "project"))
        self.assertIn("Clawdbot", evidence[4])
        alias = conn.execute("select alias, confidence, origin, approved from alias_links").fetchone()
        self.assertEqual(alias, ("github:owner/clawdbot", "deterministic", "hn_llm_classifier", 1))

    def test_hn_classifier_marks_news_article_as_noise_for_rules(self):
        from pipeline.decision.hn_classifier import run_hn_classifier

        conn = self.make_conn()
        provider = FakeLLMProvider([
            {
                "item_id": 1,
                "projectness": "news_article",
                "confidence": 0.9,
                "canonical_name": "",
                "deterministic_links": [],
                "proposed_links": [],
                "summary": "Article about a broader market event, not a project.",
            }
        ])

        run_hn_classifier(conn, run_id="decision_run", provider=provider, limit=5, now="2026-05-31T00:00:00Z")

        row = conn.execute("select metric_name, metric_value, signal_label from evidence_rows").fetchone()
        self.assertEqual(row, ("hn_projectness", "news_article", "noise"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_hn_classifier -v
```

Expected: FAIL because `hn_classifier.py` does not exist.

- [ ] **Step 3: Write minimal implementation**

Implement `pipeline/decision/hn_classifier.py` with:

- `PROMPT_VERSION = "hn-projectness-v1"`
- `candidate_hn_rows(conn, limit)` selecting latest `hn_firebase` and `hn_algolia` rows ordered by score/points descending, capped by `limit`.
- `validate_hn_output(payload)` enforcing allowed enum values and numeric confidence `0..1`.
- `entity_id_for_link(key)` returning `entity:{sha256(key)[:16]}`.
- `run_hn_classifier(conn, run_id, provider, limit, now)` that:
  - Calls provider with bounded rows.
  - Writes to `llm_cache`.
  - Writes one evidence row per classified HN row.
  - Inserts approved deterministic `alias_links` for deterministic GitHub/domain/npm keys.
  - Inserts `entity_merge_proposals` for non-deterministic proposed links with status `open`.
  - Exposes classifier results to the final deterministic rules pass through the explicit classifier evidence path.

Evidence row values:

```python
source="hn_llm_classifier"
family="hn"
metric_name="hn_projectness"
metric_value=projectness
rule_id="hn_llm_projectness"
signal_label="watch" for project/package/company_product, otherwise "noise"
historical_safety="llm_source_classifier"
raw_url_or_ref=f"item:{item_id}"
```

Rules behavior:

- HN rows classified as `news_article`, `topic_discussion`, `research_paper`, or `unknown` do not cast an HN Potential vote by themselves.
- HN rows classified as `project`, `package`, or `company_product` keep their normal HN vote behavior and can add deterministic aliases/backfill jobs when links are extracted.
- If the same entity has non-HN verified evidence, the HN noise classification does not delete that non-HN evidence; it only prevents HN-only news/topic promotion.

- [ ] **Step 4: Run test**

Run:

```bash
python3 -m unittest tests.test_hn_classifier -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/decision/hn_classifier.py tests/test_hn_classifier.py
git commit -m "feat: add hn projectness classifier"
```

## Task 7: X Stage 0 And Stage 1 Batched Triage

**Files:**
- Create: `pipeline/decision/x_classifier.py`
- Test: `tests/test_x_classifier.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_x_classifier.py`:

```python
from __future__ import annotations

import json
import sqlite3
import unittest

from pipeline.decision.llm_provider import FakeLLMProvider
from pipeline.decision.schema import init_decision_db


class XClassifierTest(unittest.TestCase):
    def make_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        conn.executescript(
            """
            create table x_tweets_store (
                tweet_id text primary key,
                author_username text not null,
                text text not null,
                url text,
                created_at text not null,
                imported_at text not null,
                raw_json text not null
            );
            """
        )
        rows = [
            ("t1", "credible1", "New repo https://github.com/owner/repo is useful", "https://x.com/credible1/status/t1", "2026-05-31T01:00:00Z", "2026-05-31T02:00:00Z", "{}"),
            ("t2", "credible2", "Trying owner/repo for agents", "https://x.com/credible2/status/t2", "2026-05-31T03:00:00Z", "2026-05-31T04:00:00Z", "{}"),
        ]
        conn.executemany("insert into x_tweets_store(tweet_id, author_username, text, url, created_at, imported_at, raw_json) values (?, ?, ?, ?, ?, ?, ?)", rows)
        conn.commit()
        return conn

    def test_stage1_extracts_mentions_and_stores_entity_mentions(self):
        from pipeline.decision.x_classifier import run_x_stage1

        conn = self.make_conn()
        provider = FakeLLMProvider([
            {
                "triage": [
                    {
                        "tweet_id": "t1",
                        "about_concrete_project": True,
                        "closer_look": True,
                        "project_refs": [{"entity_key": "github:owner/repo", "entity_name": "owner/repo", "entity_confidence": "linked", "confidence": 0.9}],
                        "expression_strength": "recommendation",
                        "evidence_quote": "New repo",
                        "reason": "Links a concrete repo.",
                    },
                    {
                        "tweet_id": "t2",
                        "about_concrete_project": True,
                        "closer_look": True,
                        "project_refs": [{"entity_key": "github:owner/repo", "entity_name": "owner/repo", "entity_confidence": "exact_handle", "confidence": 0.8}],
                        "expression_strength": "adoption_or_usage",
                        "evidence_quote": "Trying owner/repo",
                        "reason": "Mentions trying the same repo.",
                    },
                ]
            }
        ])

        summary = run_x_stage1(conn, run_id="decision_run", provider=provider, credible_handles={"credible1", "credible2"}, now="2026-05-31T04:00:00Z", limit=10, batch_size=10)

        self.assertEqual(summary["mentions"], 2)
        mention = conn.execute("select entity_id, window, distinct_authors, credible_authors, mention_count from entity_mentions").fetchone()
        self.assertEqual(mention[1:], ("24h", 2, 2, 2))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_x_classifier -v
```

Expected: FAIL because `x_classifier.py` does not exist.

- [ ] **Step 3: Write minimal implementation**

Implement `pipeline/decision/x_classifier.py` with:

- `X_STAGE1_PROMPT_VERSION = "x-stage1-v1"`
- `github_key_from_text(text)` extracting GitHub repo URLs.
- `candidate_tweets(conn, now, limit)` selecting tweets within 7 days, newest first.
- `run_x_stage1(conn, run_id, provider, credible_handles, now, limit, batch_size)` batching tweets, using cache, validating `triage` objects, filtering to `closer_look == true`, expanding `project_refs`, and upserting `entity_mentions` for `24h` and `7d`.

Use stable entity IDs from entity keys:

```python
entity_id = "entity:" + hashlib.sha256(entity_key.encode("utf-8")).hexdigest()[:16]
```

- [ ] **Step 4: Run test**

Run:

```bash
python3 -m unittest tests.test_x_classifier -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/decision/x_classifier.py tests/test_x_classifier.py
git commit -m "feat: add x batched triage"
```

## Task 8: X Stage 2 Tier Evidence And Rules Consumption

**Files:**
- Modify: `pipeline/decision/x_classifier.py`
- Modify: `pipeline/decision/rules.py`
- Modify: `pipeline/rules.json`
- Test: `tests/test_x_classifier.py`
- Test: `tests/test_rules_engine.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_x_classifier.py`:

```python
    def test_stage2_writes_x_social_evidence(self):
        from pipeline.decision.x_classifier import run_x_stage2

        conn = self.make_conn()
        conn.execute(
            """
            insert into entity_mentions(entity_id, run_id, window, distinct_authors, credible_authors, mention_count, mention_acceleration, source_refs_json)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("entity:x", "decision_run", "24h", 2, 2, 2, 2.0, json.dumps(["tweet:t1", "tweet:t2"])),
        )
        provider = FakeLLMProvider([
            {
                "entity_key": "github:owner/repo",
                "x_tier": "potential",
                "entity_confidence": "linked",
                "x_expression_strength": "recommendation",
                "cited_tweet_ids": ["t1", "t2"],
                "rationale": "Two credible authors cited the same repo.",
                "cross_source_notes": [],
            }
        ])

        summary = run_x_stage2(conn, run_id="decision_run", provider=provider, now="2026-05-31T04:00:00Z", limit=5)

        self.assertEqual(summary["tiered"], 1)
        rows = conn.execute("select source, family, metric_name, metric_value, raw_url_or_ref from evidence_rows order by metric_name").fetchall()
        metric_names = {row[2] for row in rows}
        self.assertIn("mention_count", metric_names)
        self.assertIn("x_tier", metric_names)
        tier = [row for row in rows if row[2] == "x_tier"][0]
        self.assertEqual(tier[:4], ("x_tweets", "x_social", "x_tier", "potential"))
        self.assertEqual(tier[4], "tweet:t1,tweet:t2")
```

Append to `tests/test_rules_engine.py`:

```python
    def test_x_social_evidence_can_promote_to_potential(self):
        from pipeline.decision.rules import evaluate_x_social_evidence

        state = self.state_for("github:owner/repo")
        rows = [{
            "source": "x_tweets",
            "metadata": {"x_tier": "potential", "cited_tweet_ids": ["t1", "t2"], "summary": "credible mentions"},
            "fetched_at": "2026-05-31T04:00:00Z",
            "id": 501,
            "name": "owner/repo",
        }]

        evidence = evaluate_x_social_evidence(state, rows, {"x_social": {"enabled": True}}, "rules-v1", "run-x")

        self.assertEqual(state.level, "potential")
        self.assertEqual(evidence[0].family, "x_social")
        self.assertEqual(evidence[0].metric_name, "x_tier")
```

If `tests/test_rules_engine.py` does not have `state_for`, add a tiny helper in the test file that constructs `EntityState(Entity(...))` using the existing dataclasses.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m unittest tests.test_x_classifier tests.test_rules_engine -v
```

Expected: FAIL because Stage 2 and x rules do not exist.

- [ ] **Step 3: Write minimal implementation**

Add to `pipeline/decision/x_classifier.py`:

- `X_STAGE2_PROMPT_VERSION = "x-stage2-v1"`
- `run_x_stage2(conn, run_id, provider, now, limit)` selecting `entity_mentions` with credible/burst gates.
- `validate_x_stage2_output`.
- Evidence writes with `source="x_tweets"` and `family="x_social"` for `distinct_authors`, `credible_authors`, `mention_count`, `mention_acceleration`, `x_tier`, `x_llm_summary`, and `x_expression_strength`.

Add to `pipeline/decision/rules.py`:

```python
def evaluate_x_social_evidence(state, rows, rules, rule_version, run_id):
    if not (rules.get("x_social") or {}).get("enabled", False):
        return []
    # consume rows that already carry metadata.x_tier or evidence-like x_tier rows
```

Rules:

- `x_tier = watch` adds weak signal.
- `x_tier = potential` promotes to potential.
- `x_tier = high` promotes to high_potential only when cited tweet ids are present and confidence context is not empty.
- Generic/no-citation output does not promote.

Add to `pipeline/rules.json`:

```json
"x_social": {
  "enabled": true,
  "watch": "watch",
  "potential": "potential",
  "high": "high_potential"
}
```

- [ ] **Step 4: Run tests**

Run:

```bash
python3 -m unittest tests.test_x_classifier tests.test_rules_engine -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/decision/x_classifier.py pipeline/decision/rules.py pipeline/rules.json tests/test_x_classifier.py tests/test_rules_engine.py
git commit -m "feat: add x social tier evidence"
```

## Task 9: NPM Registry Backfill

**Files:**
- Create: `pipeline/decision/npm_backfill.py`
- Modify: `pipeline/decision/rules.py`
- Modify: `pipeline/rules.json`
- Test: `tests/test_npm_backfill.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_npm_backfill.py`:

```python
from __future__ import annotations

import sqlite3
import unittest

from pipeline.decision.schema import init_decision_db


class FakeNpmClient:
    def package_metadata(self, package: str):
        return {"name": package, "repository": {"url": "git+https://github.com/owner/repo.git"}, "time": {"modified": "2026-05-31T00:00:00.000Z"}}

    def downloads(self, package: str, period: str):
        return {"downloads": 12000, "package": package, "start": "2026-05-30", "end": "2026-05-31"}


class NpmBackfillTest(unittest.TestCase):
    def test_npm_backfill_writes_download_and_repo_evidence(self):
        from pipeline.decision.npm_backfill import run_npm_backfill

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        conn.execute(
            """
            insert into backfill_jobs(entity_id, run_id, source, reason, status, requested_at)
            values (?, ?, ?, ?, ?, ?)
            """,
            ("entity:npm", "run", "npm_registry", "package_downloads:demo-package", "pending", "2026-05-31T00:00:00Z"),
        )
        conn.commit()

        summary = run_npm_backfill(conn, run_id="run", client=FakeNpmClient(), now="2026-05-31T00:00:00Z", limit=5)

        self.assertEqual(summary["completed"], 1)
        rows = conn.execute("select source, family, metric_name, metric_value from evidence_rows order by metric_name").fetchall()
        self.assertEqual(rows[0], ("npm_registry", "package_family", "daily_downloads", "12000"))
        self.assertEqual(rows[1], ("npm_registry", "package_family", "npm_repository_link", "github:owner/repo"))
        status = conn.execute("select status from backfill_jobs").fetchone()[0]
        self.assertEqual(status, "completed")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_npm_backfill -v
```

Expected: FAIL because `npm_backfill.py` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `pipeline/decision/npm_backfill.py`:

- `NpmRegistryClient.package_metadata(package)` calls `https://registry.npmjs.org/{package}`.
- `NpmRegistryClient.downloads(package, period)` calls `https://api.npmjs.org/downloads/point/{period}/{package}`.
- `run_npm_backfill(conn, run_id, client, now, limit)` consumes pending `backfill_jobs.source = npm_registry` capped by `limit`.
- Parse job reason `package_downloads:<package>`.
- Write `daily_downloads` evidence and `npm_repository_link` evidence when a GitHub repo is present.
- Mark jobs completed/failed preserving `completed_at` and `result_ref`.

Add npm rules support:

- `daily_downloads >= 10000` and rising versus the 7-day average promotes package family to potential.
- `daily_downloads >= 100000` promotes to high_potential.
- Repository link should create approved deterministic `alias_links` for the same entity.

Add to `pipeline/rules.json`:

```json
"npm_registry": {
  "daily_downloads": {
    "watch": 3000,
    "potential": 10000,
    "high_potential": 100000
  }
}
```

- [ ] **Step 4: Run test**

Run:

```bash
python3 -m unittest tests.test_npm_backfill -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/decision/npm_backfill.py pipeline/decision/rules.py pipeline/rules.json tests/test_npm_backfill.py
git commit -m "feat: add npm registry backfill"
```

## Task 10: Decision Runner Integration

**Files:**
- Modify: `pipeline/decision/run_decision.py`
- Modify: `pipeline/decision/schema.py`
- Test: `tests/test_decision_runner.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_decision_runner.py`:

```python
    def test_runner_can_invoke_bounded_source_classifiers(self):
        from pipeline.decision.llm_provider import FakeLLMProvider
        from pipeline.decision.run_decision import run_decision

        db_path = self.seed_db_with_hn_and_x_rows()
        provider = FakeLLMProvider([
            {"item_id": 1, "projectness": "news_article", "confidence": 0.88, "canonical_name": "", "deterministic_links": [], "proposed_links": [], "summary": "news only"}
        ])
        summary = run_decision(
            db_path=db_path,
            run_id="decision_integration",
            export_json_path=self.temp_path("candidates.json"),
            now="2026-05-31T00:00:00Z",
            hn_llm_provider=provider,
            hn_classifier_limit=1,
            x_llm_provider=None,
            npm_client=None,
        )

        self.assertIn("hn_classified", summary)
        self.assertLessEqual(summary["hn_classified"], 1)
```

If test helpers do not exist, add local helpers in the test file using the same seed pattern already present in `tests/test_decision_runner.py`.

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m unittest tests.test_decision_runner -v
```

Expected: FAIL because `run_decision` does not accept classifier/backfill arguments.

- [ ] **Step 3: Write minimal implementation**

Extend `run_decision` keyword args:

```python
hn_llm_provider: Any | None = None,
hn_classifier_limit: int = 0,
x_llm_provider: Any | None = None,
x_classifier_limit: int = 0,
npm_client: Any | None = None,
npm_backfill_limit: int = 0,
```

Execution order:

1. Reset run-scoped decision tables.
2. Resolve entities and run deterministic pass 1.
3. Optional GitHub backfill as already implemented.
4. Optional HN classifier if provider and limit are set.
5. Optional X Stage 1/Stage 2 if provider and limit are set.
6. Optional npm backfill if client and limit are set.
7. Run final deterministic evaluation consuming new classifier/backfill data through the explicit classifier evidence path.
8. Persist candidates/evidence/export.

Do not enqueue npm jobs unless explicit npm rules produce a package key or an existing pending npm job exists. Do not run LLM if provider is `None`.

- [ ] **Step 4: Run integration test**

Run:

```bash
python3 -m unittest tests.test_decision_runner -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/decision/run_decision.py tests/test_decision_runner.py
git commit -m "feat: integrate bounded classifiers"
```

## Task 11: Bounded Real Smoke Tests

**Files:**
- Create: `pipeline/decision/smoke_llm.py`
- Create: `pipeline/decision/smoke_npm.py`

- [ ] **Step 1: Write smoke scripts**

`pipeline/decision/smoke_llm.py`:

```python
from __future__ import annotations

import argparse
import json

from pipeline.run_pipeline import load_dotenv
from pipeline.decision.llm_provider import DeepSeekProvider


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", choices=["json"], default="json")
    args = parser.parse_args()
    load_dotenv()
    provider = DeepSeekProvider()
    result = provider.complete_json(
        task="smoke",
        prompt_version="smoke-v1",
        input_payload={"instruction": "Return {'ok': true, 'scope': 'smoke'} as JSON."},
        system_prompt="Return strict JSON only.",
    )
    print(json.dumps({"ok": bool(result.get("ok")), "keys": sorted(result.keys())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`pipeline/decision/smoke_npm.py`:

```python
from __future__ import annotations

import json

from pipeline.decision.npm_backfill import NpmRegistryClient


def main() -> int:
    client = NpmRegistryClient()
    metadata = client.package_metadata("typescript")
    downloads = client.downloads("typescript", "last-day")
    print(json.dumps({"package": metadata.get("name"), "downloads": downloads.get("downloads")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run bounded smoke**

Run:

```bash
python3 pipeline/decision/smoke_npm.py
python3 pipeline/decision/smoke_llm.py
```

Expected:

- npm prints package name and one download number.
- LLM prints only non-secret summary keys, no token content.

- [ ] **Step 3: Commit**

```bash
git add pipeline/decision/smoke_llm.py pipeline/decision/smoke_npm.py
git commit -m "chore: add bounded source smoke checks"
```

## Task 12: Final Verification And Push

**Files:**
- Verification only; source edits should already be committed by Tasks 1-11.

- [ ] **Step 1: Run Python tests**

```bash
python3 -m unittest discover -s tests -v
```

Expected: all tests PASS.

- [ ] **Step 2: Run web tests and build**

```bash
cd web && npm test && npm run build
```

Expected: all tests PASS and Vite build succeeds.

- [ ] **Step 3: Run export and decision offline**

```bash
python3 pipeline/run_pipeline.py --export-only
python3 -m pipeline.decision.run_decision --no-backfill
```

Expected:

- Dashboard export still succeeds.
- Decision run succeeds without external calls.

- [ ] **Step 4: Serve and open app**

```bash
python3 pipeline/server.py --host 127.0.0.1 --port 8787
cd web && VITE_API_BASE=http://127.0.0.1:8787 npm run dev
```

Open `http://127.0.0.1:5173/` and verify the React app loads dynamic source dashboard data.

- [ ] **Step 5: Push**

```bash
git status --short
git push origin main
```

Expected: `main` pushed.

## Self-Review

- Spec coverage: UI migration is covered by Tasks 1-3; DeepSeek/LLM harness by Tasks 4-5 and 11; HN LLM project/entity classification by Task 6; X social classifier by Tasks 7-8; npm registry backfill by Task 9; runner integration by Task 10; final verification by Task 12.
- Scope check: Layer 2 Feed selection/cards, Kimi deepdive, chatbot, cron, and rule editor are explicitly excluded. Candidate Pool remains inside Feed; Daily Feed remains locked.
- TDD check: Every implementation task starts with a failing test or smoke script, then minimal implementation, then verification, then commit.
- API contract check: `/api/dashboard-data` returns source dashboard payload and candidate data without exposing secrets. Existing `/api/candidates`, `/api/evidence`, and `/api/entity/{entity_id}` are not removed.
- Data safety check: Fuzzy HN/X outputs create proposals only. Deterministic links can create approved aliases. External calls are capped by task limits and smoke scripts print only summaries.
- Parallelism check: Shared files `schema.py` and `run_decision.py` are coordinator-owned to avoid subagent conflicts; other tasks can be split by ownership.
