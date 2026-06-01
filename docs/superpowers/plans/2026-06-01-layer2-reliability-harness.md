# Layer 2 Reliability Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Phase 0 and Phase 1 from the Layer 2 reliability roadmap: Kimi local secrets/base URL loading, bounded handshake smoke, per-candidate failure isolation, run telemetry, tool error traces, and Feed API status exposure.

**Architecture:** Keep grouping/context/scheduler/scout/scoring deterministic or single-shot as they are today. Add a small `layer2_harness` module for sanitized errors, stage events, telemetry summaries, and final run status; wire it into the existing runner without redesigning the UI or introducing a ReAct loop.

**Tech Stack:** Python 3 stdlib, SQLite, existing `pipeline.decision` modules, existing web model helpers, pytest/unittest, Node built-in test runner.

---

## File Structure

- Modify `pipeline/decision/kimi_provider.py`
  - load `pipeline/secrets.local.json`
  - support local `kimi.base_url`
  - add a sanitized `/v1/models` handshake
- Modify `pipeline/decision/run_layer2_evals.py`
  - make `--smoke` use provider config, not env-only detection
  - add `--handshake`
- Create `pipeline/decision/layer2_harness.py`
  - stage status constants
  - sanitized error conversion
  - stage event persistence
  - telemetry summary
  - final run status helper
- Modify `pipeline/decision/schema.py`
  - add `l2_stage_events`
  - add indexes
  - allow reset by `feed_run_id`
- Modify `pipeline/decision/run_layer2_feed.py`
  - record stage events
  - isolate candidate-level scout/scoring/deepdive errors
  - set final status to `ok`, `ok_with_errors`, or `error`
  - store stage/error counts in `l2_feed_runs.note`
- Modify `pipeline/decision/layer2_deepdive.py`
  - convert tool exceptions into trace items
  - record tool family/status telemetry in trace metadata
- Modify `pipeline/server.py`
  - include `ok_with_errors` feed runs when selecting latest Feed
  - expose run status, note telemetry, and stage events in `/api/feed`
- Modify `web/src/dashboardModel.js`
  - preserve run status/telemetry in normalized Feed payload
  - keep UI unchanged for this plan
- Test files:
  - `tests/test_kimi_provider.py`
  - `tests/test_layer2_evals.py`
  - `tests/test_decision_schema.py`
  - `tests/test_layer2_harness.py`
  - `tests/test_run_layer2_feed.py`
  - `tests/test_layer2_deepdive.py`
  - `tests/test_feed_api.py`
  - `web/src/feedModel.test.mjs`

---

## Task 1: Kimi Local Secrets And Handshake

**Files:**
- Modify: `pipeline/decision/kimi_provider.py`
- Modify: `pipeline/decision/run_layer2_evals.py`
- Test: `tests/test_kimi_provider.py`
- Test: `tests/test_layer2_evals.py`

- [ ] **Step 1: Write failing provider tests**

Append to `tests/test_kimi_provider.py`:

```python
    def test_kimi_provider_reads_local_secrets_when_env_absent(self):
        from pathlib import Path

        from pipeline.decision import kimi_provider
        from pipeline.decision.kimi_provider import KimiProvider

        with tempfile.TemporaryDirectory() as tmpdir:
            secrets_path = Path(tmpdir) / "secrets.local.json"
            secrets_path.write_text(
                json.dumps(
                    {
                        "kimi": {
                            "api_key": "local-kimi-secret",
                            "base_url": "https://api.moonshot.cn/v1",
                        }
                    }
                )
            )
            with mock.patch.object(kimi_provider, "LOCAL_SECRETS_PATH", secrets_path):
                with mock.patch.dict("os.environ", {}, clear=True):
                    provider = KimiProvider()

        self.assertEqual(provider.api_key, "local-kimi-secret")
        self.assertEqual(provider.base_url, "https://api.moonshot.cn/v1")
        self.assertNotIn("local-kimi-secret", repr(provider))

    def test_kimi_provider_env_overrides_local_secrets(self):
        from pathlib import Path

        from pipeline.decision import kimi_provider
        from pipeline.decision.kimi_provider import KimiProvider

        with tempfile.TemporaryDirectory() as tmpdir:
            secrets_path = Path(tmpdir) / "secrets.local.json"
            secrets_path.write_text(
                json.dumps(
                    {
                        "kimi": {
                            "api_key": "local-kimi-secret",
                            "base_url": "https://api.moonshot.cn/v1",
                        }
                    }
                )
            )
            with mock.patch.object(kimi_provider, "LOCAL_SECRETS_PATH", secrets_path):
                with mock.patch.dict(
                    "os.environ",
                    {
                        "KIMI_API_KEY": "env-kimi-secret",
                        "KIMI_BASE_URL": "https://api.moonshot.ai/v1",
                    },
                    clear=True,
                ):
                    provider = KimiProvider()

        self.assertEqual(provider.api_key, "env-kimi-secret")
        self.assertEqual(provider.base_url, "https://api.moonshot.ai/v1")

    def test_kimi_provider_handshake_returns_sanitized_model_count(self):
        from pipeline.decision.kimi_provider import KimiProvider

        def fake_urlopen(request, timeout):
            self.assertEqual(request.full_url, "https://api.moonshot.cn/v1/models")
            self.assertIn("Bearer secret", request.headers["Authorization"])
            return FakeHttpResponse({"data": [{"id": "kimi-k2.5"}, {"id": "kimi-k2.6"}]})

        provider = KimiProvider(
            api_key="secret",
            base_url="https://api.moonshot.cn/v1",
            timeout=1,
        )
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = provider.handshake()

        self.assertEqual(
            result,
            {
                "ok": True,
                "base_url_host": "api.moonshot.cn",
                "key_configured": True,
                "models_count": 2,
            },
        )
        self.assertNotIn("secret", json.dumps(result))
```

Also add missing imports at the top of `tests/test_kimi_provider.py`:

```python
import tempfile
```

- [ ] **Step 2: Write failing eval CLI tests**

Append to `tests/test_layer2_evals.py`:

```python
    def test_run_handshake_uses_provider_handshake_without_completion(self) -> None:
        from pipeline.decision.run_layer2_evals import run_handshake

        class Provider:
            def handshake(self):
                return {
                    "ok": True,
                    "base_url_host": "api.moonshot.cn",
                    "key_configured": True,
                    "models_count": 9,
                }

        result = run_handshake(provider=Provider())

        self.assertTrue(result["ok"])
        self.assertEqual(result["models_count"], 9)

    def test_run_smoke_uses_provider_configuration_instead_of_env_gate(self) -> None:
        from pipeline.decision.run_layer2_evals import run_smoke

        class Provider:
            provider_name = "kimi"
            model = "kimi-k2.5"
            api_key = "configured"

            def complete_json(self, **kwargs):
                return {"ok": True, "score": 88}

        with mock.patch.dict("os.environ", {}, clear=True):
            result = run_smoke(provider=Provider())

        self.assertFalse(result["skipped"])
        self.assertEqual(result["shape"], ["ok", "score"])
```

Also add missing import at the top of `tests/test_layer2_evals.py`:

```python
from unittest import mock
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_kimi_provider.py tests/test_layer2_evals.py -q
```

Expected: fail because local secrets loading, `handshake()`, injectable `run_handshake()`, and provider-based `run_smoke()` do not exist.

- [ ] **Step 4: Implement Kimi config and handshake**

In `pipeline/decision/kimi_provider.py`, add imports:

```python
from pathlib import Path
from urllib.parse import urlparse
```

Add module constants and helper:

```python
ROOT = Path(__file__).resolve().parents[2]
LOCAL_SECRETS_PATH = ROOT / "pipeline" / "secrets.local.json"


def load_local_kimi_config(path: Path = LOCAL_SECRETS_PATH) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError):
        return {}
    kimi = payload.get("kimi") if isinstance(payload, dict) else {}
    if not isinstance(kimi, dict):
        return {}
    return {
        "api_key": str(kimi.get("api_key") or ""),
        "base_url": str(kimi.get("base_url") or ""),
        "model": str(kimi.get("model") or ""),
    }
```

Update `KimiProvider.__init__()`:

```python
        local_config = load_local_kimi_config()
        self.api_key = (
            api_key
            or os.environ.get("KIMI_API_KEY", "")
            or os.environ.get("MOONSHOT_API_KEY", "")
            or local_config.get("api_key", "")
        )
        self.model = (
            model
            or os.environ.get("KIMI_MODEL", "")
            or local_config.get("model", "")
            or DEFAULT_KIMI_SCORING_MODEL
        )
        self.base_url = (
            base_url
            or os.environ.get("KIMI_BASE_URL", "")
            or os.environ.get("MOONSHOT_BASE_URL", "")
            or local_config.get("base_url", "")
            or DEFAULT_KIMI_BASE_URL
        ).rstrip("/")
```

Add `KimiProvider.handshake()`:

```python
    def handshake(self) -> dict[str, Any]:
        host = urlparse(self.base_url).netloc
        if not self.api_key:
            return {
                "ok": False,
                "base_url_host": host,
                "key_configured": False,
                "models_count": 0,
                "reason": "Kimi key not configured",
            }
        request = urllib.request.Request(
            f"{self.base_url}/models",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return {
                "ok": False,
                "base_url_host": host,
                "key_configured": True,
                "models_count": 0,
                "status": exc.code,
                "reason": "HTTPError",
            }
        except (TimeoutError, urllib.error.URLError, ValueError) as exc:
            return {
                "ok": False,
                "base_url_host": host,
                "key_configured": True,
                "models_count": 0,
                "reason": type(exc).__name__,
            }
        data = body.get("data") if isinstance(body, dict) else []
        return {
            "ok": True,
            "base_url_host": host,
            "key_configured": True,
            "models_count": len(data) if isinstance(data, list) else 0,
        }
```

- [ ] **Step 5: Implement eval handshake and provider-based smoke**

In `pipeline/decision/run_layer2_evals.py`, change `run_smoke()` signature and body:

```python
def run_smoke(model: str = "kimi-k2.5", *, provider: Any | None = None) -> dict[str, Any]:
    active_provider = provider or KimiProvider(model=model, timeout=45, max_retries=1)
    if not getattr(active_provider, "api_key", ""):
        return {"ok": False, "skipped": True, "reason": "Kimi key not configured"}
    response = active_provider.complete_json(
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
    return {
        "ok": bool(response.get("ok", True)),
        "skipped": False,
        "shape": sorted(response.keys()),
    }
```

Add:

```python
def run_handshake(*, provider: Any | None = None, model: str = "kimi-k2.5") -> dict[str, Any]:
    active_provider = provider or KimiProvider(model=model, timeout=20, max_retries=0)
    return active_provider.handshake()
```

Update `main()`:

```python
    parser.add_argument("--handshake", action="store_true")
    if args.handshake:
        result = run_handshake(model=args.model)
    elif args.smoke:
        result = run_smoke(args.model)
    else:
        result = rank_eval_cases(default_eval_cases())
```

- [ ] **Step 6: Run tests and bounded real handshake**

Run:

```bash
python3 -m pytest tests/test_kimi_provider.py tests/test_layer2_evals.py -q
python3 -m pipeline.decision.run_layer2_evals --handshake --model kimi-k2.5
```

Expected: tests pass. Handshake prints sanitized JSON. If local network/key is available, it should return `ok: true`; otherwise it must fail without printing secrets.

- [ ] **Step 7: Commit**

```bash
git add pipeline/decision/kimi_provider.py pipeline/decision/run_layer2_evals.py tests/test_kimi_provider.py tests/test_layer2_evals.py
git commit -m "Add Kimi local handshake"
```

---

## Task 2: Stage Event Schema And Harness Helpers

**Files:**
- Modify: `pipeline/decision/schema.py`
- Create: `pipeline/decision/layer2_harness.py`
- Test: `tests/test_decision_schema.py`
- Test: `tests/test_layer2_harness.py`

- [ ] **Step 1: Write failing schema test**

Append to `tests/test_decision_schema.py`:

```python
    def test_init_creates_layer2_stage_events_table(self):
        import sqlite3

        from pipeline.decision.schema import init_decision_db

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)

        conn.execute(
            """
            insert into l2_stage_events(
              feed_run_id, group_id, stage, status, error_type, error,
              metadata_json, created_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "l2-run",
                "group:repo",
                "scoring",
                "scoring_error",
                "ValueError",
                "bad response",
                "{}",
                "2026-06-01T00:00:00Z",
            ),
        )

        row = conn.execute(
            "select status, error_type from l2_stage_events where feed_run_id = ?",
            ("l2-run",),
        ).fetchone()
        self.assertEqual(row, ("scoring_error", "ValueError"))
```

Extend the existing reset-stage test assertion to include `l2_stage_events`:

```python
        conn.execute(
            "insert into l2_stage_events(feed_run_id, group_id, stage, status, error_type, error, metadata_json, created_at) values (?, ?, ?, ?, ?, ?, ?, ?)",
            ("l2-run", "group:repo", "scoring", "scoring_ok", "", "", "{}", "2026-06-01T00:00:00Z"),
        )
        reset_decision_stage(conn, run_id="l2-run", tables=["l2_stage_events"])
        self.assertEqual(conn.execute("select count(*) from l2_stage_events").fetchone()[0], 0)
```

- [ ] **Step 2: Write failing harness tests**

Create `tests/test_layer2_harness.py`:

```python
from __future__ import annotations

import sqlite3
import unittest

from pipeline.decision.schema import init_decision_db


class Layer2HarnessTest(unittest.TestCase):
    def test_record_stage_event_sanitizes_secret_and_summarizes_counts(self):
        from pipeline.decision.layer2_harness import record_stage_event, stage_summary

        conn = sqlite3.connect(":memory:")
        init_decision_db(conn)

        record_stage_event(
            conn,
            feed_run_id="l2-run",
            group_id="group:repo",
            stage="scoring",
            status="scoring_error",
            error=RuntimeError("Bearer secret-token failed"),
            metadata={"attempt": 1},
        )
        record_stage_event(
            conn,
            feed_run_id="l2-run",
            group_id="group:ok",
            stage="scoring",
            status="scoring_ok",
        )

        row = conn.execute("select error from l2_stage_events where group_id = ?", ("group:repo",)).fetchone()
        summary = stage_summary(conn, "l2-run")

        self.assertNotIn("secret-token", row[0])
        self.assertEqual(summary["stage_counts"]["scoring_error"], 1)
        self.assertEqual(summary["stage_counts"]["scoring_ok"], 1)
        self.assertEqual(summary["error_counts"]["scoring"], 1)

    def test_final_run_status_distinguishes_ok_with_errors(self):
        from pipeline.decision.layer2_harness import final_run_status

        self.assertEqual(final_run_status({"error_total": 0, "success_total": 2}), "ok")
        self.assertEqual(final_run_status({"error_total": 1, "success_total": 2}), "ok_with_errors")
        self.assertEqual(final_run_status({"error_total": 1, "success_total": 0}), "error")
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_decision_schema.py tests/test_layer2_harness.py -q
```

Expected: fail because `l2_stage_events` and `layer2_harness` do not exist.

- [ ] **Step 4: Implement schema**

In `pipeline/decision/schema.py`, add after `l2_feed_items`:

```sql
create table if not exists l2_stage_events (
    id integer primary key autoincrement,
    feed_run_id text not null,
    group_id text,
    stage text not null,
    status text not null,
    error_type text,
    error text,
    metadata_json text not null,
    created_at text not null
);
```

Add index:

```sql
create index if not exists idx_l2_stage_events_run on l2_stage_events(feed_run_id, stage, status);
```

Add reset mapping:

```python
        "l2_stage_events": "feed_run_id",
```

- [ ] **Step 5: Implement harness module**

Create `pipeline/decision/layer2_harness.py`:

```python
from __future__ import annotations

import re
import sqlite3
from typing import Any

from pipeline.decision.schema import to_json, utc_now

SECRET_PATTERNS = [
    re.compile(r"Bearer\s+[A-Za-z0-9._-]+", re.I),
    re.compile(r"sk-[A-Za-z0-9._-]+", re.I),
    re.compile(r"(api[_-]?key\s*[:=]\s*)[A-Za-z0-9._-]+", re.I),
]

SUCCESS_STATUSES = {
    "scheduled",
    "skipped_unchanged",
    "pending_budget",
    "scout_ok",
    "scoring_ok",
    "deepdive_selected",
    "deepdive_ok",
}


def sanitize_text(value: Any, *, max_chars: int = 800) -> str:
    text = str(value or "")
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(lambda match: match.group(1) + "[redacted]" if match.lastindex else "[redacted]", text)
    return text[:max_chars]


def sanitized_error(error: BaseException | str | None) -> dict[str, str]:
    if error is None:
        return {"error_type": "", "error": ""}
    if isinstance(error, BaseException):
        return {
            "error_type": type(error).__name__,
            "error": sanitize_text(error),
        }
    return {"error_type": "Error", "error": sanitize_text(error)}


def record_stage_event(
    conn: sqlite3.Connection,
    *,
    feed_run_id: str,
    group_id: str | None,
    stage: str,
    status: str,
    error: BaseException | str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    err = sanitized_error(error)
    conn.execute(
        """
        insert into l2_stage_events(
          feed_run_id, group_id, stage, status, error_type, error, metadata_json, created_at
        )
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            feed_run_id,
            group_id,
            stage,
            status,
            err["error_type"],
            err["error"],
            to_json(metadata or {}),
            utc_now(),
        ),
    )


def stage_summary(conn: sqlite3.Connection, feed_run_id: str) -> dict[str, Any]:
    rows = conn.execute(
        "select stage, status from l2_stage_events where feed_run_id = ?",
        (feed_run_id,),
    ).fetchall()
    stage_counts: dict[str, int] = {}
    error_counts: dict[str, int] = {}
    success_total = 0
    error_total = 0
    for stage, status in rows:
        stage_counts[status] = stage_counts.get(status, 0) + 1
        if str(status).endswith("_error"):
            error_total += 1
            error_counts[stage] = error_counts.get(stage, 0) + 1
        elif status in SUCCESS_STATUSES:
            success_total += 1
    return {
        "stage_counts": stage_counts,
        "error_counts": error_counts,
        "success_total": success_total,
        "error_total": error_total,
    }


def final_run_status(summary: dict[str, Any]) -> str:
    error_total = int(summary.get("error_total") or 0)
    success_total = int(summary.get("success_total") or 0)
    if error_total and success_total:
        return "ok_with_errors"
    if error_total and not success_total:
        return "error"
    return "ok"
```

- [ ] **Step 6: Run tests**

Run:

```bash
python3 -m pytest tests/test_decision_schema.py tests/test_layer2_harness.py -q
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add pipeline/decision/schema.py pipeline/decision/layer2_harness.py tests/test_decision_schema.py tests/test_layer2_harness.py
git commit -m "Add Layer 2 stage telemetry"
```

---

## Task 3: Runner Failure Isolation And Run Telemetry

**Files:**
- Modify: `pipeline/decision/run_layer2_feed.py`
- Test: `tests/test_run_layer2_feed.py`

- [ ] **Step 1: Write failing runner tests**

Append to `tests/test_run_layer2_feed.py`:

```python
    def test_run_layer2_continues_when_one_scoring_candidate_fails(self):
        from pipeline.decision.llm_provider import FakeLLMProvider
        from pipeline.decision.run_layer2_feed import run_layer2_feed

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hero.sqlite"
            conn = sqlite3.connect(db_path)
            init_decision_db(conn)
            conn.execute(
                "insert into decision_runs(run_id, source_snapshot_run_id, started_at, completed_at, status, config_hash, rule_version, note) values (?, ?, ?, ?, ?, ?, ?, ?)",
                ("decision-run", "source-run", "2026-06-01T00:00:00Z", "2026-06-01T00:01:00Z", "ok", "hash", "rules-v1", ""),
            )
            for entity_id, name in [("entity:bad", "bad/repo"), ("entity:good", "good/repo")]:
                conn.execute(
                    "insert into entities(entity_id, canonical_entity, canonical_key, key_type, first_seen, aliases_json, source_item_ids_json) values (?, ?, ?, ?, ?, ?, ?)",
                    (entity_id, name, f"github:{name}", "github", "2026-06-01T00:00:00Z", "[]", "[]"),
                )
                conn.execute(
                    "insert into potential_candidates(entity_id, run_id, level, fired_families_json, first_trigger_at) values (?, ?, ?, ?, ?)",
                    (entity_id, "decision-run", "potential", '["github"]', "2026-06-01T00:00:00Z"),
                )
            conn.commit()
            conn.close()

            provider = FakeLLMProvider(
                [
                    {"axes": {"momentum": "not-a-number"}},
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
                    {"tool_requests": []},
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
                ]
            )

            summary = run_layer2_feed(
                db_path=db_path,
                decision_run_id="decision-run",
                feed_run_id="l2-errors",
                now="2026-06-01T12:00:00Z",
                provider=provider,
                config={"max_deepdives_per_run": 1, "deepdive_min_l2_score": 0},
            )

            self.assertTrue(summary["ok"])
            self.assertEqual(summary["status"], "ok_with_errors")
            self.assertEqual(summary["scored"], 1)
            self.assertEqual(summary["errors"], 1)

            conn = sqlite3.connect(db_path)
            run_row = conn.execute("select status, note from l2_feed_runs where feed_run_id = ?", ("l2-errors",)).fetchone()
            statuses = [row[0] for row in conn.execute("select status from l2_stage_events where feed_run_id = ? order by id", ("l2-errors",)).fetchall()]
            conn.close()

            self.assertEqual(run_row[0], "ok_with_errors")
            self.assertIn("scoring_error", statuses)
            self.assertIn("scoring_ok", statuses)
            self.assertIn("error_counts", run_row[1])
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_run_layer2_feed.py::Layer2RunnerTest::test_run_layer2_continues_when_one_scoring_candidate_fails -q
```

Expected: fail because one malformed scoring response aborts the entire run and no stage events exist.

- [ ] **Step 3: Implement failure isolation**

In `pipeline/decision/run_layer2_feed.py`, import helpers:

```python
from pipeline.decision.layer2_harness import (
    final_run_status,
    record_stage_event,
    stage_summary,
)
```

After scheduling, record skipped and pending:

```python
        for skipped in schedule.skipped:
            record_stage_event(
                conn,
                feed_run_id=active_feed_run_id,
                group_id=skipped["group_id"],
                stage="schedule",
                status="skipped_unchanged",
                metadata=skipped,
            )
        for group in schedule.pending:
            record_stage_event(
                conn,
                feed_run_id=active_feed_run_id,
                group_id=group.group_id,
                stage="schedule",
                status="pending_budget",
            )
```

Replace the bulk scout call with per-group isolation:

```python
        scouted = []
        for group in schedule.scout_edge_watch:
            try:
                result = scout_edge_watch_groups(
                    conn,
                    feed_run_id=active_feed_run_id,
                    groups=[group],
                    provider=scout_provider,
                )
                status = "scout_ok" if result else "scout_filtered"
                record_stage_event(
                    conn,
                    feed_run_id=active_feed_run_id,
                    group_id=group.group_id,
                    stage="scout",
                    status=status,
                )
                scouted.extend(result)
            except Exception as exc:
                record_stage_event(
                    conn,
                    feed_run_id=active_feed_run_id,
                    group_id=group.group_id,
                    stage="scout",
                    status="scout_error",
                    error=exc,
                )
```

Replace bulk scoring with per-group isolation:

```python
        scored = []
        for group in [*schedule.score_now, *scouted]:
            try:
                result = score_candidate_groups(
                    conn,
                    feed_run_id=active_feed_run_id,
                    groups=[group],
                    provider=scoring_provider,
                )
                scored.extend(result)
                record_stage_event(
                    conn,
                    feed_run_id=active_feed_run_id,
                    group_id=group.group_id,
                    stage="scoring",
                    status="scoring_ok",
                )
            except Exception as exc:
                record_stage_event(
                    conn,
                    feed_run_id=active_feed_run_id,
                    group_id=group.group_id,
                    stage="scoring",
                    status="scoring_error",
                    error=exc,
                )
```

Before each deepdive candidate, record `deepdive_selected`. Run deepdives one
selected candidate at a time:

```python
        selected_for_deepdive = select_deepdives(
            scored,
            max_deepdives=int(cfg.get("max_deepdives_per_run", 10)),
            min_l2_score=float(cfg.get("deepdive_min_l2_score", 70)),
        )
        reports = []
        for row in selected_for_deepdive:
            group = row["group"]
            record_stage_event(
                conn,
                feed_run_id=active_feed_run_id,
                group_id=group.group_id,
                stage="deepdive",
                status="deepdive_selected",
            )
            try:
                reports.extend(
                    run_deepdives(
                        conn,
                        feed_run_id=active_feed_run_id,
                        scored=[row],
                        provider=active_deepdive_provider,
                        max_deepdives=1,
                        min_l2_score=0,
                        tools=active_tools,
                        limits=active_limits,
                    )
                )
                record_stage_event(
                    conn,
                    feed_run_id=active_feed_run_id,
                    group_id=group.group_id,
                    stage="deepdive",
                    status="deepdive_ok",
                )
            except Exception as exc:
                record_stage_event(
                    conn,
                    feed_run_id=active_feed_run_id,
                    group_id=group.group_id,
                    stage="deepdive",
                    status="deepdive_error",
                    error=exc,
                )
```

This requires importing `select_deepdives` and extracting `active_tools` /
`active_limits` variables before the loop.

At the end, summarize and update run status:

```python
        telemetry = stage_summary(conn, active_feed_run_id)
        status = final_run_status(telemetry)
        note = {
            "scored": len(scored),
            "deepdives": len(reports),
            "stage_counts": telemetry["stage_counts"],
            "error_counts": telemetry["error_counts"],
            "success_total": telemetry["success_total"],
            "error_total": telemetry["error_total"],
        }
```

Return:

```python
            "status": status,
            "errors": telemetry["error_total"],
```

- [ ] **Step 4: Run test**

Run:

```bash
python3 -m pytest tests/test_run_layer2_feed.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add pipeline/decision/run_layer2_feed.py tests/test_run_layer2_feed.py
git commit -m "Isolate Layer 2 candidate failures"
```

---

## Task 4: Deepdive Tool Error Trace Hardening

**Files:**
- Modify: `pipeline/decision/layer2_deepdive.py`
- Test: `tests/test_layer2_deepdive.py`

- [ ] **Step 1: Write failing deepdive tool trace test**

Append to `tests/test_layer2_deepdive.py`:

```python
    def test_tool_plan_records_tool_errors_without_aborting(self):
        from pipeline.decision.layer2_deepdive import DeepdiveLimits, _run_tool_plan

        def failing_tool(arguments):
            raise RuntimeError("Bearer secret-token failed")

        trace = _run_tool_plan(
            {"tool_requests": [{"name": "fetch_github_file", "arguments": {"repo": "owner/repo", "path": "README.md"}}]},
            {"fetch_github_file": failing_tool},
            DeepdiveLimits(max_tool_calls=2),
        )

        self.assertEqual(trace[0]["status"], "error")
        self.assertEqual(trace[0]["error_type"], "RuntimeError")
        self.assertNotIn("secret-token", trace[0]["error"])
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_layer2_deepdive.py::Layer2DeepdiveTest::test_tool_plan_records_tool_errors_without_aborting -q
```

Expected: fail because tool exceptions currently abort `_run_tool_plan`.

- [ ] **Step 3: Implement tool error trace**

In `pipeline/decision/layer2_deepdive.py`, import:

```python
from pipeline.decision.layer2_harness import sanitized_error
```

Replace the direct tool call block:

```python
        result = tools[name](arguments)
        total_count += 1
        family_counts[family] = family_counts.get(family, 0) + 1
        trace.append(
            {
                "tool": name,
                "arguments": arguments,
                "status": "ok",
                "result": _trim_result(result, limits.max_tool_result_chars),
            }
        )
```

with:

```python
        total_count += 1
        family_counts[family] = family_counts.get(family, 0) + 1
        try:
            result = tools[name](arguments)
        except Exception as exc:
            err = sanitized_error(exc)
            trace.append(
                {
                    "tool": name,
                    "arguments": arguments,
                    "family": family,
                    "status": "error",
                    "error_type": err["error_type"],
                    "error": err["error"],
                    "result": {},
                }
            )
            continue
        trace.append(
            {
                "tool": name,
                "arguments": arguments,
                "family": family,
                "status": "ok",
                "result": _trim_result(result, limits.max_tool_result_chars),
            }
        )
```

Also add `"family": family` to `budget_exceeded` and `unavailable` trace items.

- [ ] **Step 4: Run tests**

Run:

```bash
python3 -m pytest tests/test_layer2_deepdive.py tests/test_run_layer2_feed.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add pipeline/decision/layer2_deepdive.py tests/test_layer2_deepdive.py
git commit -m "Harden Layer 2 tool traces"
```

---

## Task 5: Feed API Status And Telemetry Exposure

**Files:**
- Modify: `pipeline/server.py`
- Modify: `web/src/dashboardModel.js`
- Test: `tests/test_feed_api.py`
- Test: `web/src/feedModel.test.mjs`

- [ ] **Step 1: Write failing API test**

Append to `tests/test_feed_api.py`:

```python
    def test_query_feed_payload_includes_run_status_and_stage_events(self):
        import pipeline.server as server

        temp, db_path = self.make_db()
        self.addCleanup(temp.cleanup)
        conn = sqlite3.connect(db_path)
        conn.execute("update l2_feed_runs set status = ?, note = ? where feed_run_id = ?", (
            "ok_with_errors",
            json.dumps({"error_counts": {"scoring": 1}, "stage_counts": {"scoring_error": 1}}),
            "l2-run",
        ))
        conn.execute(
            "insert into l2_stage_events(feed_run_id, group_id, stage, status, error_type, error, metadata_json, created_at) values (?, ?, ?, ?, ?, ?, ?, ?)",
            ("l2-run", "group:repo", "scoring", "scoring_error", "ValueError", "bad response", "{}", "2026-06-01T00:00:00Z"),
        )
        conn.commit()
        conn.close()

        with mock.patch.object(server, "DB_PATH", db_path):
            payload = server.query_feed_payload()

        self.assertEqual(payload["run_status"], "ok_with_errors")
        self.assertEqual(payload["telemetry"]["error_counts"]["scoring"], 1)
        self.assertEqual(payload["stage_events"][0]["status"], "scoring_error")
```

- [ ] **Step 2: Write failing web model test**

Append to `web/src/feedModel.test.mjs`:

```javascript
test('normalizeFeedPayload preserves run status and telemetry', () => {
  const normalized = normalizeFeedPayload({
    feed_run_id: 'l2-run',
    run_status: 'ok_with_errors',
    telemetry: { error_counts: { scoring: 1 } },
    stage_events: [{ stage: 'scoring', status: 'scoring_error' }],
    today_focus: [],
    scored_list: [],
  });

  assert.equal(normalized.run_status, 'ok_with_errors');
  assert.equal(normalized.telemetry.error_counts.scoring, 1);
  assert.equal(normalized.stage_events[0].status, 'scoring_error');
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_feed_api.py -q
cd web && npm test -- feedModel.test.mjs
```

Expected: fail because payload/model do not expose status telemetry.

- [ ] **Step 4: Implement API exposure**

In `pipeline/server.py`, update `query_latest_feed_run()`:

```sql
where status in ('ok', 'ok_with_errors')
```

Update the run query in `query_feed_payload()`:

```sql
select decision_run_id, completed_at, started_at, model_profile_json, status, note
```

Fetch stage events:

```python
        stage_events = [
            {
                "group_id": row[0],
                "stage": row[1],
                "status": row[2],
                "error_type": row[3],
                "error": row[4],
                "metadata": json_loads(row[5], {}),
                "created_at": row[6],
            }
            for row in conn.execute(
                """
                select group_id, stage, status, error_type, error, metadata_json, created_at
                from l2_stage_events
                where feed_run_id = ?
                order by id
                """,
                (active_feed_run_id,),
            ).fetchall()
        ]
```

Return:

```python
            "run_status": run[4],
            "telemetry": json_loads(run[5], {}),
            "stage_events": stage_events,
```

Update `_empty_feed_payload()` with:

```python
        "run_status": "",
        "telemetry": {},
        "stage_events": [],
```

- [ ] **Step 5: Implement web model preservation**

In `web/src/dashboardModel.js`, update `normalizeFeedPayload()` return object:

```javascript
    run_status: String(payload?.run_status || ''),
    telemetry: payload?.telemetry || {},
    stage_events: Array.isArray(payload?.stage_events) ? payload.stage_events : [],
```

- [ ] **Step 6: Run tests**

Run:

```bash
python3 -m pytest tests/test_feed_api.py tests/test_dashboard_data_api.py -q
cd web && npm test
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add pipeline/server.py web/src/dashboardModel.js tests/test_feed_api.py web/src/feedModel.test.mjs
git commit -m "Expose Layer 2 run telemetry"
```

---

## Task 6: Verification And Walkthrough

**Files:**
- No implementation files unless verification finds a bug.

- [ ] **Step 1: Run backend tests**

Run:

```bash
python3 -m pytest tests -q
```

Expected: pass.

- [ ] **Step 2: Run frontend tests and build**

Run:

```bash
cd web && npm test && npm run build
```

Expected: pass.

- [ ] **Step 3: Run Kimi handshake**

Run:

```bash
python3 -m pipeline.decision.run_layer2_evals --handshake --model kimi-k2.5
```

Expected: sanitized JSON. On the current local setup, expected success is:

```json
{
  "ok": true,
  "base_url_host": "api.moonshot.cn",
  "key_configured": true,
  "models_count": 9
}
```

If the network/key is unavailable, the result must still be sanitized and must
not print secrets.

- [ ] **Step 4: Run tiny completion smoke**

Run:

```bash
python3 -m pipeline.decision.run_layer2_evals --smoke --model kimi-k2.5
```

Expected: sanitized JSON shape result or a sanitized failure. Do not run full
Feed.

- [ ] **Step 5: Inspect API shape**

If a local backend is running from current code, run:

```bash
curl -sS http://127.0.0.1:8788/api/feed | python3 -m json.tool | head -80
```

Expected: includes `run_status`, `telemetry`, and `stage_events` keys. No
secrets.

- [ ] **Step 6: Commit verification fixes only if needed**

If verification required fixes:

```bash
git add pipeline/decision/kimi_provider.py pipeline/decision/run_layer2_evals.py pipeline/decision/schema.py pipeline/decision/layer2_harness.py pipeline/decision/run_layer2_feed.py pipeline/decision/layer2_deepdive.py pipeline/server.py web/src/dashboardModel.js tests/test_kimi_provider.py tests/test_layer2_evals.py tests/test_decision_schema.py tests/test_layer2_harness.py tests/test_run_layer2_feed.py tests/test_layer2_deepdive.py tests/test_feed_api.py web/src/feedModel.test.mjs
git commit -m "Verify Layer 2 reliability harness"
```

If there are no changes, do not create an empty commit.

---

## Self-Review

- Spec coverage: This plan covers Phase 0 and Phase 1 only: Kimi config/handshake, failure isolation, run telemetry, tool error traces, and API/model exposure. It intentionally does not implement Phase 2 context management, Phase 3 calibration expansion, or Phase 4 Deepdive ReAct.
- Scope guard: No UI redesign, no Feed run, no agent loop, no hosted/deploy work.
- TDD: Every implementation task starts with failing tests, then minimal implementation, then passing tests and commit.
- Secrets: Tests and commands assert sanitized outputs and never print key values.
