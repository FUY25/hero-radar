# Layer 2 Phase 2 Runtime Guardrails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make bounded real Layer 2 runs inspectable and recoverable before deeper Grouping/Scout/Scoring/Deepdive calibration.

**Architecture:** Add a thin telemetry wrapper around existing LLM providers instead of rewriting stage modules. Keep stage-level success/error events as run-status signals, and record `llm_call_*` events as observational telemetry that does not double-count final run errors. Add one scoring repair retry for schema-invalid JSON, plus runner controls for hard smoke caps and stale running cleanup.

**Tech Stack:** Python stdlib, SQLite, existing `pipeline.decision` modules, pytest/unittest.

---

## File Structure

- Modify `pipeline/decision/layer2_harness.py`
  - Add `TelemetryLLMProvider`
  - Add `observation` status handling for `llm_call_started`, `llm_call_ok`, `llm_call_error`
  - Add stale `running` run finalization helper
- Modify `pipeline/decision/layer2_scoring.py`
  - Add one bounded repair retry for validation failures such as missing `axes`
- Modify `pipeline/decision/run_layer2_feed.py`
  - Wrap scout/scoring/deepdive provider calls with `TelemetryLLMProvider`
  - Add per-stage timeout config
  - Add hard total scoring cap
  - Add stale running cleanup config
  - Add CLI aliases for `--no-deepdive`, `--max-total-scoring-candidates`, and timeout knobs
- Tests:
  - `tests/test_layer2_harness.py`
  - `tests/test_layer2_scoring.py`
  - `tests/test_run_layer2_feed.py`

---

### Task 1: LLM Call Telemetry Wrapper

**Files:**
- Modify: `pipeline/decision/layer2_harness.py`
- Test: `tests/test_layer2_harness.py`

- [ ] **Step 1: Write failing tests**

Add tests that create an in-memory decision DB and assert:

```python
def test_telemetry_provider_records_llm_call_started_and_ok(self):
    from pipeline.decision.layer2_harness import TelemetryLLMProvider, stage_summary

    class Provider:
        provider_name = "fake"
        model = "fake-json"
        timeout = 90

        def complete_json(self, **kwargs):
            return {"ok": True, "score": 88}

    conn = sqlite3.connect(":memory:")
    init_decision_db(conn)
    wrapped = TelemetryLLMProvider(
        Provider(),
        conn=conn,
        feed_run_id="l2-run",
        group_id="group:repo",
        stage="scoring",
        timeout_seconds=12,
    )

    self.assertEqual(wrapped.complete_json(task="layer2_scoring", prompt_version="v1", input_payload={}), {"ok": True, "score": 88})
    rows = conn.execute("select status, metadata_json from l2_stage_events order by id").fetchall()
    self.assertEqual([row[0] for row in rows], ["llm_call_started", "llm_call_ok"])
    self.assertIn('"timeout_seconds":12', rows[0][1].replace(" ", ""))
    self.assertEqual(stage_summary(conn, "l2-run")["error_total"], 0)
```

```python
def test_telemetry_provider_records_llm_call_error_without_secret(self):
    from pipeline.decision.layer2_harness import TelemetryLLMProvider, stage_summary

    class Provider:
        provider_name = "fake"
        model = "fake-json"

        def complete_json(self, **kwargs):
            raise RuntimeError("Bearer secret-token timed out")

    conn = sqlite3.connect(":memory:")
    init_decision_db(conn)
    wrapped = TelemetryLLMProvider(
        Provider(),
        conn=conn,
        feed_run_id="l2-run",
        group_id="group:repo",
        stage="deepdive",
    )

    with self.assertRaises(RuntimeError):
        wrapped.complete_json(task="layer2_deepdive_plan", prompt_version="v1", input_payload={})

    rows = conn.execute("select status, error from l2_stage_events order by id").fetchall()
    self.assertEqual([row[0] for row in rows], ["llm_call_started", "llm_call_error"])
    self.assertNotIn("secret-token", rows[1][1])
    self.assertEqual(stage_summary(conn, "l2-run")["error_total"], 0)
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
python3 -m pytest tests/test_layer2_harness.py -q
```

Expected: fail because `TelemetryLLMProvider` does not exist.

- [ ] **Step 3: Implement minimal wrapper**

Add `OBSERVATION_STATUSES`, skip them in `stage_summary()`, and add `TelemetryLLMProvider` with delegated attributes, started/ok/error events, duration metadata, optional timeout override, and sanitized errors.

- [ ] **Step 4: Verify**

Run:

```bash
python3 -m pytest tests/test_layer2_harness.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add pipeline/decision/layer2_harness.py tests/test_layer2_harness.py
git commit -m "Add Layer 2 LLM call telemetry"
```

---

### Task 2: Scoring Schema Repair Retry

**Files:**
- Modify: `pipeline/decision/layer2_scoring.py`
- Test: `tests/test_layer2_scoring.py`

- [ ] **Step 1: Write failing test**

Add:

```python
def test_scoring_repairs_missing_axes_once(self):
    from pipeline.decision.layer2_scoring import score_candidate_groups

    conn = sqlite3.connect(":memory:")
    init_decision_db(conn)
    provider = FakeLLMProvider([
        {"primary_reason": "Missing axes"},
        {
            "axes": {
                "momentum": 80,
                "workflow_shift": 80,
                "technical_substance": 80,
                "adoption_path": 80,
                "confidence": 80,
                "derivative_news_penalty": 0,
            },
            "primary_reason": "Repaired",
            "topic_tags": ["agent workflow"],
            "rationale_short": "Repaired response.",
            "caveats": [],
        },
    ])
    group = CandidateGroup(...)
    scores = score_candidate_groups(conn, feed_run_id="l2-run", groups=[group], provider=provider)
    self.assertEqual(scores[0]["primary_reason"], "Repaired")
    self.assertEqual([call["task"] for call in provider.calls], ["layer2_scoring", "layer2_scoring_repair"])
```

- [ ] **Step 2: Run failing test**

Run:

```bash
python3 -m pytest tests/test_layer2_scoring.py::Layer2ScoringTest::test_scoring_repairs_missing_axes_once -q
```

Expected: fail because missing `axes` raises immediately.

- [ ] **Step 3: Implement minimal repair**

Catch validation `ValueError`, call provider again with task `layer2_scoring_repair`, include `validation_error` and a compact previous response shape, validate repaired response, then persist as usual.

- [ ] **Step 4: Verify**

Run:

```bash
python3 -m pytest tests/test_layer2_scoring.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add pipeline/decision/layer2_scoring.py tests/test_layer2_scoring.py
git commit -m "Repair Layer 2 scoring schema responses"
```

---

### Task 3: Runner Guardrails And Hard Smoke Controls

**Files:**
- Modify: `pipeline/decision/run_layer2_feed.py`
- Test: `tests/test_run_layer2_feed.py`

- [ ] **Step 1: Write failing tests**

Add tests that assert:

```python
def test_run_layer2_applies_total_scoring_cap_after_scout_promotions(self):
    summary = run_layer2_feed(..., config={"max_scored_candidates": 2, "max_total_scoring_candidates": 1, "max_deepdives_per_run": 0})
    self.assertEqual(summary["scored"], 1)
    self.assertIn(("scoring", "pending_budget"), stage_rows)
```

```python
def test_run_layer2_marks_stale_running_runs_before_starting(self):
    conn.execute("insert into l2_feed_runs(... status='running' started_at='2026-06-01T00:00:00Z' ...)")
    run_layer2_feed(..., config={"finalize_stale_running_before": "2026-06-01T01:00:00Z", "max_deepdives_per_run": 0})
    self.assertEqual(old_run_status, "error")
```

```python
def test_cli_no_deepdive_and_timeout_knobs_map_to_config(self):
    args = parse_args(["--no-deepdive", "--scout-timeout-seconds", "7", "--max-total-scoring-candidates", "2"])
    config = config_from_args(args)
    self.assertEqual(config["max_deepdives_per_run"], 0)
    self.assertEqual(config["scout_timeout_seconds"], 7)
    self.assertEqual(config["max_total_scoring_candidates"], 2)
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
python3 -m pytest tests/test_run_layer2_feed.py -q
```

Expected: fail because the cap, stale cleanup, and parser helpers do not exist.

- [ ] **Step 3: Implement minimal runner changes**

Add:

- `parse_args(argv)`
- `config_from_args(args)`
- `--no-deepdive`
- `--max-total-scoring-candidates`
- `--scout-timeout-seconds`
- `--scoring-timeout-seconds`
- `--deepdive-timeout-seconds`
- `--web-search-timeout-seconds`
- `--finalize-stale-running-before`
- `TelemetryLLMProvider` wrapping per group/stage call
- total scoring candidate cap after scout promotions
- stale running cleanup before inserting the new run

- [ ] **Step 4: Verify**

Run:

```bash
python3 -m pytest tests/test_run_layer2_feed.py tests/test_layer2_harness.py tests/test_layer2_scoring.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add pipeline/decision/run_layer2_feed.py tests/test_run_layer2_feed.py
git commit -m "Add Layer 2 runtime guardrails"
```

---

### Task 4: Verification

**Files:**
- No planned code changes.

- [ ] **Step 1: Run full Python tests**

Run:

```bash
python3 -m pytest tests -q
```

Expected: pass.

- [ ] **Step 2: Run Kimi bounded API checks**

Run:

```bash
python3 -m pipeline.decision.run_layer2_evals --handshake --model kimi-k2.5
python3 -m pipeline.decision.run_layer2_evals --smoke --model kimi-k2.5
```

Expected: handshake and smoke pass if key is configured; skip only if key is absent.

- [ ] **Step 3: Commit verification note only if code changed**

No commit is needed if verification does not require code changes.
