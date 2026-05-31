# Layer 1.5 Evidence And Layer 2 Feed Design

Date: 2026-05-31

Status: design approved in conversation, implementation not started.

## Goal

Turn a reliable Candidate Pool into a trustworthy daily reading surface.

The Candidate Pool should explain why each entity exists in the pool. The Feed
should consume that evidence, score every Potential/High candidate, deepdive at
most 10 projects, and display a small daily focus queue plus a compact scored
list.

This design covers Layer 1.5 and Layer 2 only.

Terminology note: this document uses `High` as shorthand for the existing
deterministic level `high_potential`.

## Non-Goals

- No Layer 3 agentic chat.
- No rule/prompt editing workflow.
- No full review/action workflow.
- No implementation plan in this document.
- No change to deterministic Potential level from L2 scoring or deepdive.

The only Feed feedback action in scope is tiny thumbs up/down feedback on whether
the Feed selection was useful.

## Product Boundary

```text
items/source rows
  -> entity resolution
  -> classifiers / certifiers / resolver / backfill
  -> evidence_rows
  -> candidate_evidence_summary + candidate_context_bundle
  -> Candidate Pool
  -> L2 scoring for all Potential/High candidates
  -> top <= 10 bounded deepdives
  -> Daily Feed
```

Layer 1 answers: "Does this entity qualify for the candidate universe?"

Layer 1.5 answers: "Can we explain and package the candidate clearly?"

Layer 2 answers: "Which qualified projects should I look at today, and why?"

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
```

Examples:

```text
GH +1.3k stars / 24h
HN front page, 142 pts
X 3 credible authors [LLM classifier]
Verified GH + HN within 48h
```

Each bullet carries structured provenance:

```json
{
  "label": "X 3 credible authors",
  "family": "x_social",
  "origin_type": "source_classifier",
  "provenance_badge": "LLM classifier",
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

Layer 2 analysis is not a why-in-pool evidence bullet. It belongs to Feed scoring
and analysis.

## Layer 1.5: Candidate Context Bundle

Every Potential and High candidate gets a context bundle for L2 scoring, Feed
display, and Candidate Pool expansion.

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
source family count
verified cross-source status
resolver / binding confidence
freshness
```

README behavior:

```text
README enrichment is cheap context enrichment, not deepdive.
Fetch only for verified GitHub repos attached to Potential/High candidates.
Cache the result.
Store a bounded excerpt, not an unbounded document.
UI shows README collapsed by default.
```

Recommended bounds:

```text
Stored README excerpt: 4k-8k chars.
Default UI preview: 600-1000 chars.
Candidate Pool list: no full README column.
Candidate Pool row expansion / drawer: collapsed README section.
Feed card: collapsed README excerpt section when available.
Scored Feed list row: short description or README preview only.
```

If README is unavailable, use the best available source description or homepage
meta description.

Current implementation note:

```text
Existing source rows already contain descriptions and metadata.
GitHub backfill currently caches repo metadata and stargazer evidence, but not README.
npm backfill extracts repository links but not README.
HN/X classifiers provide product/project evidence and links, not README content.
```

## Candidate Pool UI Contract

Candidate Pool remains an evidence-first table/list.

Default columns:

```text
candidate name / canonical key
level
evidence bullets, max 3 + "+N"
source families
canonical link
optional L2 score/status if available
```

Candidate Pool should not default to long L2 analysis. Not every row will be
deepdived, and the Pool's main job is traceability.

Expanded row / drawer can show:

```text
full evidence rows
all evidence bullets
README excerpt, collapsed
description/source snippets
official links
L2 score details if available
deepdive report if available
```

## Layer 2: Lightweight Scoring

All Potential and High candidates get lightweight L2 scoring.

The score answers:

```text
How worth reading is this candidate today, given both movement and semantic opportunity?
```

Default visible score:

```text
l2_score: 0-100
primary_reason: one short reason label
topic_tags: short content tags
score_rationale_short: one sentence
```

Example:

```json
{
  "l2_score": 82,
  "primary_reason": "Workflow Shift",
  "topic_tags": ["agent workflow", "repo-native protocol"],
  "score_rationale_short": "README and source evidence suggest a concrete repo-native agent workflow, not just a wrapper."
}
```

Internal scoring axes:

```text
Momentum
  Is something objectively moving now?
  Mostly deterministic: velocity, acceleration, cross-source, source-family heat.

Workflow Shift
  Does this change what users can do, or how a task gets done?

Technical Substance
  Is there real implementation, architecture, or a technical unlock?

Adoption Path
  Can this spread through real workflows or channels?
  Examples: CLI, chat gateway, package, docs, integrations, migration path,
  community, easy onboarding.

Confidence
  Do we trust the entity binding, evidence, and classifier claims?
```

These axes are for scoring, evals, and detail explanations. The default Feed UI
shows one aggregate score and one primary reason, not five score bars.

Primary reason examples:

```text
Momentum-led
Workflow Shift
Technical Substance
Adoption Path
Cross-source Resonance
Confidence Risk
```

Topic tag examples:

```text
agent workflow
personal assistant
developer tool
repo-native protocol
package adoption
model ecosystem
workflow automation
```

## Layer 2: Deepdive Selection

Daily bounded deepdive budget:

```text
max_deepdives_per_run = 10
```

Selection rules:

```text
1. Run lightweight scoring for all Potential and High candidates.
2. Include High candidates first.
3. If High candidates exceed 10, take the top 10 by L2/deepdive score.
4. If fewer than 10 High candidates exist, fill remaining slots from Potential.
5. Potential candidates are ranked by L2 score.
6. Edge Watch is not included in the default V1 top 10 unless explicitly enabled later.
```

Deepdive selection does not change deterministic level. It only affects Feed
display, analysis, caveats, and whether a deepdive report exists.

## Layer 2: Top Deepdive Cards

The Feed top section is `Today Focus`.

It contains at most 10 deepdive cards. Each top card must include both:

```text
lightweight L2 analysis
bounded deepdive summary
```

Card required fields:

```text
candidate name
canonical link / jump link
level
l2_score
primary_reason
topic tags
lightweight analysis: why look today
bounded deepdive summary
README excerpt collapsed, or short description if README unavailable
evidence bullets after analysis/deepdive
tiny thumbs feedback at bottom
```

Card information order:

```text
1. Name / link / level / score / reason
2. Lightweight analysis: why look today
3. Bounded deepdive summary
4. README excerpt or description, collapsed
5. Evidence bullets, max 3 + "+N"
6. Tiny thumbs up/down feedback
```

Thumb feedback:

```text
Default: small line icons, no strong color.
Selected: dark gray fill.
Purpose: collect whether Feed selection was useful.
Not a full review/action workflow.
```

## Layer 2: Scored Potential List

Below the Top 10 cards, Feed shows a compact scored list of high-scoring
Potential/High candidates.

Default cap:

```text
top_scored_list_limit = 100
```

Columns:

```text
rank
candidate name + canonical link
short description / README preview
l2_score, 0-100
primary_reason
topic tags
short evidence string
deepdive status
```

The list shows one aggregate score, not the internal five axes.

Example row:

```text
#12  pkg/name  79  Adoption Path
npm rising · repo verified · 2 source families
status: analyzed, not deepdived
```

## Model Routing

Use a provider abstraction, not a model SDK or framework lock-in.

Recommended model roles:

```text
DeepSeek
  source classifiers
  lightweight scoring
  structured L2 analysis when context is already assembled
  small JSON eval/smoke runs

Kimi / Moonshot
  bounded deepdive when repo/web/README/code/doc context matters
  long-context project understanding
```

Architecture recommendation:

```text
thin provider abstraction + bounded self-written tool loop
```

Do not make LangGraph a V1 dependency unless later complexity proves it is
necessary.

Bounded deepdive tools:

```text
fetch README
fetch homepage/docs
inspect selected repo files/package manifest
read source evidence rows
read cited HN/X/PH/npm/HF details
```

Hard limits:

```text
max 10 candidates per run
max pages/files per candidate
max runtime/context
cache all fetched context and model outputs
```

## Evals

Minimum evals before accepting Layer 2:

```text
Evidence summary eval
  Evidence bullets are short, provenance-marked, and capped at 3 by default.

Context bundle eval
  Verified repo candidates include description and README excerpt when available.

Scoring eval
  Hermes/OpenClaw-like candidates rank above generic AI news, listicles,
  wrappers, and pure papers.

Deepdive budget eval
  Given more than 10 candidates, output never exceeds 10 deepdives and High
  candidates get first consideration.

Feed shape eval
  Top cards include analysis, deepdive, link, README/description, and evidence.
  Scored list rows include one score, reason tag, link, description, and evidence.

Model output validation
  DeepSeek/Kimi outputs satisfy JSON schema and cite candidate context/evidence.
```

Use fake-provider fixtures for deterministic unit tests. Real DeepSeek/Kimi smoke
runs must be bounded and skip unless configured.

## Acceptance Criteria

- Candidate Pool rows can show concise why-in-pool bullets.
- LLM classifier-derived bullets are visibly marked as LLM classifier evidence.
- Potential/High candidates have context bundles with descriptions and, when
  available, cached README excerpts.
- Every Potential/High candidate receives one `l2_score` in the 0-100 range.
- Feed renders at most 10 top deepdive cards.
- Top cards contain both lightweight analysis and bounded deepdive summaries.
- Feed also renders a compact scored list, default cap 100.
- README and descriptions are expandable or previewed, never dumped into the
  default list layout.
- Tiny thumbs feedback exists only as simple Feed quality feedback.
- L2 scoring/deepdive never mutates deterministic Potential level.
