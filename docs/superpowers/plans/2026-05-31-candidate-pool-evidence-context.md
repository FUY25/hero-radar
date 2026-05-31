# Candidate Pool Evidence Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Candidate Pool explain itself: compact evidence bullets, canonical links, bounded README context, and a cached 3-round agentic link research path for classifier candidates that do not already have a repo/site/package link.

**Architecture:** Add a small Candidate Context layer over existing decision tables. Evidence summaries are derived from `evidence_rows`, resolver aliases, cached README excerpts, and source rows; the server returns this enriched shape through existing dashboard/candidate endpoints. External research stays off by default and only runs when an LLM provider and search client are explicitly supplied, using a bounded local loop instead of introducing a framework.

**Tech Stack:** Python 3 stdlib, SQLite, `unittest`, existing DeepSeek OpenAI-compatible provider, existing `api_cache`, React + Vite tests. No Layer 2 score/feed selection, no chatbot, no rule editor.

---

## Source Documents And External Research

Read before implementation:

- `docs/decision-layer-v1.md`, especially Layer 1 source evidence and Layer 2 boundary.
- `docs/superpowers/specs/2026-05-31-layer2-evidence-feed-design.md`, especially Candidate Evidence Summary, Candidate Context Bundle, Candidate Pool UI Contract, and README bounds.
- `docs/superpowers/plans/2026-05-31-classifier-eval-and-post-tier-resolver.md`, especially post-tier resolver decisions.
- `pipeline/decision/resolver.py`, `pipeline/server.py`, `web/src/App.jsx`, and `web/src/dashboardModel.js`.

External research conclusion:

- DeepSeek official Tool Calls docs: `https://api-docs.deepseek.com/guides/tool_calls`. The model emits a tool request; our code executes the function/search and sends results back. The model does not browse by itself.
- Hugging Face smolagents docs: `https://huggingface.co/docs/smolagents/reference/default_tools`. It has built-in web search tools and is the lightest framework candidate for a packaged version of this loop.
- LangGraph docs: `https://docs.langchain.com/oss/python/langgraph`. It is a low-level orchestration runtime for long-running/stateful agents; too much surface for this 3-round link lookup.
- OpenAI Agents SDK docs: `https://openai.github.io/openai-agents-js/guides/tools/`. It has hosted web search, but that is OpenAI-model-specific and not aligned with current DeepSeek provider routing.

Decision: implement a local `agentic_link_research` loop with max 3 rounds, cached results, injected `search_client`, and injected DeepSeek-compatible `LLMProvider`. Do not add a new framework dependency in this slice.

## Scope

Included:

- Candidate Pool rows include:
  - `evidence_bullets`: max 3 by default in the UI, each with `label`, `family`, `origin_type`, `provenance_badge`, `strength`, and `source_refs`.
  - `evidence_count`.
  - `canonical_link`: best verified GitHub/homepage/npm link.
  - `context_preview`: source description or README preview.
  - `readme_excerpt_available`.
  - `binding_confidence`: `verified`, `resolved`, `weak`, or `none`.
- `/api/dashboard-data`, `/api/candidates`, and `/api/entity/{id}` return the enriched context shape.
- README excerpt fetch/cache for verified GitHub repo candidates only, bounded to 8000 stored chars and 1000 preview chars.
- Post-tier agentic link research that runs only for accepted classifier candidates when deterministic/internal resolver did not find a link, with max 3 LLM/search rounds.
- Tests use fake clients/providers first. A tiny real smoke can be run manually if keys/search provider exist.

Excluded:

- Layer 2 scoring, feed selection, Kimi/deepdive, Daily Feed cards, chatbot, rule editor.
- Unbounded crawling or generic web browsing from the pipeline.
- Running external research for all source rows. It only targets accepted classifier candidates/candidate pool rows without a link.
- Introducing smolagents, LangGraph, OpenAI Agents SDK, Tavily, Serper, or Brave as a required dependency in this slice.

## File Structure

Create:

- `pipeline/decision/candidate_context.py`  
  Builds evidence bullets, canonical links, binding confidence, README preview, and per-entity context bundles from existing tables.
- `pipeline/decision/readme_enrichment.py`  
  Parses GitHub repo links, fetches bounded README excerpts through an injected GitHub client, and stores them in `api_cache`.
- `pipeline/decision/web_research.py`  
  Implements the max-3-round agentic link research loop with fake-provider-friendly interfaces and cache keys.
- `tests/test_candidate_context.py`
- `tests/test_readme_enrichment.py`
- `tests/test_web_research.py`

Modify:

- `pipeline/server.py`  
  Use `candidate_context` in `query_candidates()` and `query_entity()`.
- `pipeline/decision/resolver.py`  
  Publicly expose search result normalization and call `web_research` only when internal/search-client direct lookup fails and an LLM provider is supplied.
- `pipeline/decision/run_decision.py`  
  Add explicit knobs for README enrichment and agentic link research, wiring them after classifier resolver enrichment and before export.
- `tests/test_dashboard_data_api.py`
- `tests/test_resolver.py`
- `tests/test_decision_runner.py`
- `web/src/dashboardModel.js`
- `web/src/dashboardModel.test.mjs`
- `web/src/App.jsx`
- `web/src/styles.css`

## Subagent Ownership

Use separate workers only on disjoint write scopes:

- Worker A, backend context/API: `pipeline/decision/candidate_context.py`, `pipeline/server.py`, `tests/test_candidate_context.py`, `tests/test_dashboard_data_api.py`.
- Worker B, enrichment/resolver: `pipeline/decision/readme_enrichment.py`, `pipeline/decision/web_research.py`, `pipeline/decision/resolver.py`, `pipeline/decision/run_decision.py`, `tests/test_readme_enrichment.py`, `tests/test_web_research.py`, `tests/test_resolver.py`, `tests/test_decision_runner.py`.
- Worker C, React shell: `web/src/dashboardModel.js`, `web/src/dashboardModel.test.mjs`, `web/src/App.jsx`, `web/src/styles.css`.

All workers must work on `main`, not `.claude/worktrees/...`, must not revert unrelated changes, and must report every changed path and every test run.

---

## Round 1: Candidate Evidence Context API And UI

### Task 1: Candidate Context Builder

**Files:**

- Create: `pipeline/decision/candidate_context.py`
- Test: `tests/test_candidate_context.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_candidate_context.py`:

```python
import json
import sqlite3
import unittest

from pipeline.decision.schema import init_decision_db


class CandidateContextTest(unittest.TestCase):
    def make_conn(self):
        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        conn.executescript(
            """
            create table items (
                id integer primary key autoincrement,
                run_id text not null,
                snapshot_id integer,
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
        conn.execute(
            """
            insert into entities(entity_id, canonical_entity, canonical_key, key_type, first_seen, aliases_json, source_item_ids_json)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "entity:repo",
                "owner/repo",
                "github:owner/repo",
                "github",
                "2026-05-31T00:00:00Z",
                json.dumps(["owner/repo"]),
                json.dumps([1]),
            ),
        )
        conn.execute(
            """
            insert into items(id, run_id, snapshot_id, source, external_id, name, url, fetched_at, description, metadata_json, raw_json)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "source-run",
                1,
                "github_trending",
                "daily:owner/repo",
                "owner/repo",
                "https://github.com/owner/repo",
                "2026-05-31T00:00:00Z",
                "Repo description from source row.",
                json.dumps({"period_stars": 1300}),
                "{}",
            ),
        )
        rows = [
            ("github_trending", "stars_today", "1300", "github", "github_daily", "potential", "item:1"),
            ("hn_top", "hn_score", "142", "hn", "hn_frontpage", "potential", "item:2"),
            ("x_tweets", "x_tier", "potential", "x_social", "x_stage2", "potential", "tweet:t1"),
            ("resolver", "canonical_link", "github:owner/repo", "resolver", "resolver_link", "context", "alias:1"),
        ]
        for source, metric_name, metric_value, family, rule_id, signal_label, raw_ref in rows:
            conn.execute(
                """
                insert into evidence_rows(entity_id, canonical_entity, alias, source, event_at, metric_name, metric_value, family, rule_id, rule_version, signal_label, historical_safety, note, raw_url_or_ref, run_id)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "entity:repo",
                    "owner/repo",
                    "owner/repo",
                    source,
                    "2026-05-31T00:00:00Z",
                    metric_name,
                    metric_value,
                    family,
                    rule_id,
                    "rules-v1",
                    signal_label,
                    "snapshot_only",
                    "note",
                    raw_ref,
                    "run-1",
                ),
            )
        conn.commit()
        return conn

    def test_context_bundle_has_bullets_link_preview_and_binding(self):
        from pipeline.decision.candidate_context import context_bundle_for_entity

        conn = self.make_conn()
        bundle = context_bundle_for_entity(conn, entity_id="entity:repo", run_id="run-1")

        self.assertEqual(bundle["canonical_link"], "https://github.com/owner/repo")
        self.assertEqual(bundle["binding_confidence"], "verified")
        self.assertEqual(bundle["context_preview"], "Repo description from source row.")
        self.assertEqual(bundle["evidence_count"], 4)
        self.assertEqual([b["label"] for b in bundle["evidence_bullets"][:3]], [
            "GH +1.3k stars / 24h",
            "HN front page, 142 pts",
            "X potential",
        ])
        self.assertEqual(bundle["evidence_bullets"][2]["origin_type"], "source_classifier")

    def test_context_prefers_resolver_alias_when_canonical_key_is_name(self):
        from pipeline.decision.candidate_context import context_bundle_for_entity

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        conn.execute(
            """
            insert into entities(entity_id, canonical_entity, canonical_key, key_type, first_seen, aliases_json, source_item_ids_json)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            ("entity:claw", "Clawdbot", "name:clawdbot", "name", "2026-05-31T00:00:00Z", "[]", "[]"),
        )
        conn.execute(
            """
            insert into alias_links(entity_id, source, external_id, alias, confidence, origin, approved, created_at)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("entity:claw", "resolver", "name:clawdbot", "github:owner/clawdbot", "deterministic", "resolver", 1, "2026-05-31T00:00:00Z"),
        )
        bundle = context_bundle_for_entity(conn, entity_id="entity:claw", run_id="run-1")
        self.assertEqual(bundle["canonical_link"], "https://github.com/owner/clawdbot")
        self.assertEqual(bundle["binding_confidence"], "resolved")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
python3 -m unittest tests.test_candidate_context -v
```

Expected: `ModuleNotFoundError` or `ImportError` for `pipeline.decision.candidate_context`.

- [ ] **Step 3: Implement minimal candidate context**

Create `pipeline/decision/candidate_context.py` with these public functions:

```python
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any


SOURCE_CLASSIFIER_SOURCES = {"hn_llm_classifier", "x_tweets", "npm_registry"}
BACKFILL_SOURCES = {"github_api", "github_stargazers", "npm_downloads"}


def key_to_url(key: str) -> str | None:
    if key.startswith("github:"):
        return f"https://github.com/{key.split(':', 1)[1]}"
    if key.startswith("domain:"):
        return f"https://{key.split(':', 1)[1]}"
    if key.startswith("npm:"):
        return f"https://www.npmjs.com/package/{key.split(':', 1)[1]}"
    return None


def context_bundle_for_entity(conn: sqlite3.Connection, *, entity_id: str, run_id: str) -> dict[str, Any]:
    entity = _entity_row(conn, entity_id)
    evidence = _evidence_rows(conn, entity_id, run_id)
    canonical_key = str(entity.get("canonical_key") or "")
    canonical_link = key_to_url(canonical_key)
    binding = "verified" if canonical_link else "none"
    if not canonical_link:
        alias_key = _best_alias_key(conn, entity_id)
        canonical_link = key_to_url(alias_key or "")
        binding = "resolved" if canonical_link else "none"
    if not canonical_link:
        source_link = _best_source_link(conn, entity)
        canonical_link = source_link
        binding = "weak" if source_link else "none"
    readme_preview = _readme_preview(conn, canonical_key)
    context_preview = readme_preview or _best_source_description(conn, entity)
    bullets = [_evidence_bullet(row) for row in evidence]
    return {
        "entity_id": entity_id,
        "canonical_link": canonical_link,
        "binding_confidence": binding,
        "context_preview": context_preview,
        "readme_excerpt_available": bool(readme_preview),
        "evidence_count": len(bullets),
        "evidence_bullets": bullets,
        "source_families": sorted({bullet["family"] for bullet in bullets if bullet.get("family")}),
    }
```

Implementation details:

- `_entity_row()` returns an empty dict when missing.
- `_evidence_rows()` orders deterministic movement first by source priority: GitHub, HN, X, npm/package, resolver, then id.
- `_evidence_bullet()` maps:
  - `github` + `stars_today`: `GH +{compact_number(value)} stars / 24h`
  - `hn` + `hn_score`: `HN front page, {value} pts`
  - `x_tweets` + `x_tier`: `X {value}`
  - `resolver`: `Resolved {metric_value}`
  - fallback: `{family}: {metric_name} {metric_value}`
- `_origin_type()` maps `SOURCE_CLASSIFIER_SOURCES` to `source_classifier`, resolver to `resolver`, backfill sources to `backfill`, `family == "cross_source"` to `cross_source_rule`, else `deterministic_rule`.
- `_provenance_badge()` returns `LLM classifier`, `resolver`, `backfill`, `cross-source`, or `rule`.
- `_readme_preview()` reads `api_cache` rows where `source = 'github_readme'`, `external_id` equals `owner/repo`, and status is `ok`; it returns `response_json["preview"]`.
- `_best_source_description()` reads `items.description` for ids in `source_item_ids_json`.
- `_best_source_link()` reads `items.url` for source item ids and returns a GitHub/npm/domain URL when present.

- [ ] **Step 4: Run passing tests**

Run:

```bash
python3 -m unittest tests.test_candidate_context -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add pipeline/decision/candidate_context.py tests/test_candidate_context.py docs/superpowers/plans/2026-05-31-candidate-pool-evidence-context.md
git commit -m "feat: build candidate evidence context"
```

### Task 2: Server Candidate Context API

**Files:**

- Modify: `pipeline/server.py`
- Modify: `tests/test_dashboard_data_api.py`

- [ ] **Step 1: Write failing server tests**

Add this test to `tests/test_dashboard_data_api.py`:

```python
    def test_server_candidates_include_evidence_context(self) -> None:
        import pipeline.server as server
        from pipeline.decision.schema import begin_decision_run, finish_decision_run, init_decision_db

        temp, db_path = self.make_db()
        self.addCleanup(temp.cleanup)
        conn = sqlite3.connect(db_path)
        init_decision_db(conn)
        begin_decision_run(
            conn,
            run_id="decision-run",
            source_snapshot_run_id="source-run",
            config_hash="config",
            rule_version="rules-v1",
        )
        conn.execute(
            """
            insert into entities(entity_id, canonical_entity, canonical_key, key_type, first_seen, aliases_json, source_item_ids_json)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            ("entity:repo", "owner/repo", "github:owner/repo", "github", "2026-05-31T10:00:00Z", "[]", "[1]"),
        )
        conn.execute(
            """
            insert into potential_candidates(entity_id, run_id, level, fired_families_json, first_trigger_at)
            values (?, ?, ?, ?, ?)
            """,
            ("entity:repo", "decision-run", "potential", json.dumps(["github"]), "2026-05-31T10:00:00Z"),
        )
        conn.execute(
            """
            insert into evidence_rows(entity_id, canonical_entity, alias, source, event_at, metric_name, metric_value, family, rule_id, rule_version, signal_label, historical_safety, note, raw_url_or_ref, run_id)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("entity:repo", "owner/repo", "owner/repo", "github_trending", "2026-05-31T10:00:00Z", "stars_today", "321", "github", "github_daily", "rules-v1", "potential", "snapshot_only", "passed", "item:1", "decision-run"),
        )
        finish_decision_run(conn, run_id="decision-run", status="ok")
        conn.close()

        with mock.patch.object(server, "DB_PATH", db_path):
            payload = server.query_dashboard_data_payload()

        row = payload["candidates"]["candidates"][0]
        self.assertEqual(row["canonical_link"], "https://github.com/owner/repo")
        self.assertEqual(row["evidence_bullets"][0]["label"], "GH +321 stars / 24h")
        self.assertEqual(row["binding_confidence"], "verified")
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
python3 -m unittest tests.test_dashboard_data_api -v
```

Expected: failure because `canonical_link` / `evidence_bullets` are not present.

- [ ] **Step 3: Attach context in server payloads**

Modify `pipeline/server.py`:

```python
from pipeline.decision.candidate_context import context_bundle_for_entity
```

Inside `query_candidates()`, after building each candidate/edge row, merge:

```python
row_payload.update(context_bundle_for_entity(conn, entity_id=row_payload["entity_id"], run_id=run_id))
```

Inside `query_entity()`, add:

```python
"context": context_bundle_for_entity(conn, entity_id=entity_id, run_id=run_id) if run_id else {},
```

Do not change endpoint paths or existing keys.

- [ ] **Step 4: Run passing tests**

Run:

```bash
python3 -m unittest tests.test_candidate_context tests.test_dashboard_data_api -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add pipeline/server.py tests/test_dashboard_data_api.py
git commit -m "feat: expose candidate context in api"
```

### Task 3: Candidate Pool UI Evidence Columns

**Files:**

- Modify: `web/src/dashboardModel.js`
- Modify: `web/src/dashboardModel.test.mjs`
- Modify: `web/src/App.jsx`
- Modify: `web/src/styles.css`

- [ ] **Step 1: Write failing model tests**

Add to `web/src/dashboardModel.test.mjs`:

```javascript
test('candidateRowsForFeed keeps evidence and canonical link fields', () => {
  const candidates = {
    candidates: [{
      entity_id: 'entity:1',
      canonical_entity: 'Repo',
      level: 'potential',
      evidence_bullets: [{ label: 'GH +321 stars / 24h', origin_type: 'deterministic_rule' }],
      evidence_count: 4,
      canonical_link: 'https://github.com/owner/repo',
      context_preview: 'Repo description',
      binding_confidence: 'verified',
    }],
    edge_watch: [],
  };
  const [row] = candidateRowsForFeed(candidates);
  assert.equal(row.evidence_bullets[0].label, 'GH +321 stars / 24h');
  assert.equal(row.evidence_extra_count, 1);
  assert.equal(row.canonical_link, 'https://github.com/owner/repo');
  assert.equal(row.binding_confidence, 'verified');
});
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
npm test --prefix web
```

Expected: `evidence_extra_count` assertion fails.

- [ ] **Step 3: Normalize candidate UI row fields**

Modify `candidateRowsForFeed()` in `web/src/dashboardModel.js`:

```javascript
function normalizeCandidateRow(row, poolType) {
  const evidence = Array.isArray(row.evidence_bullets) ? row.evidence_bullets : [];
  return {
    ...row,
    level: row.level || poolType,
    pool_type: poolType,
    evidence_bullets: evidence,
    evidence_extra_count: Math.max(0, Number(row.evidence_count || evidence.length) - 3),
    canonical_link: row.canonical_link || '',
    context_preview: row.context_preview || '',
    binding_confidence: row.binding_confidence || 'none',
  };
}

export function candidateRowsForFeed(candidates) {
  return [
    ...(candidates?.candidates || []).map((row) => normalizeCandidateRow(row, row.level)),
    ...(candidates?.edge_watch || []).map((row) => normalizeCandidateRow({ ...row, level: 'edge_watch' }, 'edge_watch')),
  ];
}
```

- [ ] **Step 4: Update the Candidate Pool table**

Modify the `FeedView` Candidate Pool table in `web/src/App.jsx`:

```jsx
<th>Candidate</th>
<th>Level</th>
<th>Evidence</th>
<th>Link</th>
<th>Context</th>
```

Render each row:

```jsx
<td>
  <strong>{row.canonical_entity || row.entity_id}</strong>
  <code>{row.canonical_key || row.entity_id}</code>
</td>
<td><span className={`badge ${row.level}`}>{levelLabel(row.level)}</span></td>
<td>
  <div className="evidence-list">
    {(row.evidence_bullets || []).slice(0, 3).map((bullet) => (
      <span className="evidence-pill" key={`${row.entity_id}:${bullet.label}:${bullet.origin_type}`}>
        {bullet.label}
        {bullet.provenance_badge ? <small>{bullet.provenance_badge}</small> : null}
      </span>
    ))}
    {row.evidence_extra_count > 0 ? <span className="evidence-more">+{row.evidence_extra_count}</span> : null}
  </div>
</td>
<td>
  {row.canonical_link ? (
    <a className="candidate-link" href={row.canonical_link} target="_blank" rel="noreferrer">
      Open
    </a>
  ) : (
    <span className="muted">{row.binding_confidence === 'weak' ? 'Weak binding' : 'No link'}</span>
  )}
</td>
<td className="candidate-context">{row.context_preview || row.first_trigger_at || row.status || ''}</td>
```

Keep the current panel/table structure. Do not introduce cards, drawer design, or new visual language in this task.

Add CSS:

```css
.evidence-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  max-width: 420px;
}

.evidence-pill,
.evidence-more {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 2px 7px;
  font-size: 12px;
  line-height: 1.4;
  background: var(--panel2);
}

.evidence-pill small {
  color: var(--muted);
  font-size: 11px;
}

.candidate-link {
  font-size: 12px;
  font-weight: 650;
}

.candidate-context {
  max-width: 360px;
  color: var(--muted);
}
```

- [ ] **Step 5: Run passing frontend tests and build**

Run:

```bash
npm test --prefix web
npm run build --prefix web
```

Expected: tests and build pass.

- [ ] **Step 6: Commit**

Run:

```bash
git add web/src/dashboardModel.js web/src/dashboardModel.test.mjs web/src/App.jsx web/src/styles.css web/dist
git commit -m "feat: show candidate evidence context"
```

---

## Round 2: README Enrichment And Agentic Link Research

### Task 4: README Excerpt Enrichment

**Files:**

- Create: `pipeline/decision/readme_enrichment.py`
- Test: `tests/test_readme_enrichment.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_readme_enrichment.py`:

```python
import sqlite3
import unittest

from pipeline.decision.schema import init_decision_db


class FakeGitHubReadmeClient:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def get_readme_text(self, repo_key):
        self.calls.append(repo_key)
        return self.text


class ReadmeEnrichmentTest(unittest.TestCase):
    def test_parse_github_repo_from_url_and_key(self):
        from pipeline.decision.readme_enrichment import github_repo_key_from_link

        self.assertEqual(github_repo_key_from_link("github:Owner/Repo"), "owner/repo")
        self.assertEqual(github_repo_key_from_link("https://github.com/Owner/Repo?tab=readme"), "owner/repo")
        self.assertIsNone(github_repo_key_from_link("https://example.com"))

    def test_fetches_bounds_and_caches_readme_excerpt(self):
        from pipeline.decision.readme_enrichment import fetch_and_cache_readme_excerpt, read_cached_readme_excerpt

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        client = FakeGitHubReadmeClient("# Title\n" + ("A" * 9000))

        response = fetch_and_cache_readme_excerpt(conn, client=client, repo_key="owner/repo")
        cached = read_cached_readme_excerpt(conn, repo_key="owner/repo")

        self.assertEqual(client.calls, ["owner/repo"])
        self.assertEqual(response["repo_key"], "owner/repo")
        self.assertEqual(len(response["excerpt"]), 8000)
        self.assertEqual(len(response["preview"]), 1000)
        self.assertEqual(cached["excerpt"], response["excerpt"])

    def test_cache_prevents_second_fetch(self):
        from pipeline.decision.readme_enrichment import fetch_and_cache_readme_excerpt

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        client = FakeGitHubReadmeClient("hello readme")

        first = fetch_and_cache_readme_excerpt(conn, client=client, repo_key="owner/repo")
        second = fetch_and_cache_readme_excerpt(conn, client=client, repo_key="owner/repo")

        self.assertEqual(first, second)
        self.assertEqual(client.calls, ["owner/repo"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
python3 -m unittest tests.test_readme_enrichment -v
```

Expected: import failure for `pipeline.decision.readme_enrichment`.

- [ ] **Step 3: Implement README cache helpers**

Create `pipeline/decision/readme_enrichment.py`:

```python
from __future__ import annotations

import base64
import json
import os
import sqlite3
import urllib.parse
import urllib.request
from typing import Any

from pipeline.decision.cache import api_cache_key, get_api_cache, put_api_cache, stable_hash

README_SOURCE = "github_readme"
README_WINDOW = "candidate_context"
MAX_README_CHARS = 8000
MAX_README_PREVIEW_CHARS = 1000


def github_repo_key_from_link(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if raw.startswith("github:"):
        repo = raw.split(":", 1)[1]
    else:
        parsed = urllib.parse.urlparse(raw)
        if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
            return None
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if len(parts) < 2:
            return None
        repo = f"{parts[0]}/{parts[1]}"
    if "/" not in repo:
        return None
    owner, name = repo.split("/", 1)
    return f"{owner.lower()}/{name.lower()}"


class GitHubReadmeClient:
    def __init__(self, token: str | None = None, timeout: int = 30) -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN", "")
        self.timeout = timeout

    def get_readme_text(self, repo_key: str) -> str:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repo_key}/readme",
            headers={
                "Accept": "application/vnd.github+json",
                **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = payload.get("content") or ""
        encoding = payload.get("encoding") or ""
        if encoding == "base64":
            return base64.b64decode(content).decode("utf-8", errors="replace")
        return str(content)
```

Also implement:

```python
def read_cached_readme_excerpt(conn: sqlite3.Connection, *, repo_key: str) -> dict[str, Any] | None:
    key = _readme_cache_key(repo_key)
    return get_api_cache(conn, key)


def fetch_and_cache_readme_excerpt(conn: sqlite3.Connection, *, client: Any, repo_key: str) -> dict[str, Any]:
    cached = read_cached_readme_excerpt(conn, repo_key=repo_key)
    if cached:
        return cached
    text = client.get_readme_text(repo_key)
    excerpt = str(text or "")[:MAX_README_CHARS]
    response = {
        "repo_key": repo_key,
        "excerpt": excerpt,
        "preview": excerpt[:MAX_README_PREVIEW_CHARS],
        "chars": len(excerpt),
    }
    input_hash = stable_hash({"repo_key": repo_key, "max_chars": MAX_README_CHARS})
    put_api_cache(
        conn,
        cache_key=_readme_cache_key(repo_key),
        source=README_SOURCE,
        external_id=repo_key,
        window=README_WINDOW,
        input_hash=input_hash,
        response=response,
        status="ok",
    )
    return response
```

`_readme_cache_key(repo_key)` must use `api_cache_key(source=README_SOURCE, external_id=repo_key, window=README_WINDOW, input_hash=stable_hash(...))`.

- [ ] **Step 4: Run passing tests**

Run:

```bash
python3 -m unittest tests.test_readme_enrichment tests.test_candidate_context -v
```

Expected: all tests pass and `candidate_context` can read the cache preview.

- [ ] **Step 5: Commit**

Run:

```bash
git add pipeline/decision/readme_enrichment.py tests/test_readme_enrichment.py
git commit -m "feat: cache github readme excerpts"
```

### Task 5: Agentic Link Research Loop

**Files:**

- Create: `pipeline/decision/web_research.py`
- Modify: `pipeline/decision/resolver.py`
- Test: `tests/test_web_research.py`
- Test: `tests/test_resolver.py`

- [ ] **Step 1: Write failing web research tests**

Create `tests/test_web_research.py`:

```python
import sqlite3
import unittest

from pipeline.decision.llm_provider import FakeLLMProvider
from pipeline.decision.schema import init_decision_db


class FakeSearchClient:
    def __init__(self, results_by_query):
        self.results_by_query = results_by_query
        self.calls = []

    def search(self, query, limit=5):
        self.calls.append({"query": query, "limit": limit})
        return self.results_by_query.get(query, [])


class WebResearchTest(unittest.TestCase):
    def test_agentic_research_searches_then_finalizes_valid_github_link(self):
        from pipeline.decision.web_research import research_candidate_link

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        provider = FakeLLMProvider([
            {"action": "search", "query": "Clawdbot GitHub repo", "selected": None, "reason": "need repo"},
            {"action": "final", "query": "", "selected": {"type": "github", "key": "github:owner/clawdbot", "url": "https://github.com/owner/clawdbot", "confidence": 0.91}, "reason": "official repo result"},
        ])
        search = FakeSearchClient({"Clawdbot GitHub repo": [{"title": "Clawdbot", "url": "https://github.com/owner/clawdbot"}]})

        result = research_candidate_link(
            conn,
            entity_key="name:clawdbot",
            evidence_context={"canonical_entity": "Clawdbot", "evidence": ["X potential"]},
            provider=provider,
            search_client=search,
            max_rounds=3,
            max_results=5,
        )

        self.assertEqual(result["resolved_links"][0]["key"], "github:owner/clawdbot")
        self.assertEqual(result["rounds"], 2)
        self.assertEqual(len(search.calls), 1)

    def test_agentic_research_stops_at_max_rounds_and_caches(self):
        from pipeline.decision.web_research import research_candidate_link

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)
        provider = FakeLLMProvider([
            {"action": "search", "query": "first", "selected": None, "reason": "try"},
            {"action": "search", "query": "second", "selected": None, "reason": "try again"},
            {"action": "search", "query": "third", "selected": None, "reason": "last try"},
        ])
        search = FakeSearchClient({"first": [], "second": [], "third": []})

        first = research_candidate_link(conn, entity_key="name:nope", evidence_context={}, provider=provider, search_client=search, max_rounds=3)
        second = research_candidate_link(conn, entity_key="name:nope", evidence_context={}, provider=provider, search_client=search, max_rounds=3)

        self.assertEqual(first["resolved_links"], [])
        self.assertEqual(first["source"], "agentic_link_research")
        self.assertEqual(first, second)
        self.assertEqual(len(search.calls), 3)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
python3 -m unittest tests.test_web_research -v
```

Expected: import failure for `pipeline.decision.web_research`.

- [ ] **Step 3: Implement max-3-round loop**

Create `pipeline/decision/web_research.py` with:

```python
from __future__ import annotations

import sqlite3
from typing import Any

from pipeline.decision.cache import api_cache_key, get_api_cache, put_api_cache, stable_hash
from pipeline.decision.resolver import normalize_resolved_link

RESEARCH_SOURCE = "agentic_link_research"
RESEARCH_WINDOW = "candidate_link"
PROMPT_VERSION = "agentic-link-research-v1"


def research_candidate_link(
    conn: sqlite3.Connection,
    *,
    entity_key: str,
    evidence_context: dict[str, Any],
    provider: Any,
    search_client: Any,
    max_rounds: int = 3,
    max_results: int = 5,
) -> dict[str, Any]:
    rounds = max(1, min(int(max_rounds or 3), 3))
    cache_key, input_hash = _cache_key(entity_key, evidence_context, rounds, max_results)
    cached = get_api_cache(conn, cache_key)
    if cached:
        return cached
    observations: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    for round_index in range(1, rounds + 1):
        action = provider.complete_json(
            task="agentic_link_research",
            prompt_version=PROMPT_VERSION,
            system_prompt=SYSTEM_PROMPT,
            input_payload={
                "entity_key": entity_key,
                "evidence_context": evidence_context,
                "observations": observations,
                "round_index": round_index,
                "max_rounds": rounds,
                "schema": {
                    "action": "search|final|give_up",
                    "query": "string",
                    "selected": {"type": "github|domain|npm", "key": "string", "url": "string", "confidence": "0..1"},
                    "reason": "string",
                },
            },
        )
        action_name = str(action.get("action") or "").strip()
        if action_name == "search":
            query = str(action.get("query") or "").strip()
            results = search_client.search(query, limit=max_results) if query else []
            observations.append({"query": query, "results": list(results)[:max_results]})
            continue
        if action_name == "final":
            selected = action.get("selected") or {}
            link = normalize_resolved_link(selected) if isinstance(selected, dict) else None
            if link:
                links.append(link)
            break
        if action_name == "give_up":
            break
    response = {
        "entity_key": entity_key,
        "resolved_links": links,
        "source": RESEARCH_SOURCE,
        "rounds": len(provider.calls) if hasattr(provider, "calls") else min(len(observations) + (1 if links else 0), rounds),
        "observations": observations,
    }
    put_api_cache(
        conn,
        cache_key=cache_key,
        source=RESEARCH_SOURCE,
        external_id=entity_key,
        window=RESEARCH_WINDOW,
        input_hash=input_hash,
        response=response,
        status="ok",
    )
    return response
```

`SYSTEM_PROMPT` must state:

```text
You identify the official project link for one candidate. Prefer GitHub repo, then official domain, then npm package. Use search only when current observations are insufficient. Return strict JSON only. Do not invent links. If unsure after max rounds, return give_up.
```

Expose `normalize_resolved_link()` from `pipeline/decision/resolver.py` as a public wrapper around the existing `_normalize_search_result()`.

- [ ] **Step 4: Integrate with resolver after direct lookup fails**

Modify `resolve_candidate_links()` in `pipeline/decision/resolver.py`:

```python
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
```

Keep existing behavior first:

1. internal rows,
2. cache/direct search client,
3. if no links and `research_provider is not None`, `search_client is not None`, and `max_research_rounds > 0`, call `research_candidate_link(...)`.

Add to `enrich_classifier_candidates()` signature:

```python
research_provider: Any | None = None,
max_research_rounds: int = 0,
```

Pass `research_context={"entity_key": entity_key, "run_id": run_id}` to `resolve_candidate_links()`.

- [ ] **Step 5: Run passing tests**

Run:

```bash
python3 -m unittest tests.test_web_research tests.test_resolver -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

Run:

```bash
git add pipeline/decision/web_research.py pipeline/decision/resolver.py tests/test_web_research.py tests/test_resolver.py
git commit -m "feat: add bounded agentic link research"
```

### Task 6: Run Decision Wiring For README And Research

**Files:**

- Modify: `pipeline/decision/run_decision.py`
- Modify: `tests/test_decision_runner.py`

- [ ] **Step 1: Write failing runner tests**

Add tests to `tests/test_decision_runner.py`:

```python
    def test_run_from_args_builds_llm_provider_for_agentic_research_limit(self):
        import pipeline.decision.run_decision as runner

        calls = []
        provider = object()

        def fake_run_decision(**kwargs):
            calls.append(kwargs)
            return {"potential_candidates": 0, "edge_watch_candidates": 0}

        args = argparse.Namespace(
            db=":memory:",
            run_id="run",
            source_snapshot_run_id=None,
            rules=str(Path("pipeline/rules.json")),
            export_json=None,
            backfill_github_limit=0,
            classify_hn_limit=0,
            classify_x_limit=0,
            llm_model="deepseek-v4-flash",
            llm_concurrency=1,
            resolver_search_limit=0,
            resolver_research_limit=5,
            resolver_research_rounds=3,
            enrich_readme_limit=0,
        )

        runner.run_from_args(
            args,
            run_decision_fn=fake_run_decision,
            llm_provider_builder=lambda parsed: provider,
        )

        self.assertIs(calls[0]["resolver_research_provider"], provider)
        self.assertEqual(calls[0]["resolver_research_limit"], 5)
        self.assertEqual(calls[0]["resolver_research_rounds"], 3)
```

Add a unit around README enrichment with a fake client if a helper is introduced:

```python
def test_enrich_readmes_for_candidates_uses_verified_github_links(self):
    summary = enrich_candidate_readmes(conn, run_id="run-1", client=fake_client, limit=10)
    self.assertEqual(summary["fetched"], 1)
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
python3 -m unittest tests.test_decision_runner -v
```

Expected: CLI args/keyword assertions fail because they do not exist.

- [ ] **Step 3: Wire explicit knobs**

Modify `run_decision()` signature:

```python
resolver_research_provider: Any | None = None,
resolver_research_limit: int = 0,
resolver_research_rounds: int = 0,
readme_client: Any | None = None,
enrich_readme_limit: int = 0,
```

Pass these into `enrich_classifier_candidates()`.

After final candidates are written, call:

```python
readme_summary = {"fetched": 0, "cached": 0, "skipped": 0}
if readme_client is not None and enrich_readme_limit > 0:
    from pipeline.decision.readme_enrichment import enrich_candidate_readmes
    readme_summary = enrich_candidate_readmes(conn, run_id=run_id, client=readme_client, limit=enrich_readme_limit)
```

Add summary keys:

```python
"resolver_researched": int(resolver_summary.get("researched") or 0),
"readme_fetched": int(readme_summary.get("fetched") or 0),
"readme_cached": int(readme_summary.get("cached") or 0),
```

Modify `run_from_args()`:

- Build the LLM provider when `hn_limit > 0 or x_limit > 0 or resolver_research_limit > 0`.
- Pass `resolver_research_provider=llm_provider if resolver_research_limit > 0 else None`.
- Add parser flags:

```python
parser.add_argument("--resolver-research-limit", type=int, default=0)
parser.add_argument("--resolver-research-rounds", type=int, default=3)
parser.add_argument("--enrich-readme-limit", type=int, default=0)
```

README client construction:

```python
def build_github_readme_client_from_args(args: argparse.Namespace) -> Any:
    from pipeline.decision.readme_enrichment import GitHubReadmeClient
    return GitHubReadmeClient()
```

Only build it when `enrich_readme_limit > 0`.

- [ ] **Step 4: Run passing tests**

Run:

```bash
python3 -m unittest tests.test_decision_runner tests.test_resolver tests.test_readme_enrichment tests.test_web_research -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add pipeline/decision/run_decision.py tests/test_decision_runner.py
git commit -m "feat: wire candidate enrichment knobs"
```

---

## Final Verification

- [ ] Run Python target tests:

```bash
python3 -m unittest \
  tests.test_candidate_context \
  tests.test_dashboard_data_api \
  tests.test_readme_enrichment \
  tests.test_web_research \
  tests.test_resolver \
  tests.test_decision_runner \
  -v
```

- [ ] Run broader Python tests:

```bash
python3 -m unittest discover -s tests -v
```

- [ ] Run frontend tests and build:

```bash
npm test --prefix web
npm run build --prefix web
```

- [ ] Run a bounded local populate command only after unit tests pass:

```bash
python3 -m pipeline.decision.run_decision \
  --db data/hero_radar.sqlite \
  --run-id decision_candidate_context_smoke_20260531 \
  --export-json data/exports/candidates_latest.json \
  --classify-hn-limit 50 \
  --classify-x-limit 50 \
  --llm-concurrency 4 \
  --resolver-search-limit 1 \
  --resolver-research-limit 20 \
  --resolver-research-rounds 3 \
  --enrich-readme-limit 20
```

If no concrete search client is configured in code, run the same command with `--resolver-research-limit 0`; README enrichment can still run for verified GitHub candidates.

## Plan Self-Review

Spec coverage:

- Candidate evidence bullets: Task 1, Task 2, Task 3.
- Candidate context bundle with canonical link, descriptions, README preview, binding confidence: Task 1, Task 4, Task 6.
- README bounds and cache: Task 4, Task 6.
- Candidate Pool UI evidence-first table: Task 3.
- External research for missing repo/site/package links: Task 5, Task 6.
- No L2 score/default feed selection: explicitly excluded.

Placeholder scan:

- No unresolved implementation instructions.
- No rule editor/chatbot/Feed deepdive work is introduced.

Type consistency:

- API keys returned by server: `evidence_bullets`, `evidence_count`, `source_families`, `canonical_link`, `context_preview`, `readme_excerpt_available`, `binding_confidence`.
- UI consumes the same keys.
- README cache uses `source='github_readme'`.
- Agentic research cache uses `source='agentic_link_research'`.
