# Layer 2 Real Run Harness Lessons

Date: 2026-06-01

This note captures what the first bounded real Layer 2 Kimi Feed runs taught us about the current harness, where the pipeline is already working, and what Phase 2 should strengthen next.

## Real Run Summary

Two bounded real runs were executed against decision run `decision_v2_today_20260601T000000Z`.

### Run 1: Deepdive Smoke

- Feed run: `l2_real_smoke_20260601T115725Z`
- Mode: small real run with scout, scoring, and `deepdive_limit=1`
- Started: `2026-06-01T11:57:31Z`
- Ended: manually interrupted and marked `error` at `2026-06-01T12:07:07Z`
- Groups built: `178`
- Scout results: `3`
- Scores written before interrupt: `5`
- Deepdives written: `0`
- Observed stall: `layer2_deepdive_plan` waiting on Kimi HTTPS response

This run proved the pipeline can build real groups, scout, and score, but also showed that a single deepdive planning call can block the whole bounded run for too long.

### Run 2: Scored-Only Smoke

- Feed run: `l2_real_scored_20260601T120744Z`
- Mode: small real run with `deepdive_limit=0`
- Started: `2026-06-01T12:08:01Z`
- Completed: `2026-06-01T12:14:26Z`
- Final status: `ok_with_errors`
- Groups built: `178`
- Scored items: `3`
- Deepdives: `0`
- Stage events: `180`
- Scoring errors: `2`

The API and UI can now read a real scored Feed run. `/api/feed` returns:

```json
{
  "feed_run_id": "l2_real_scored_20260601T120744Z",
  "run_status": "ok_with_errors",
  "error_counts": {
    "scoring": 2
  },
  "stage_events_count": 180,
  "today_focus_count": 0,
  "scored_count": 3
}
```

Top scored items:

| Rank | Candidate | Level | Score | Lesson |
| --- | --- | --- | ---: | --- |
| 1 | `anthropics/knowledge-work-plugins` | `high_potential` | `79.65` | Good intelligence-feed candidate: official repo, clear workflow shift, strong acceleration. |
| 2 | `anthropics/claude-plugins-official` | `high_potential` | `67.5` | Good ecosystem signal, but partly overlaps semantically with rank 1, so grouping/dedup needs review. |
| 3 | `88lin/video_vip` | `high_potential` | `19.25` | Correctly downranked, but still appears in a tiny scored-only run; Phase 2 needs quality gates and section thresholds. |

## What Worked

### 1. The run-level harness is now useful.

The pipeline no longer behaves like an all-or-nothing batch. Candidate-level failures are captured as stage events, and the run can finish as `ok_with_errors`.

Useful fields now exist:

- `l2_feed_runs.status`
- `l2_feed_runs.note`
- `l2_stage_events.stage`
- `l2_stage_events.status`
- `l2_stage_events.error_type`
- `l2_stage_events.error`
- `/api/feed.run_status`
- `/api/feed.telemetry`
- `/api/feed.stage_events`

This is enough to inspect run health without reading raw logs.

### 2. Scoring can produce useful intelligence signals.

The two Anthropic plugin candidates were materially relevant to the intended Feed direction: repo-native, tool/workflow oriented, and tied to a broader change in AI agent extensibility.

This suggests the general scoring rubric is pointed in the right direction.

### 3. `ok_with_errors` is the right status model.

The scored-only run produced usable Feed data while still surfacing two scoring errors. This is preferable to failing the entire run.

The next step is not to remove `ok_with_errors`; it is to make it more interpretable and actionable.

## What Broke Or Looked Weak

### 1. Deepdive can block too long.

The deepdive smoke stalled while waiting for the Kimi response to `layer2_deepdive_plan`.

Current issue:

- The run is bounded by candidate count and tool budgets.
- But a single LLM call can still consume too much wall-clock time.
- While the request is in flight, there is no heartbeat event showing which stage/candidate is active.

Needed harness improvements:

- Per-stage timeout policy.
- Per-call attempt telemetry.
- A `deepdive_started` or `llm_call_started` event before long calls.
- A bounded fallback when deepdive planning does not return in time.
- Run finalization on interrupt or timeout so no run remains stuck as `running`.

### 2. Scoring schema validation is too brittle.

Two candidates failed with:

```text
ValueError: scoring response missing axes
```

Current issue:

- Kimi sometimes returns JSON that is parseable but incomplete.
- The current scorer validates and records `scoring_error`, which is good.
- But it does not repair, retry with validation feedback, or preserve enough response-shape metadata for calibration.

Needed harness improvements:

- Schema-specific validation error categories.
- One bounded repair retry for missing required fields.
- Record response keys and validation failure reason in sanitized metadata.
- Eval fixtures that intentionally omit `axes`, `l2_score`, `primary_reason`, or `topic_tags`.

### 3. Scoring limit semantics are non-obvious.

The first run used `--scoring-limit 3` but wrote 5 scores.

This was not a CLI bug. Current semantics are:

- `--scoring-limit` limits scheduled `score_now` groups.
- Edge Watch groups that pass scout are appended to scoring.
- So total scoring candidates can be `score_now + scout_ok`.

Needed harness improvements:

- Rename internal counters or expose telemetry as:
  - `score_now_budget`
  - `edge_scout_budget`
  - `scoring_total_attempted`
- Make CLI help explicit.
- Add a hard optional `max_total_scoring_candidates` if smoke runs need tighter control.

### 4. Grouping/dedup likely needs a closer look.

The two top scored candidates were:

- `anthropics/knowledge-work-plugins`
- `anthropics/claude-plugins-official`

They may be legitimately separate repos, but they are also part of one broader Claude plugin ecosystem story.

Needed grouping review:

- Determine when same-ecosystem repos should remain separate.
- Determine when Feed should merge related repos into one intelligence item.
- Add grouping eval fixtures for official repo, registry repo, plugin repo, and third-party plugin references.

### 5. Low-quality viral utility can still enter the scored list.

`88lin/video_vip` was correctly scored low, but it still appeared because the run was tiny and scored-only.

Needed Feed selection improvements:

- Minimum score threshold for `scored_list`.
- Policy filters for legally/ethically dubious consumer bypass tools.
- Stronger distinction between "viral repo" and "strategic workflow/product signal".
- Maybe section-level gates:
  - `today_focus`: high score plus deepdive or strong multi-source evidence.
  - `scored`: above threshold and not policy-filtered.
  - `watch`: low-confidence or low-quality but interesting raw signal.

### 6. API has telemetry, but UI does not expose run health yet.

The API returns `run_status`, `telemetry`, and `stage_events`, but the Feed UI does not yet show them.

Needed UI decision:

- Add a compact run-health strip only after user approval.
- Candidate display should remain focused on signal cards, not debug logs.
- Debug stage events may belong in a collapsible diagnostics panel or Settings/Run History view.

## Phase 2 Direction

Yes: Phase 2 should optimize the pipeline one stage at a time.

However, the real run suggests one small cross-stage harness pass should come first. Otherwise each stage walkthrough will be harder because long LLM calls and schema errors are not yet visible enough.

Recommended order:

1. Phase 2.0: Cross-stage runtime guardrails
2. Phase 2A: Grouping walkthrough and dedup calibration
3. Phase 2B: Scout calibration
4. Phase 2C: Scoring schema repair and ranking calibration
5. Phase 2D: Deepdive bounded agent harness
6. Phase 2E: API/UI run health surfacing

## Phase 2.0: Cross-Stage Runtime Guardrails

Goal: make every real run bounded, inspectable, and recoverable before deeper quality work.

Changes to consider:

- Provider-level timeout config per stage:
  - scout: short
  - scoring: medium
  - deepdive plan: medium
  - deepdive synthesis: medium
  - web search: short
- Stage attempt telemetry:
  - `llm_call_started`
  - `llm_call_ok`
  - `llm_call_error`
  - duration in metadata
  - model and task name, but no secrets
- Finalize stale `running` runs:
  - CLI cleanup command or runner startup check.
  - Mark old `running` runs as `error` with a clear note.
- Hard smoke mode:
  - total wall-clock limit
  - hard total scoring candidate cap
  - optional `--no-deepdive` alias for clarity

Why first:

- It directly addresses the deepdive stall.
- It gives better data for every later stage walkthrough.
- It reduces the risk of long-running real Kimi experiments.

## Phase 2A: Grouping Walkthrough

Questions:

- Are related repos being grouped too separately?
- Are unrelated repos being grouped together?
- Is entity canonicalization stable across GitHub, HN, X, packages, and Product Hunt?
- Does group context include enough evidence for scout/scoring without overloading the prompt?

Artifacts to produce:

- Grouping eval fixtures for:
  - same repo across multiple source families
  - official repo plus docs/homepage
  - official repo plus plugin registry
  - third-party integration repo
  - viral low-quality utility repo
- A grouping inspection report for the Anthropic plugin candidates from this run.

Success criteria:

- Obvious duplicates are merged.
- Legitimately separate projects stay separate.
- Same-ecosystem stories can be represented either as one group or as linked related groups, but the behavior is intentional.

## Phase 2B: Scout Calibration

Questions:

- Which Edge Watch candidates should be promoted into scoring?
- Are scout decisions too permissive or too conservative?
- Does scout ask for the right missing context?

Artifacts to produce:

- Scout eval set with expected decisions:
  - promote
  - filter
  - needs context
- Metrics:
  - promoted count
  - filtered count
  - schema error count
  - average latency
- Calibration report comparing Kimi scout reasons against expected labels.

Success criteria:

- Edge Watch promotions are explainable.
- Weak single-source candidates mostly stay out of scoring.
- Good emerging workflow/tooling signals make it through.

## Phase 2C: Scoring Calibration

Questions:

- Is the scoring rubric producing useful rank order?
- Are schema failures recoverable?
- Are low-quality viral projects downranked enough?
- Do strong workflow/product signals consistently score above generic AI news?

Artifacts to produce:

- Schema repair retry for incomplete Kimi JSON.
- Scoring eval fixtures covering:
  - strong repo-native workflow shift
  - official ecosystem/platform signal
  - low-quality viral utility
  - generic company/news item
  - single-source HN release
  - package adoption signal
- Score distribution sanity report.

Success criteria:

- Missing `axes` no longer permanently drops a candidate without one repair attempt.
- Scores are stable enough for ranking.
- Low-quality viral repos are below Feed threshold.
- Strong workflow/tooling signals reliably surface.

## Phase 2D: Deepdive Agent Harness

Questions:

- Does deepdive actually understand the project deeply?
- Are tool calls bounded and useful?
- Can the model recover when a tool fails?
- Does synthesis cite evidence from candidate context or tool trace?

Artifacts to produce:

- Tool registry with explicit family budgets.
- Context manager for candidate context, score context, and prior evidence.
- Bounded plan/observe/synthesize loop.
- Deepdive validation step:
  - required fields present
  - evidence is grounded
  - no unsupported claims
  - risks/open questions are non-empty when confidence is low
- Timeout and fallback behavior:
  - if plan times out, skip deepdive with `deepdive_error`
  - if tools fail, synthesize from available context only if enough context exists

Success criteria:

- A single deepdive cannot block the whole run.
- Tool traces explain what the model saw.
- Deepdive output is materially better than scoring rationale, not a longer paraphrase.

## Phase 2E: API/UI Run Health

Questions:

- How much run health should be visible in Daily Feed?
- Where should debugging telemetry live?

Possible UI:

- Compact Feed health strip:
  - `ok`
  - `ok_with_errors`
  - scored count
  - deepdive count
  - top error category
- Diagnostics drawer or Settings/Run History:
  - stage counts
  - error counts
  - per-stage events

Constraint:

- Any meaningful Feed UI layout or text hierarchy change should be confirmed in Chinese before implementation.

## Immediate Next Recommendation

Do not start with a broad Deepdive ReAct redesign.

Start Phase 2 with a small runtime guardrails plan:

1. Add per-stage timeout and duration telemetry.
2. Add scoring schema repair retry for missing fields.
3. Add a true hard-smoke mode with total scoring cap and no-deepdive alias.
4. Re-run a scored-only smoke and a one-deepdive smoke.
5. Then begin stage-by-stage walkthrough from Grouping.

This keeps the original stage-by-stage plan intact while reducing the risk and ambiguity of every real Kimi experiment.
