# Layer 2 Reliability And Agentic Depth Design

Date: 2026-06-01

Status: design draft for user review, implementation not started.

## Goal

Strengthen the Layer 2 Feed pipeline so it can run real Kimi calls reliably,
survive per-candidate failures, produce auditable Feed output, and later support
a bounded multi-round Deepdive agent without making scout/scoring unstable.

The design follows a "reliable first, smarter second" path:

```text
Phase 0: Kimi provider/config handshake
Phase 1: reliability harness
Phase 2: context management
Phase 3: scout/scoring calibration
Phase 4: Deepdive ReAct harness
```

Each phase should be implemented, verified, and reviewed before the next phase
is planned in detail.

## Current Baseline

The current Layer 2 pipeline already has:

- deterministic presentation grouping
- group context assembly
- scheduling by candidate level, evidence hash, and budget
- one-shot Kimi Edge Watch scout
- one-shot Kimi scoring with deterministic local aggregation
- bounded Deepdive v1: plan once, run tools, synthesize once
- Feed API and Daily Feed UI
- small eval harness

Known gaps:

- `KimiProvider` reads env vars but not `pipeline/secrets.local.json`.
- the local key is valid for `https://api.moonshot.cn/v1`, while the provider
  default is `https://api.moonshot.ai/v1`.
- a single scout/scoring/deepdive exception can still fail the entire run.
- stage status and per-candidate error records are not detailed enough.
- context selection is capped but not strategically compacted.
- scout/scoring evals are too small to calibrate real Feed quality.
- Deepdive lacks a verifier that checks whether final claims are supported by
  tool trace evidence.
- Deepdive is not yet a multi-round ReAct loop.

## Non-Goals

- No Layer 3 chat.
- No hosted auth, user accounts, or deployment work.
- No permanent LLM-only entity merge.
- No agent loop for grouping, scheduler, scout, or scoring.
- No unbounded browsing, repo crawling, or recursive tool use.
- No full Feed run as part of handshake tests.
- No printing secrets or full API keys in logs, UI, tests, or command output.

## Core Principle

Only Deepdive should become agentic.

```text
Grouping       deterministic
Context        deterministic with compact summaries
Scheduler      deterministic
Scout          one-shot LLM classifier
Scoring        one-shot LLM axes + deterministic aggregation
Deepdive       bounded multi-round agent, after reliability harness exists
Feed assembly  deterministic
Verification   deterministic + optional LLM verifier where bounded
```

Scout and scoring must remain cheap, stable, and easy to evaluate. Deepdive can
spend more budget because it runs only on the highest-value candidates.

## Phase 0: Kimi Provider And Handshake

Purpose: make real Kimi calls work in a predictable, non-secret-leaking way
before any Feed work runs.

Required behavior:

- load `kimi.api_key` from `pipeline/secrets.local.json` when env vars are not
  present
- load `kimi.base_url` from `pipeline/secrets.local.json` when present
- allow env vars to override the file for temporary experiments
- keep explicit constructor arguments as the highest precedence
- support a no-completion handshake that calls `/v1/models`
- support a tiny completion smoke that returns JSON shape only
- never print key values

Provider precedence:

```text
explicit constructor args
  -> environment variables
  -> pipeline/secrets.local.json
  -> provider defaults
```

Recommended local secret shape:

```json
{
  "kimi": {
    "api_key": "...",
    "base_url": "https://api.moonshot.cn/v1"
  }
}
```

Handshake output should expose only:

```json
{
  "ok": true,
  "base_url_host": "api.moonshot.cn",
  "key_configured": true,
  "models_count": 9
}
```

## Phase 1: Reliability Harness

Purpose: make Layer 2 runs robust and inspectable before adding more
intelligence.

### Run Envelope

Each run should have a single envelope used by all stages:

```text
feed_run_id
decision_run_id
started_at
completed_at
status
config_hash
model_profile
budget_profile
stage_counts
error_counts
note
```

The existing `l2_feed_runs` table can store most of this through structured JSON
fields, but implementation may add dedicated fields if tests show that querying
JSON is awkward.

### Candidate Stage Status

Every candidate group should be able to answer:

```text
Was it grouped?
Was it skipped because evidence was unchanged?
Was it pending because of budget?
Was it scouted?
Was scout successful?
Was it scored?
Was scoring successful?
Was it selected for deepdive?
Did deepdive succeed?
If not, why?
```

Recommended status records:

```text
scheduled
skipped_unchanged
pending_budget
scout_ok
scout_error
scoring_ok
scoring_error
deepdive_selected
deepdive_ok
deepdive_error
budget_exceeded
```

### Failure Isolation

Layer 2 should continue when a single candidate fails.

Examples:

- one scout call returns malformed JSON
- one scoring call times out
- one deepdive tool fails
- one Kimi web search fails
- one GitHub file fetch returns 404

Expected behavior:

```text
record candidate status = *_error
store sanitized error type/message
continue with the next candidate
complete run with status ok_with_errors when at least one candidate succeeded
complete run with status error only when the whole run cannot proceed
```

Whole-run errors include:

- no successful decision run exists
- database schema cannot initialize
- config cannot be parsed
- grouping/context assembly fails before candidate-level isolation can begin

### Tool Error Trace

Every Deepdive tool call should produce a trace item even when it fails:

```json
{
  "tool": "fetch_github_file",
  "arguments": {"repo": "owner/repo", "path": "README.md"},
  "status": "error",
  "error_type": "HTTPError",
  "error": "HTTP 404",
  "result": {}
}
```

The trace must redact secrets and trim large results.

### Budget Telemetry

At minimum record:

```text
groups_total
groups_skipped_unchanged
groups_pending_budget
scout_attempted
scout_ok
scout_error
scoring_attempted
scoring_ok
scoring_error
deepdive_attempted
deepdive_ok
deepdive_error
tool_calls_total
tool_calls_by_family
tool_errors_total
```

This telemetry should be visible through tests and queryable by API or DB.

## Phase 2: Context Management

Purpose: make model input smaller, more consistent, and better targeted.

Current behavior caps evidence rows, but the model still receives broad context.
The stronger design is to separate full audit data from compact prompt context.

### Context Layers

Each candidate group should have:

```text
full_context
  full source/context bundle, bounded but audit-oriented

context_brief
  compact prompt-ready summary for scout/scoring/deepdive

evidence_pack
  top structured evidence rows selected by source family, freshness, and strength

open_questions
  missing facts that Deepdive may investigate
```

### Evidence Selection

Evidence selection should prioritize:

```text
verified cross-source evidence
fresh deterministic metrics
repo/package identity
HN/Product Hunt discussion with points/comments/rank
credible X mentions
README/homepage/package text
source family diversity
negative or caveat evidence
```

Avoid over-weighting:

```text
generic AI news
funding announcements
single-source social buzz
duplicated evidence rows
old metrics with no recent change
```

### Context Brief Contract

Prompt-facing `context_brief` should be structured:

```json
{
  "what_it_is": "...",
  "why_it_mattered_recently": "...",
  "strongest_evidence": [
    {"id": "evidence_row:123", "label": "...", "source": "github"}
  ],
  "weaknesses": ["..."],
  "open_questions": ["..."],
  "source_family_summary": {"github": 2, "hn": 1}
}
```

This becomes the default input to scout/scoring. Deepdive can read more through
tools.

## Phase 3: Scout And Scoring Calibration

Purpose: make cheap one-shot LLM stages reliable enough that Deepdive only runs
on worthy candidates.

### Scout

Scout remains one-shot. It should not call tools.

Scout output should support:

```text
include_in_l2_scoring
scout_score
reason
missing_evidence
drop_reason
risk
confidence
```

Scout eval cases should cover:

```text
weak single-source HN buzz
generic AI/funding news
repo with no product/workflow signal
package with real adoption signal
X-only fuzzy mention
HN post about an article, not a project
new repo with strong README but low social proof
```

### Scoring

Scoring remains one-shot and model output remains axes, not final ranking.

Local deterministic aggregation remains the source of truth for `l2_score`.

Score bands:

```text
85-100: today_focus candidate, likely deepdive
70-84: strong scored candidate
50-69: lower-priority scored/watch item
0-49: not useful for main Feed unless explicitly requested
```

Calibration evals should verify:

```text
project/workflow signal outranks generic news
adoption path outranks raw popularity
technical substance matters
derivative news penalty works
weak evidence produces low confidence
cross-source evidence lifts score
```

## Phase 4: Deepdive ReAct Harness

Purpose: support deeper project understanding for top candidates while staying
bounded and auditable.

Deepdive v1 is:

```text
plan once -> run tools -> synthesize once
```

Deepdive v2 should be:

```text
initialize context brief
repeat up to max_rounds:
  model chooses one action from allowed tools or stop
  local runner validates action and budget
  local runner executes tool
  local runner appends compact observation
final synthesis
verification pass
persist report and trace
```

### Allowed Actions

The model may only choose from the registered tool names plus `stop`.

Each action request must be strict JSON:

```json
{
  "thought_summary": "Short reason for the next action, no hidden chain-of-thought.",
  "action": "fetch_github_file",
  "arguments": {"repo": "owner/repo", "path": "README.md"},
  "stop_reason": ""
}
```

Unknown tools produce an `unavailable` trace item and count against a small
invalid-action budget.

### ReAct Limits

Initial defaults:

```text
max_rounds: 4
max_tool_calls: 12
max_invalid_actions: 2
max_web_search_calls: 3
max_repo_tree_calls: 2
max_repo_file_calls: 8
max_page_fetch_calls: 6
max_hn_thread_calls: 3
max_x_context_calls: 5
max_tool_result_chars: 6000
max_trace_chars_for_synthesis: 24000
```

### Observation Compaction

Tool outputs should not be appended raw forever. Each observation should keep:

```text
tool name
status
compact facts
source URL/ref when available
evidence id or trace id
error type/message if failed
truncation flag
```

### Final Synthesis

Deepdive final output:

```json
{
  "summary": "...",
  "why_now": "...",
  "what_changed": "...",
  "evidence": [
    {"claim": "...", "trace_refs": ["trace:1", "trace:3"]}
  ],
  "adoption_path": "...",
  "risks": ["..."],
  "open_questions": ["..."],
  "recommended_action": "read|watch|ignore|defer",
  "confidence": 0.0
}
```

### Verification Pass

After synthesis, run a verifier that checks:

```text
required fields exist
recommended_action is allowed
evidence claims cite trace refs
trace refs exist
high-confidence claims have supporting observations
unsupported claims are moved to open_questions or report confidence is reduced
```

The verifier can start deterministic. A bounded LLM verifier may be added later,
but only after deterministic checks exist.

## Feed-Level Verification

After all stages, run a cheap Feed verifier:

```text
today_focus items have score/deepdive evidence
no duplicate presentation groups
no item lacks primary reason
no card exposes secrets
unsupported deepdive warnings are counted
budget and error telemetry are present
```

This should not block UI rendering. It should attach warnings to run metadata.

## API And UI Impact

The first implementation phase should not redesign the UI. It may expose:

```text
feed run status
ok_with_errors state
error counts
pending counts
stage telemetry
deepdive warning count
```

Daily Feed cards can remain visually unchanged until the data contract stabilizes.

Settings should eventually expose:

```text
Kimi base URL
handshake status
max rounds for deepdive
verifier enabled/disabled
```

Any larger visual layout change requires separate review.

## Testing Strategy

Every phase must use TDD.

Required test types:

```text
unit tests for config/secrets loading
unit tests for handshake response sanitization
unit tests for per-candidate failure isolation
unit tests for stage telemetry
unit tests for context brief selection
eval tests for scout/scoring calibration
deepdive fake-provider tests for ReAct loop budgets
tool trace tests for failures and truncation
API tests for feed run status shape
web model tests for status/warning display helpers
```

Real Kimi tests must stay small:

```text
handshake /v1/models
one tiny JSON completion smoke
no full Feed run unless explicitly requested
```

## Implementation Sequencing

Do not implement all phases from one giant plan.

Recommended plan sequence:

```text
Plan 1: Phase 0 + Phase 1
  docs/superpowers/plans/2026-06-01-layer2-reliability-harness.md

Plan 2: Phase 2
  docs/superpowers/plans/2026-06-01-layer2-context-management.md

Plan 3: Phase 3
  docs/superpowers/plans/2026-06-01-layer2-scout-scoring-calibration.md

Plan 4: Phase 4
  docs/superpowers/plans/2026-06-01-layer2-deepdive-react-harness.md
```

After each plan is implemented:

```text
run tests
run bounded smoke if applicable
walk through DB/API output
walk through UI if data contract changed
review failure modes
decide what to refine before the next phase
```

## Acceptance Criteria

The reliability/depth roadmap is successful when:

- Kimi handshake works from local secrets without env-only setup.
- `api.moonshot.cn` can be configured without code changes.
- no secret is printed during smoke, tests, API responses, or UI rendering.
- one candidate failure does not fail the entire Layer 2 run.
- run metadata reports stage counts and error counts.
- deepdive tool failures become trace items.
- scout/scoring evals cover common false positives and true positives.
- context brief is smaller and more stable than full context.
- Deepdive ReAct has strict round/tool budgets.
- Deepdive final reports cite trace refs and pass verification.
- Daily Feed can render `ok_with_errors` runs with useful warnings.

