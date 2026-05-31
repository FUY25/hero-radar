# Layer 1.5 Evidence And Layer 2 Kimi Feed Design

Date: 2026-05-31

Status: design approved in conversation, implementation not started.

## Goal

Turn the existing Candidate Pool into a trustworthy daily reading surface.

The Candidate Pool explains why an entity exists. Layer 2 decides what is worth
reading today, using Kimi for semantic scout/scoring/deepdive and deterministic
code for grouping, caching, scheduling, budget limits, and final score
aggregation.

This document covers Layer 1.5 evidence/context and Layer 2 Feed only.

Terminology note: this document uses `High` as shorthand for the existing
deterministic level `high_potential`.

## Non-Goals

- No Layer 3 agentic chat.
- No rule/prompt editor.
- No automated OS cron setup. `pipeline/run_daily.py` may call Layer 2, but
  installing a system scheduler is a separate operational step.
- No mutation of deterministic Candidate Pool levels from Layer 2 output.
- No LLM-only permanent entity merge. Layer 2 may create presentation groups and
  merge proposals, but it must not silently rewrite entity identity.
- No unbounded web browsing, repo crawling, or LLM agent loops.

The only Feed feedback action in scope is tiny thumbs up/down feedback on
whether the Feed selection was useful.

## Product Boundary

```text
items/source rows
  -> entity resolution
  -> classifiers / certifiers / resolver / backfill
  -> evidence_rows
  -> candidate_evidence_summary + candidate_context_bundle
  -> Candidate Pool
  -> presentation grouping / dedupe
  -> Layer2 eligibility + scheduler
  -> Kimi Edge Watch Scout
  -> Kimi multi-axis scoring
  -> deterministic score aggregation
  -> top <= 10 bounded Kimi deepdives
  -> Daily Feed
```

Layer 1 answers: "Does this entity qualify for the candidate universe?"

Layer 1.5 answers: "Can we explain and package the candidate clearly?"

Layer 2 answers: "Which qualified projects should I look at today, and why?"

## Locked Decisions

1. Kimi/Moonshot is the Layer 2 model family.
   - Edge Watch scout: cheap Kimi profile, default `kimi-k2.5`.
   - Scoring: cheap Kimi profile, default `kimi-k2.5`.
   - Deepdive: stronger Kimi profile, default `kimi-k2.6`.
   - The UI settings must allow the model names to be changed.

2. DeepSeek remains valid for Layer 1 source classifiers.
   - Existing HN/X/npm classifier code can keep using DeepSeek.
   - Layer 2 Feed/scoring/deepdive must not route through DeepSeek by default.

3. Deterministic code does not decide semantic value in Layer 2.
   - Deterministic code handles eligibility, grouping, caching, scheduling,
     source/evidence availability, and budget.
   - Kimi handles semantic value judgments: "worth scoring", workflow shift,
     technical substance, adoption path, and deepdive synthesis.

4. Potential and High candidates go to Layer 2 scoring by default.
   - Edge Watch candidates go through Kimi Edge Watch Scout first.
   - Edge Watch candidates that pass scout enter the same scoring path as
     Potential/High candidates.

5. Layer 2 does not change Candidate Pool level.
   - `potential`, `high_potential`, and `edge_watch` remain Layer 1 outputs.
   - Layer 2 writes separate Feed/scout/scoring/deepdive records.

## Layer 1.5: Candidate Evidence Summary

Every Potential, High, and Edge Watch candidate gets a compact evidence summary
derived from atomic `evidence_rows`, source rows, resolver outputs, classifier
outputs, and backfill evidence.

The summary is not a replacement for `evidence_rows`. It is the short UI and L2
context layer over the audit log.

Default UI contract:

```text
Show at most 3 short bullets, then "+N" if more exist.
Each bullet should be short enough to scan in a table/list row.
Every bullet must be understandable to a human, not a raw rule identifier.
```

Examples:

```text
GitHub +1.3k stars in 24h
HN max 142 points in 7d
X: credible accounts recommend this project [LLM]
Verified GitHub + HN within 48h
```

Each bullet carries structured provenance:

```json
{
  "label": "X: credible accounts recommend this project",
  "family": "x_social",
  "origin_type": "source_classifier",
  "provenance_badge": "LLM",
  "strength": "potential",
  "source_refs": ["evidence_row:456", "tweet:..."]
}
```

Allowed `origin_type` values:

```text
deterministic_rule
source_classifier
resolver
backfill
cross_source_rule
```

Layer 2 analysis is not why-in-pool evidence. It belongs to Feed scoring and
analysis.

## Layer 1.5: Candidate Context Bundle

Every Potential, High, and Edge Watch candidate gets a context bundle for Layer 2
scout/scoring, Feed display, and Candidate Pool expansion.

The bundle includes:

```text
entity_id
canonical name
canonical key
canonical link / jump link
official links: GitHub, homepage, npm, Product Hunt, HN, source links
source descriptions, taglines, HN titles, X snippets
README excerpt when a verified GitHub repo exists
homepage meta description when cheap and bounded
evidence summary
full evidence rows, trimmed and provenance-marked
source family count
verified cross-source status
resolver / binding confidence
freshness
```

README behavior:

```text
README enrichment is cheap context enrichment, not deepdive.
Fetch only for verified GitHub repos attached to candidates entering Layer 2.
Cache the result.
Store a bounded excerpt, not an unbounded document.
UI shows README collapsed by default.
```

Recommended bounds:

```text
Stored README excerpt: 8k-40k chars when available.
Scoring context slice: 8k-16k chars.
Deepdive context slice: larger, but still capped per model/profile config.
Default UI preview: 600-1000 chars.
Candidate Pool list: no full README column.
Candidate Pool row expansion / drawer: collapsed README section.
Feed card: collapsed README excerpt section when available.
Scored Feed list row: short description or README preview only.
```

If README is unavailable, use the best available source description or homepage
meta description.

## Presentation Grouping / Dedupe

Before Layer 2 scout/scoring, candidates are grouped for presentation.

Purpose:

```text
Avoid scoring/displaying the same project multiple times when GitHub, npm, HN,
Product Hunt, X, or domain evidence landed on separate entity_ids.
```

Grouping rules:

```text
Strong deterministic group:
  same GitHub repo key
  same npm package key
  same approved resolver alias
  same canonical link after normalization

Medium deterministic group:
  same domain key when the domain is not a shared/content platform
  same Product Hunt website / source link target

LLM-assisted proposal:
  semantically same product but different names/links
  written as a merge proposal or presentation-only group reason
  never silently rewrites alias_links
```

Presentation grouping is allowed to combine rows for Feed display and Layer 2
scoring. Permanent entity merge is not automatic.

## Layer 2 Eligibility + Scheduler

The scheduler is deterministic but not a semantic hard filter.

It may decide:

```text
which candidate groups are already scored with the same evidence hash
which groups have new evidence since the last Layer 2 run
which groups fit today's Layer 2 budget
which groups need README/homepage/source context before model calls
which groups should be queued for the next run because budget is exhausted
```

It must not decide:

```text
this project is semantically uninteresting
this project is just a toy
this does not represent workflow shift
this is not worth reading
```

Those judgments belong to Kimi scout/scoring.

Only mechanical exclusions are allowed:

```text
no evidence rows or source refs
already grouped into another group for this run
same evidence hash already scored and no new evidence
blocked source/domain noise with no classifier/resolver evidence
outside configured freshness window with no new evidence
```

Potential/High groups enter scoring by default. Edge Watch groups enter Kimi
Edge Watch Scout first.

## Kimi Edge Watch Scout

The Edge Watch Scout finds valuable candidates whose deterministic evidence is
not strong enough for Potential.

Inputs:

```text
presentation group summary
candidate level = edge_watch
evidence bullets and full trimmed evidence rows
source descriptions / HN titles / X snippets
canonical link and binding confidence
README or source description when available
freshness and source-family summary
```

Output schema:

```json
{
  "include_in_l2_scoring": true,
  "scout_score": 0.72,
  "reason": "Early project with concrete agent workflow evidence, but only one source family.",
  "needed_context": ["readme", "hn_comments"],
  "risk": "single-source evidence",
  "confidence": 0.7
}
```

Scout output affects only Layer 2 inclusion. It does not promote the candidate in
the deterministic Candidate Pool.

## Layer 2 Multi-Axis Scoring

Kimi scores every Potential/High group and every Edge Watch group admitted by
scout.

The score answers:

```text
How worth reading is this candidate today, given movement, evidence quality, and
semantic opportunity?
```

Kimi returns axis scores. Deterministic code validates and aggregates them.

Internal scoring axes:

```text
Momentum
  Is something objectively moving now?
  Evidence includes velocity, source-family heat, fresh source rows, HN/X/PH/npm/GitHub movement.

Workflow Shift
  Does this change what users can do, or how a task gets done?

Technical Substance
  Is there real implementation, architecture, code, protocol, or technical unlock?

Adoption Path
  Can this spread through real workflows or channels?
  Examples: CLI, package, docs, integrations, migration path, community, onboarding.

Confidence
  Do we trust the entity binding, evidence, classifier claims, and available context?

Derivative / News Risk
  Is this mostly a generic wrapper, content/news item, reannouncement, or weakly linked topic?
  This is a penalty, not a positive axis.
```

Default deterministic aggregation:

```text
l2_score =
  0.25 * momentum
+ 0.25 * workflow_shift
+ 0.20 * technical_substance
+ 0.15 * adoption_path
+ 0.15 * confidence
- derivative_news_penalty
```

Bounds:

```text
All positive axes are 0-100.
derivative_news_penalty is 0-25.
Final l2_score is clamped to 0-100.
```

Default visible score:

```text
l2_score: 0-100
primary_reason: one short reason label
topic_tags: short content tags
score_rationale_short: one sentence
```

Primary reason examples:

```text
Momentum-led
Workflow Shift
Technical Substance
Adoption Path
Cross-source Resonance
Confidence Risk
```

The default Feed UI shows one aggregate score and one primary reason. Axis
details are available in expansion/detail views.

## Deepdive Selection

Daily bounded deepdive budget:

```text
max_deepdives_per_run = 10
```

Selection rules:

```text
1. Score all eligible Potential/High groups.
2. Scout Edge Watch groups within budget and score the included groups.
3. Sort by l2_score, tie-breaking by deterministic level, confidence, and freshness.
4. Select up to max_deepdives_per_run.
5. High candidates are not automatically forced above clearly stronger Potential
   candidates, but deterministic level remains a tie-breaker.
6. Edge Watch can enter the top set only after Kimi scout and scoring.
```

Deepdive selection does not change deterministic level. It affects Feed display,
analysis, caveats, and whether a deepdive report exists.

## Bounded Kimi Deepdive Harness

Deepdive should understand the project, not produce generic commentary.

Use a small self-written loop instead of a framework dependency.

Loop:

```text
Round 1: plan investigation from candidate context.
Round 2: gather bounded context with tool calls.
Round 3: synthesize final structured report.
```

Allowed tools:

```text
read_candidate_context
read_evidence_rows
read_source_items
fetch_cached_readme
fetch_homepage_or_docs
fetch_github_tree
fetch_github_file
fetch_package_manifest
fetch_hn_thread
fetch_x_tweet_context
kimi_web_search
```

Hard limits:

```text
max_rounds_per_candidate = 3
max_web_search_calls_per_candidate = 3
max_pages_per_candidate = 6
max_repo_files_per_candidate = 8
max_runtime_seconds_per_candidate = configurable
cache all fetched context and model outputs
```

Deepdive output schema:

```json
{
  "summary": "What this project is and why it matters.",
  "why_now": "Why it is relevant today.",
  "what_changed": "The product/workflow/technical shift.",
  "evidence": ["Grounded evidence line 1", "Grounded evidence line 2"],
  "adoption_path": "How it can spread.",
  "risks": ["Risk or caveat"],
  "open_questions": ["Question to inspect after this run"],
  "recommended_action": "read | watch | skip | investigate"
}
```

Every claim should be grounded in candidate context, source evidence, fetched
repo/web context, or Kimi web search result references.

## Feed Data Model

Layer 2 writes additive tables. It does not alter Layer 1 candidate tables.

Recommended tables:

```text
l2_feed_runs
  feed_run_id, decision_run_id, started_at, completed_at, status,
  config_hash, model_profile_json, note

l2_candidate_groups
  group_id, feed_run_id, canonical_entity_id, canonical_name, canonical_key,
  canonical_link, member_entity_ids_json, level, source_families_json,
  evidence_hash, grouping_reason_json, context_json

l2_scout_results
  feed_run_id, group_id, included_in_scoring, scout_score, reason,
  needed_context_json, risk, confidence, provider, model, prompt_version,
  cache_key

l2_scores
  feed_run_id, group_id, l2_score, axes_json, primary_reason, topic_tags_json,
  rationale_short, caveats_json, provider, model, prompt_version, cache_key

deepdive_reports
  feed_run_id, group_id, status, summary_json, tool_trace_json, provider,
  model, prompt_version, cache_key, created_at

l2_feed_items
  feed_run_id, group_id, section, rank, deepdive_status

feed_feedback
  feed_run_id, group_id, vote, created_at
```

## Feed API Contract

Add:

```text
GET /api/feed
POST /api/feed/feedback
```

`GET /api/dashboard-data` may include a compact `feed` object, but `/api/feed`
is the main contract for the Daily Feed.

`GET /api/feed` response:

```json
{
  "feed_run_id": "l2_20260531",
  "decision_run_id": "decision_daily_20260531",
  "generated_at": "2026-05-31T12:00:00Z",
  "model_profile": {
    "scout": "kimi-k2.5",
    "scoring": "kimi-k2.5",
    "deepdive": "kimi-k2.6"
  },
  "today_focus": [],
  "scored_list": [],
  "pending": {
    "edge_watch_scout": 0,
    "deepdive": 0
  }
}
```

Each Feed item includes:

```text
group_id
entity_ids
candidate name / canonical key / canonical link
deterministic level
l2_score
primary_reason
topic_tags
score axes in detail payload
score rationale
evidence bullets
source links
context preview / README excerpt
deepdive status
deepdive report when available
feedback state when available
```

`POST /api/feed/feedback` accepts:

```json
{
  "feed_run_id": "l2_20260531",
  "group_id": "group:abc",
  "vote": "up"
}
```

Allowed votes: `up`, `down`, `clear`.

## Daily Feed UI Contract

The existing React workspace shell remains. `Feed` has:

```text
Daily Feed
Candidate Pool
```

Candidate Pool remains an evidence-first table/list. Daily Feed becomes the
Layer 2 reading surface.

### Visual Direction

Design read from tasteskill:

```text
Internal AI opportunity radar workbench for a technical founder/operator, with a
signal-rich intelligence-board language. Preserve the current workspace shell,
but make Daily Feed feel like high-value signal cards rather than a generic table
or landing page.
```

Design dials:

```text
DESIGN_VARIANCE: 6
MOTION_INTENSITY: 4
VISUAL_DENSITY: 6
```

Visual constraints:

```text
No generic AI purple/blue gradient.
No marketing hero.
No nested cards.
No excessive full-page animation.
Motion should communicate signal/status: subtle shimmer, score rail glow,
loading skeletons, hover tactility, running deepdive state.
Cards can have material texture, but data must remain readable first.
The page must work in light and dark theme if the existing shell supports both.
```

Recommended Daily Feed layout:

```text
1. Run strip
   feed run id, decision run id, generated time, model profile, pending counts

2. Today Focus
   3-10 signal cards for selected deepdives

3. Scored Feed
   dense designed list of all scored candidates, default cap 100

4. Detail drawer or expanded panel
   axis details, full evidence, source links, README/context, deepdive trace summary
```

Today Focus card required fields:

```text
candidate name
canonical link / jump link
deterministic level
l2_score
primary_reason
topic tags
why look today
bounded deepdive summary
README excerpt or short description, collapsed
evidence bullets max 3 + "+N"
source links
tiny thumbs feedback at bottom
```

Scored Feed list item required fields:

```text
rank
candidate name + canonical link
short description / README preview
l2_score
primary_reason
topic tags
short evidence string
deepdive status
source links
```

## Settings Contract

Settings must expose Layer 2 model and budget configuration.

Minimum config:

```json
{
  "layer2": {
    "enabled": true,
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
}
```

## Evals

Minimum evals before accepting Layer 2:

```text
Grouping eval
  Same repo/npm/domain/link groups into one presentation group without writing
  permanent alias_links.

Scheduler eval
  Potential/High enter scoring by default.
  Edge Watch does not get deterministic semantic rejection.
  Same evidence hash is not rescored unless evidence changes.

Edge Watch scout eval
  Kimi scout admits semantically valuable edge cases and rejects pure news/noise,
  using fake-provider fixtures plus a small real Kimi smoke run.

Scoring eval
  Hermes/OpenClaw-like candidates rank above generic AI news, listicles,
  wrappers, and pure papers.

Deepdive budget eval
  Given more than 10 eligible candidates, output never exceeds configured
  max_deepdives_per_run.

Feed shape eval
  Top cards include score, reason, deepdive, link, README/description, source
  links, and evidence.
  Scored list rows include one score, reason tag, link, description, and evidence.

Model output validation
  Kimi outputs satisfy JSON schema and cite candidate context/evidence.
```

Use fake-provider fixtures for deterministic unit tests. Real Kimi smoke runs
must be bounded and skipped unless configured.

## Acceptance Criteria

- Candidate Pool rows keep concise why-in-pool bullets.
- LLM classifier-derived bullets remain visibly marked as LLM evidence.
- Candidate context includes descriptions and, when available, cached README
  excerpts.
- Presentation grouping dedupes same-project candidates before Layer 2 scoring.
- Potential/High candidates receive one `l2_score` in the 0-100 range by default.
- Edge Watch candidates can enter scoring only through Kimi Edge Watch Scout.
- Feed renders at most 10 top deepdive cards by default.
- Top cards contain both lightweight scoring rationale and bounded deepdive
  summaries.
- Feed also renders a compact scored list, default cap 100.
- README and descriptions are expandable or previewed, never dumped into the
  default list layout.
- Tiny thumbs feedback exists only as simple Feed quality feedback.
- Layer 2 scoring/deepdive never mutates deterministic Candidate Pool level.
