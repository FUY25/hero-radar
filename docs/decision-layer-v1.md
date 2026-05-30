# Decision Layer v1

This is the design source of truth for the layer that sits on top of the existing
collection pipeline. The collection pipeline (`pipeline/run_pipeline.py`) already
gathers ~15 source channels into `data/hero_radar.sqlite` (`items` table). This
document specifies the layer that turns those raw rows into a daily decision
workflow.

Status: design locked, not yet built. Implement in the build order in section 12.

## 0. One Sentence and Non-Goals

```text
Turn the raw source rows we already collect into:
  1. a deterministic, explainable Potential pool of moving entities,
  2. an LLM-analyzed daily feed of what to actually look at today,
  3. an explore/QA chatbot that can deep-dive an entity and propose changes
     to the deterministic rules or the LLM prompt.
```

Non-goals and explicit reframes:

```text
- The system is built for the NEXT unknown Potential, not for Hermes/OpenClaw.
  Hermes/OpenClaw are calibration anchors and a regression check, not the goal.
- No hand-curated alias file as the primary mechanism. Future projects cannot
  be pre-named. Entity resolution must be automatic and emergent.
- The LLM does not decide who enters the Potential pool. Deterministic rules do.
  The LLM explains, ranks, and proposes; it never silently changes production logic.
```

Deployment target (changed from the personal-local assumption in product-spec-v0.7):
this is now a HOSTED, multi-account web app, invite-only. The radar data is GLOBAL
(one shared copy for all accounts); multi-tenancy is a thin per-account overlay.
See section 14 for the full hosting and multi-tenancy model.

## 1. Shape

```text
collection layer (exists)
  source adapters -> items (raw_json + metadata_json) in SQLite

decision layer (this doc)
  Layer 0  Entity resolution     items rows -> emergent entity clusters
  Layer 1  Deterministic pool     rules over clusters -> potential_candidates + evidence_rows
  Layer 2  LLM daily feed         DeepSeek analysis over pool -> daily_feed
  Layer 3  Explore / QA chatbot   read-only deep-dive + propose rule/prompt changes

provider interface
  one swappable LLM provider used by Layer 0 (Stage B), Layer 2, and Layer 3.
```

The whole decision layer is additive. It reads `items` and writes new tables. It
does not modify the collection pipeline or the existing `items / scores / analyses`
contracts.

## 2. Locked Design Decisions

```text
LLM provider:   abstract provider interface first. DeepSeek / Claude / others pluggable.
                No DEEPSEEK_API_KEY in .env yet; provider layer must run without it
                for Layer 0 Stage A and Layer 1 (which need no LLM).

Entity grain:   automatic, emergent clusters. Deterministic join keys first,
                LLM only for the moving orphan tail. No hand-written alias file.

Build order:    Layer 0 Stage A + Layer 1 first, validated on today's live data.
                Benchmark replay is a regression check, not the acceptance goal.

Chatbot edits:  propose-only. Rule / prompt changes become reviewable proposals,
                approved into versioned records, never auto-applied.
```

## 3. Entity Resolution (Layer 0)

This is the crux of the system. It must work on entities it has never seen, and
it must not LLM-cluster the full ~15k-row universe. Two stages.

### 3.1 Stage A: deterministic join keys (no LLM, runs on all rows)

For each `items` row, extract whatever canonical keys are available:

```text
github_repo_key   if the row is or embeds github.com/{owner}/{repo}:
                    - github_trending / github_search / github_movers: direct
                    - npm:   metadata.repository / links.repository
                    - pypi:  metadata.project_urls (Source / Homepage / Repository)
                    - product_hunt: website if it resolves to github
                    - huggingface: model/space card repo link if present
                    - hn:    story url if it is a github repo url
                    - x:     links in tweet text if github
                  normalize via GitHub redirect + repository id when resolvable
                  (clawdbot -> openclaw resolves here automatically).

domain_key        normalized registrable domain of homepage/website/url
                  (secondary key, lower precision than github_repo_key).

name_key          normalized exact name / slug (tertiary key).
```

Run union-find over these keys. Output: deterministic `entity` clusters keyed by
the strongest available key (github repo id > domain > name). This is automatic,
generalizes to any future project, and links the 70-80% of serious cases that
carry a github URL somewhere in their source metadata.

Precision rules:

```text
- github_repo_key (redirect/repo-id resolved): strong, always merge.
- domain_key: medium. Merge only if not contradicted by different github keys.
- name_key: weak. Merge only for exact normalized match; never fuzzy here.
```

Fuzzy / semantic merging is explicitly NOT done in Stage A. That is Stage B.

### 3.2 Stage B: LLM orphan merge (only on moving orphans)

The residual: rows that joined to no `github_repo_key` and no `domain_key`,
i.e. bare names with no link. Examples: an HN story `Show HN: Clawdbot` with no
url, a bare npm name, an X mention. Most of these are noise. We only spend LLM
on the ones that already matter.

```text
gate:   an orphan name is eligible for Stage B only if it (or its cluster)
        already fired at least one deterministic trigger (section 4). This bounds
        LLM cost and is semantically correct: only resolve aliases for things
        that are already moving.

input:  current cluster summaries (canonical name, top sources, key evidence)
        + the eligible orphan names/descriptions.

output: proposed merges, each with { orphan, target_cluster | new_entity,
        confidence, reason }. Stored in `entity_merge_proposals`.
        NEVER silently authoritative. A proposal becomes an applied alias_link
        only after approval (auto-approve threshold is configurable, default off).
```

This is where the user's intuition lands: alias merging across sources cannot be
manual, so the LLM does the ambiguous-tail judgment, but as a reviewable proposal.

### 3.3 Why this order resolves the chicken-and-egg

```text
Triggers need entities to aggregate evidence, but entity resolution on 15k rows
is expensive. So:
  Stage A (cheap union-find) runs on all rows.
  Triggers run on Stage A clusters -> a few dozen candidates.
  Stage B (LLM) runs only on moving orphans -> bounded cost.
```

## 4. Deterministic Potential Pool (Layer 1)

Rules are stored as DATA, not code, so the chatbot can propose edits and we can
version them.

### 4.1 Rules as data

```text
pipeline/rules.json   versioned. Each rule:
  id, family, source_or_alias_scope, field, op, threshold, label/weight, note,
  rule_version
```

Seed the rules directly from `docs/benchmark-hermes-openclaw.md`
("High-Recall Closer-Look Rules"). Trigger families:

```text
github                three independent GitHub sources (github_trending, trending_repos,
                      repofomo), each its own threshold, OR-combined (section 4.5)
package_family        npm daily downloads > 10k rising, or version/scoped-package burst in 24-48h
hn                    hn_algolia: >= 3 matching stories in 7d (per entity);
                      hn_firebase: HN front page + score >= ~100, or same-day as a github accel
product_hunt          launch with daily_rank <= 5 or clear launch metadata
hugging_face          >= 2 matching resources in 48h, or 1 canonical resource same-day as another signal
x_social              >= N distinct credible (seed-list) authors mention the entity in 7d,
                      or a 24h mention burst vs the prior baseline (see section 4.4)
cross_source          any two weak signals from different source families within 48h

discovery (not a level rule):
github_search         widest net; feeds the entity universe + stargazers-backfill
                      shortlist for off-board movers (section 4.8)
excluded from v1:     pypi (benchmark: late/weak; kept for L3 explore only)
```

Triggers are evaluated over the whole entity cluster (all alias rows), not per raw row.

### 4.2 Deterministic level (no LLM) — single-dimension principle

Hard requirement (user): the Potential level must NOT depend on cross-source
aggregation. Entity resolution (Layer 0) is imperfect; the same project will
sometimes split into several un-merged entities. If the level required multiple
families to fire on one merged entity, every split fragment would fail. So:

```text
Each source has its OWN independent criterion on its OWN scale (sources differ;
two GitHub boards are black boxes with different definitions). Each source casts a
LEVEL vote (none/watch/potential/high) using its own thresholds. The entity level
is the MAX over those LEVEL votes -- an ordinal max over comparable levels, NOT a
max over incomparable raw numbers. Any single source reaching a band promotes the
entity (OR semantics). Corroboration only adds confidence and priority; it is
NEVER required to reach the base level.
```

Why ordinal-max, not raw-number-max: MAX(a,b,c) >= T with one SHARED cutoff T is
mathematically identical to "a>=T OR b>=T OR c>=T". That is fine, but using ONE
shared cutoff across heterogeneous black-box metrics is wrong. So each source gets
its OWN cutoff, the comparison becomes a per-source pass/fail -> level vote, and we
max over the (comparable) level votes.

```text
none            no source votes above its watch threshold
watch           some source votes watch
potential       some source votes potential   (one source is enough)
high_potential  some source votes high         (one source is enough)
```

The exact momentum definition, normalization, and bands are in section 4.5 and
live in `rules.json` so they are tunable and versioned. Corroboration (section
4.6) is a separate axis and cannot gate the base level.

### 4.3 Evidence rows

Every fired trigger emits an explainable evidence row. Shape (from the benchmark
doc's recommended extractor):

```text
entity_id
canonical_entity
alias
source
event_at
relative_to_reference (optional)
metric_name
metric_value
family
rule_id
rule_version
signal_label        early_trigger / confirm_signal / ecosystem_echo / late
historical_safety   as_of_safe / partial_as_of / snapshot_only
note
raw_url_or_ref      links back to the items row / source url
```

This is what the UI shows under "Deterministic Evidence" and what the chatbot
cites. The pool decision is always reconstructable from these rows.

### 4.4 X / social signal flow

An independent extraction flow that maps recent tweets to entities and feeds the
`x_social` trigger family. Source: the existing `x_tweets_store` table (already
populated, ~1.4k rows; created_at indexed). This flow does not touch the other
source adapters; it only adds one more signal into the same Potential evaluation.

```text
scope        rolling 7d of x_tweets_store (also compute a 24h sub-window).

map tweet -> entity (reuses Layer 0):
  1. github / project links in tweet text -> Stage A deterministic join key
     -> attach mention to the resolved entity.
  2. bare @handle / project name / hashtag with no link -> orphan candidate;
     only goes to Stage B (LLM) if the entity is already moving (same gate as 3.2).

aggregate per entity (7d and 24h):
  distinct_authors          number of distinct authors mentioning the entity
  credible_authors          subset whose handle is in the X seed-account list (config)
  mention_count
  mention_acceleration      24h count vs prior-baseline rate

x_social rule (in rules.json, tunable):
  credible_authors_7d >= N   (weak-to-medium signal)
  OR mention_acceleration above threshold (burst)
```

Design constraints to avoid noise:

```text
- Weight distinct CREDIBLE authors, not raw engagement. Likes/retweets are
  snapshot_only and unreliable as historical signal.
- event_at = tweet created_at (as_of_safe). Engagement counts are partial_as_of.
- x_social rarely promotes to high_potential alone. Its main value is being the
  second weak signal in cross_source resonance, since social ignition often
  precedes a GitHub takeoff.
- Mention -> entity mapping confidence is carried on the evidence row, same as
  any other alias link (strong = link in tweet, weak = bare name match).
```

Evidence rows from this flow use the section 4.3 shape with `source = x_tweets`,
`family = x_social`, and `metric_name` in {distinct_authors, credible_authors,
mention_count, mention_acceleration}.

### 4.5 Metric design: momentum backbone (PROPOSAL, under discussion)

Decisions taken in discussion:

```text
D1  Momentum is expressed as TWO independent criteria, velocity AND acceleration,
    not one blended number. A source "shows momentum" if EITHER criterion passes.
    (user: "velocity 和 acceleration 可以有 2 个 criteria")

D2  Membership is gated by ABSOLUTE thresholds calibrated from the benchmark, not
    by pool percentile. On a quiet day where nothing crosses the floor, the pool
    is legitimately small or empty. We never force a daily winner.
    (user: "有几天 nothing important happen，不能要求每天都有重要项目")

D3  Percentile is demoted: at most an OPTIONAL ranking aid AMONG entities that
    already passed an absolute floor. It never decides membership.

D4  MAX-of-signals stays: an entity qualifies at a level if ANY single signal
    passes that level's absolute criterion. Survives L0 splits.
```

Numbers must trace to `docs/benchmark-hermes-openclaw.md` (section 5 anchors and
the observation table). The acceleration criterion is deliberately self-relative
(speed-up vs the entity's own trailing baseline), so it needs no pool percentile
and stays quiet-day safe, while still catching the early second-derivative lift
that the benchmark says fires before the (too-late) velocity peak.

Starting proposal, to calibrate against the observation table:

GitHub: THREE independent sources, each its own scale, OR-combined (do NOT compare
their raw numbers). Calibrated and counted on the 2026-05-30 latest snapshot.

```text
github_trending   GitHub-native stars_today (transparent)
                  watch >= 300 | potential >= 1000 | high >= 3000   (stars/24h)
                  2026-05-30 hits: 19 / 3 / 0
trending_repos    black-box stars_velocity (daily) + sparkline (7-pt) for accel
                  watch >= 300 | potential >= 800 | high >= 2500     (its daily velocity)
                  2026-05-30 hits: 55 / 20 / 2 ; accel = sparkline slope rising
repofomo          black-box stars_7d -- a 7-day CUMULATIVE window, so it is laggy
                  velocity: watch >= 200 (stars_7d); standalone TOPS OUT AT WATCH.
                  potential ONLY if also accelerating now (r7>r30>r60).
                  high >= 5000 (stars_7d). 2026-05-30 hits: 302 / (s7>=1000) 68 / 8
                  -> RepoFOMO's threshold is the pool-size LEVER; held to
                     watch-unless-accel keeps the pool sane.
github_search     DISCOVERY only, NOT a level rule. Feeds the universe + the
                  stargazers-backfill shortlist for off-board movers (section 4.8).
precise github accel  for the backfill shortlist: 24h rate >= K x prior-6d daily
                  rate (K ~ 2), from stargazers starred_at. Anchored to Hermes
                  0.6439 stars/hour^2 at onset.
```

```text
OR-deduped GitHub pool on 2026-05-30 (per-source thresholds above):
  watch 327 / potential 82 / high 10.
  With RepoFOMO held watch-unless-accel, potential drops to ~20-40 (target size).
  Benchmark check: NousResearch/hermes-agent = 1290 stars/day -> potential. OK.
```

Non-GitHub sources (each its own criterion; per-entity ones need Layer 0 first):

```text
npm           daily_downloads > 10k rising, OR >= 3 versions in 48h,
              OR >= 2 scoped packages of one family in 24h. (strong parts need npm
              API backfill; local npm_search only has weekly_downloads.)
hn_algolia    >= 3 matching stories in 7d (per entity, needs L0)
hn_firebase   on the HN front page (top/best) with score >= ~100, OR any front-page
              story same-day as a GitHub accel. (NEW; see section 4.8 gap 2.)
product_hunt  daily_rank <= 5
hugging_face  >= 2 matching resources in 48h (per entity, needs L0)
x_social      credible_authors_7d >= N (open Q6), OR mention_acceleration burst
pypi          EXCLUDED from v1 triggers (benchmark: late/weak); kept for L3 explore.
```

```text
level bands: each source carries its own watch / potential / high_potential
threshold in rules.json. All absolute, benchmark-traceable, versioned. Entity
level = the highest LEVEL VOTE across its sources (section 4.2 ordinal max).
```

Still open (Q-c): whether ecosystem-echo sources (e.g. HF derivative resources)
get a per-source `max_level` cap so they cannot reach high_potential alone. User
undecided; deferred.

### 4.6 Corroboration axis (confidence, never gates the level)

```text
corroboration = count of DISTINCT source families that independently show momentum
                + cross-source-within-48h flag
                + L0/L2 merge confidence

effects (allowed):
  - raise the candidate's confidence label
  - raise feed priority (Layer 2)
  - upgrade a borderline potential -> high_potential

effects (forbidden):
  - it can NEVER be required to reach base potential. Single-dimension momentum
    alone must always be able to promote an entity.
```

### 4.7 Temporal model and data reach (DECIDED)

How the momentum criteria (4.5) are computed in time. Hard constraint (user):
do NOT assume we have prior local snapshots. The local DB is only ~2 days old and
its own snapshot deltas are too short and noisy to use.

Sources split into three temporal categories. The evaluation reach is per-source;
there is no single global window.

```text
A  event-stamped       event carries its own timestamp; windowable on Day 1 with
                       NO local history: HN created_at, X created_at, HF createdAt,
                       PH featuredAt, npm publish/daily-downloads, PyPI upload,
                       GitHub stargazers starred_at (backfill).
B  source-native rate  source already provides rate/window/series in ONE snapshot:
                         - Trending Repos sparkline: 7 daily star-delta points
                           (a 7-day velocity series embedded in a single snapshot)
                         - Trending Repos starsVelocity / forksVelocity
                         - RepoFOMO stars_7d / stars_30d / stars_60d / new_forks
                         - GitHub Trending stars_today / week / month
C  point-in-time count  only a current total, needs OUR delta: GitHub Search
                       stars_total, HF likes/downloads.
                       DROPPED for v1 -- needs local history we do not have.
```

Velocity and acceleration sourcing, cheapest-first (no backfill, no prior snapshot):

```text
velocity
  1. sparkline latest point or recent mean      (B, single snapshot)   free
  2. RepoFOMO stars_7d / 7                       (B)                    free
  3. GitHub Trending stars_today                 (B)                    free
  4. stargazers starred_at in [now-24h]          (A, backfill)          precise, paid

acceleration  (self-relative, single snapshot preferred; no prior local data)
  1. multi-window ratio r7=stars_7d/7, r30=stars_30d/30, r60=stars_60d/60;
     accelerating if r7 > r30 > r60               (RepoFOMO, 1 snapshot) free
     same idea on GitHub Trending: today vs week/7 vs month/30.
  2. sparkline slope / last-point vs prior-baseline (B)                  free
  3. stargazers backfill: 24h rate vs prior-6d daily rate, K >= 2        precise, paid
```

```text
backfill policy (P2/P3 decided)
  GitHub stargazers starred_at (and npm daily-downloads) is the precise but
  expensive path. NOT run on all repos. Reserved for:
    - the shortlist of repos that already look like movers from free B-class /
      event-count signals, and
    - off-board movers that show signal in HN / npm / X but are on no GitHub board.
  Free signals decide the shortlist; backfill confirms and adds precision.
  Needs GITHUB_TOKEN. Cache pulled star curves to avoid re-pulling within a day.
```

```text
evaluation windows (P1/P4 decided)
  - two windows: 24h (velocity) and 7d (trend). 30d display-only.
  - acceleration is self-relative (above); it does NOT require local history.
  - windows are RATE look-back windows, not creation-date filters: an old repo
    moving now still qualifies.
  - live daily run is now-anchored ([now-24h], [now-7d]); as-of rigor is only for
    the benchmark replay.
  - cadence: one full run per day.
```

Caveat (benchmark): B-class board fields and sparkline are snapshot_only. They are
used going forward, absence must never count against a candidate, and they are not
claimed as historical replay.

### 4.8 Source coverage and gaps (DECIDED)

Audit of every source we already collect vs whether a rule covers it.

```text
source              data   rule          role
github_trending     yes    yes           github main axis
trending_repos      yes    yes           github main axis (black box)
repofomo            yes    yes           github main axis (black box, pool lever)
hn_algolia          yes    yes (L0)      hn family (story-count per entity)
huggingface x3      yes    yes           hf family
npm_search          yes    partial       strong parts need npm API backfill
product_hunt        yes    yes           weak corroboration
x_tweets            yes    yes (L0)      x_social
github_search       yes    NO  -> gap 1
hn_firebase         yes    NO  -> gap 2
pypi newest/updates yes    NO  -> gap 3
x_seed_accounts     yes    n/a           it is the monitoring list (config), not a signal
```

Decisions:

```text
gap 1  github_search -- DO (early-discovery backbone)
  Only has stars_total (point-in-time), so NO level rule. Its value: the widest
  net, the only way to catch a repo NOT yet on any black-box board. Role:
    a) the entity universe; b) the off-board stargazers-backfill shortlist
       (section 4.7): "created within N days AND stars >= X AND not on any board"
       -> enqueue for stargazers backfill.
  This is how an OpenClaw-style pre-board mover is caught early.

gap 2  hn_firebase -- DO (strong and cheap)
  HN front page (top/new/best) with score, distinct from hn_algolia search.
  Front-page presence + score >= ~100 is a strong "happening now" signal, and the
  story URL resolves to a GitHub repo via L0. Added as a rule in the hn family
  (section 4.5).

gap 3  pypi -- SKIP for v1
  Benchmark: PyPI is late/not-useful for both calibration cases. No standalone
  trigger in v1. Keep raw data for L3 explore and as a possible weak ecosystem-echo
  later.
```

Reframe: the black-box boards only cover repos that ALREADY ranked = confirmation.
Early recall comes from github_search discovery + event sources (hn_firebase / npm
/ HF / X) + stargazers backfill. So github_search is the early-discovery entry, not
an uncovered edge case.

## 5. LLM Harness and Providers

Two tiers, because the user wants different swap-ability for different jobs.

```text
Tier 1  Pipeline provider (single-shot, cost-sensitive, high-volume)
  pipeline/llm_provider.py
    Provider protocol: complete(messages, *, tools=None, json_schema=None) -> response
    Default: DeepSeekProvider. Used for Layer 2 per-candidate feed analysis.
    DeepSeek is acceptable here ("数据 pipeline 用 deepseek 可以").
    Swappable by config, but a plain API call is enough.

Tier 2  Agentic harness (tool-using, model-swappable). ONE shared architecture,
        TWO separate configured instances (user: "相似架构但是 2 套").
  pipeline/agent_harness.py + a shared tool registry.

  2a  Pipeline judgment agent (constrained, mostly deterministic context)
      Used by Layer 0 Stage B merges and Layer 2 de-dup. These stages are
      deterministic-first and pipeline-like; the agent only runs the LAST step:
      go look at the actual repo/source, decide the merge, and fill structured
      fields. Bounded tool set, structured output, low autonomy.

  2b  Explore agent (flexible)
      Backs Layer 3. Broad tool set over ALL collected data, multi-step,
      free-form, higher autonomy. Externally callable over an authenticated
      HTTP API (section 7; the local CLI is a thin wrapper around it).

  Both share the harness and tool registry; they differ in tool scope, autonomy,
  and output contract. Model is swappable (DeepSeek / Claude / others) on both.

  Framework choice (recommendation): a thin self-written tool-loop over a provider
  abstraction (LiteLLM-style) for model-swap; NOT LangGraph, NOT the DeepSeek SDK
  as a framework. The one genuinely code-heavy capability (2b "explain / search OUR
  code") is delegated to a coding agent used as a single tool. In hosted mode this
  tool runs IN-PROCESS via the Claude Agent SDK (or an API-based file-reading tool
  over a server-side repo checkout); it does NOT shell out to a local coding CLI
  per request (that does not isolate or scale on a shared server). 2a needs only
  github API + web-fetch + DB tools, no coding agent.

Hard rule: Layer 0 Stage A and Layer 1 import nothing from either tier. They are
LLM-free and fully deterministic.
```

## 6. Daily Feed (Layer 2)

### 6.0 Candidate aggregation / de-dup pass (runs first)

Because Layer 0 is imperfect, the same project can enter the pool as several
un-merged candidates with the same/similar name (user comment C1). The level is
already robust to this (section 4.2 uses MAX, so each fragment still qualifies),
but the FEED must not show duplicate cards. So before analysis:

```text
input:   the day's candidates (each with canonical name, top sources, key evidence)
job:     group candidates that are clearly the same project
  - deterministic first (pipeline): exact normalized-name collision, shared link/domain.
  - only the ambiguous rest goes to the Tier 2a constrained judgment agent, whose
    LAST step is to actually look at the repo/source, decide the merge, and fill
    structured fields. Reuses entity_merge_proposals (propose-only; default not
    auto-applied).
effect:  feed shows ONE merged card per project; its evidence is the union of the
         fragments' evidence; momentum is the MAX across fragments.
```

This is L2 aggregation as a safety net over L0, not a replacement for it. It is
the same architecture as Stage B but a separate configured instance (section 5,
"相似架构但是 2 套").

Note: the exact end-to-end steps from Potential pool -> today's feed (ordering,
bucket cutoffs, when 2a runs, caching) are still being discussed and are not
locked here.

### 6.1 Per-candidate analysis

```text
input per candidate:  compact JSON context
  { entity, canonical_entity, level, fired_families, evidence_rows (trimmed),
    source_facts }

call:                 one Tier 1 provider call per merged candidate (cap N, see open questions)

output analysis_json:
  priority            0-100
  thesis              one short paragraph
  why_now
  caveats             e.g. PH missing is not negative; alias confidence weak
  paradigm_judgment   new paradigm / old paradigm new execution / clone / unclear
  next_questions      suggested chatbot follow-ups

cost control:         only analyze level >= potential; dedupe by (entity, day); cache.
```

Reuse the existing `analyses` table (store analysis_json in `analysis_text`, set
`judgment`) plus a new `daily_feed` table for ranking. Feed buckets:

```text
today_focus     top by priority
secondary
backlog
```

## 7. Explore / QA Agent (Layer 3)

Layer 3 is NOT just a chatbot over the Potential pool (user comment C2). It is a
model-swappable agentic harness (section 5, Tier 2) whose scope is ALL collected
data and the system itself, with several uses:

```text
scope     not limited to the Potential list. The agent can explore the full
          history and all of today's collected items, mine ad-hoc signals the
          deterministic rules did not surface, and pull live source APIs.

uses      1. explore: free-form Q&A over entities, evidence, raw rows, history.
          2. explain: walk through the code / rules / pipeline ("how does the
             x_social rule work", "why is this column computed this way").
          3. callable agent: be invoked BY external agents (e.g. Claude Code) to
             run tasks using our data + already-integrated source APIs, and
             return structured results. Primary surface is a simple CLI, not MCP.
```

### 7.1 Surfaces

The external-call surface should be lightweight (user: a plain "send a prompt"
is enough; MCP may be too heavy).

```text
/api/chat   web Explore tab (UI).
CLI         `python3 pipeline/agent.py "<prompt>"` -> structured result on stdout.
            PRIMARY external surface. Claude Code etc. just shell out to it.
            No protocol overhead.
MCP         OPTIONAL later thin wrapper around the same CLI/harness, only if a
            richer protocol is ever needed. Not required for v1.
all share   one agent_harness.py + the same tool registry.
```

### 7.2 Tools (read-only by default)

```text
explore / deep-dive (read-only)
  why_in_pool(entity)        which signals/levels fired, with evidence rows
  why_not_in_pool(entity)    which momentum bands were missed and by how much
  query_entity / get_evidence / get_source_rows
  search_all_items(query)    full collected corpus, NOT only the pool
  velocity_over_time(entity)
  call_source_api(...)       live GitHub / HN / npm / HF / PH backfill on demand
  explain_code / explain_rule
  replay_benchmark           run the Hermes/OpenClaw regression check on demand

logic editing (propose-only)
  propose_rule_change        emits a rules.json diff + rationale -> rule_proposals
  propose_prompt_change      emits a prompt diff -> prompt_proposals
  Never writes production rules/prompts directly.
```

### 7.3 Guardrail (user-specified)

```text
user feedback
  -> agent generates a proposed change (diff + rationale)
  -> optional benchmark replay to show impact
  -> user approves
  -> saved as a new rule_version / prompt_version
```

The agent may cite: raw source rows, evidence rows, deterministic trigger
results, LLM analysis, benchmark cases, rule versions, prompt versions.

## 8. Three Separate Scores / States

Keep these three independent. Mixing them makes the system undebuggable.

```text
1. Deterministic Potential Level   none / watch / potential / high_potential
                                    set by single-dimension momentum only (section 4.2).
2. LLM Priority Score              0-100, from the Layer 2 provider. Orders the feed.
                                    Does NOT decide pool membership.
3. Human Status                    unreviewed / opened / watching / dismissed /
                                    promoted / needs_followup / rule_feedback.
                                    Set by user actions in the UI.
```

## 9. Storage

Hosted target: Postgres (multi-account concurrency + pipeline-write/user-read).
SQLite stays usable for local dev. Tables split into GLOBAL (one shared copy,
written by the daily pipeline) and PER-ACCOUNT (keyed by user_id). See section 14.

```text
GLOBAL (shared radar; cost does not scale with users)
  entities                cluster id, canonical_entity, canonical_key, key_type, first_seen
  alias_links             entity_id, source, external_id/handle, confidence, origin, approved
  entity_merge_proposals  Stage B LLM proposals (orphan, target, confidence, reason, status)
  potential_candidates    entity_id, run_id, level, fired_families_json, first_trigger_at
  entity_mentions         entity_id, window (7d/24h), distinct_authors, credible_authors,
                          mention_count, mention_acceleration, run_id (section 4.4)
  evidence_rows           per section 4.3
  daily_feed              run_id, entity_id, bucket, priority, analysis_id
  rule_versions           version, rules_json, created_at, author, note   (v1: global, any admin)
  prompt_versions         version, kind, prompt_text, created_at, author, note
  rule_proposals          diff, rationale, status, created_from_feedback
  prompt_proposals        diff, rationale, status

PER-ACCOUNT (multi-tenant, keyed by user_id)
  accounts                id, email, invite/auth, role, created_at
  sessions                auth/session tokens
  chat_sessions           user_id, messages, created_at (L3 history)
  human_status            user_id, entity_id, status (watch/dismiss/promote/...)  (was global in §8)
  feedback_events         user_id, payload, created_at
  saved_views             user_id, config
  usage_quota             user_id, llm_calls, tokens, window (per-account metering for L3)
```

Reuse `analyses` (global) for the LLM analysis text/judgment.

Note: Human Status (section 8 item 3) is now PER-ACCOUNT. The radar level/priority
(items 1-2) stay GLOBAL.

## 10. UI

Hosted web app behind invite-only auth. All API endpoints are authenticated and
carry the user_id; the radar data they read is GLOBAL, the state they write
(status, chat, feedback) is PER-ACCOUNT.

Core stance: make the deterministic reasoning legible and the daily review fast.

### 10.1 Principles

```text
P-a  Ranked feed. Default = the full pool ranked by LLM Priority. Per-account
     human_status (unreviewed / watching / dismissed) and "new / moved-up since
     last visit" are FILTERS and markers on top of the ranked list, not the frame.
P-b  Three numbers stay visually SEPARATE on every card (section 8): Level badge
     (rule), Priority chip (LLM), Status (you). Never one blended score.
P-c  Card = scannable; Drawer = full evidence. One click from any item to the
     deterministic evidence that reconstructs "why in pool".
P-d  L0 imperfection is visible and correctable in the UI; corrections feed the
     proposal / feedback loop.
```

### 10.2 Navigation: four peer top-level entries

```text
1. Feed                  GET /api/feed   (global ranked feed + per-user status overlay)
   flat list ranked by LLM Priority; buckets today_focus / secondary / backlog.
   filters/markers: New, Moved-up, Unreviewed, Watching (per-account human_status).
   card: name, canonical entity, Level badge, LLM Priority chip, one-line thesis,
         evidence chips ("GH +1290/d", "HN front page 142", "npm 70k/d"),
         source-family icons, Human Status.
   actions: open source, deep-dive in chat, mark noise, promote, watch
         -> write per-account human_status / feedback_events.
   -> Project Drawer  GET /api/entity/{id}: per-source LEVEL-VOTE table with raw
      numbers + source links (the evidence), full DeepSeek analysis, velocity-over-
      time, alias pack + merge confidence (L0 visible), evidence_rows, "deep-dive
      with chatbot" (carries entity context).

2. Explore               POST /api/chat  (per-account session + quota)
   free-form agent over ALL collected data (not only the pool); read-only tools by
   default, propose-only for rule/prompt edits. Same agent exposed over an
   authenticated HTTP API for external callers; CLI is a thin wrapper (section 7).

3. Sources               the existing source-native dashboard (each source's own
   facts/ranks/links). A first-class peer entry for raw inspection / transparency.

4. Settings              global config + governance + account:
   - monitored universe (config.json: sources, queries, seed accounts) -- v1 all-admin
   - rules / prompts: current version, chat-proposed diffs, run benchmark replay,
     approve -> new version (section 7.3 / 14 H3)
   - LLM provider / model choice (section 5)
   - account, quota / usage view
```

### 10.3 Decided UI choices

```text
F1  Frontend = small SPA (React + Vite).
F2  Feed = flat ranked-by-Priority list; human_status (new / moved-up / unreviewed
    / watching) are filters and markers, NOT the primary frame.
F3  Nav = Feed | Explore | Sources | Settings, four peer entries. Sources (the raw
    dashboard) is a first-class peer, not buried; Settings is the fourth.
```

## 11. Benchmark as Regression, Not Goal

```text
docs/benchmark-hermes-openclaw.md and the observation table remain the
calibration anchors. After building Layer 1, and after any threshold/rule change,
run a replay check:
  - Hermes cluster reaches potential on github_acceleration near its T0 window.
  - OpenClaw cluster (clawdbot + openclaw merged) reaches potential/high_potential
    via alias-aware multi-source triggers.
A failed replay is a signal that a tuning change regressed a known-good case.
It is NOT the definition of success. Success is sane Potential clusters on
today's unseen live data.
```

## 12. Build Order

```text
Step 1  Layer 0 Stage A (union-find entity resolution) + Layer 1 (rules.json,
        single-dimension momentum level per section 4.2/4.5, evidence_rows).
        Pure code, no LLM. Run on the current ~15k live items.
        Includes the deterministic part of the X social flow (section 4.4):
        link-based tweet -> entity mapping + entity_mentions aggregates +
        the x_social rule. Bare-name mention matching waits for Stage B.
        Inspect: does the pool look sane on TODAY's data, and does the
        single-dimension level survive split (un-merged) entities?
        Run benchmark replay as a regression check.

Step 2  Tier 1 provider (llm_provider.py, default DeepSeek) + Tier 2 harness
        (agent_harness.py). Layer 0 Stage B merges (2a) on moving orphans (incl.
        bare X mentions) + Layer 2 candidate de-dup (6.0, also 2a) + feed analysis
        (6.1, Tier 1) -> daily_feed.

Step 3  Daily Feed tab (/api/feed).

Step 4  Layer 3 read-only explore agent (2b) on BOTH surfaces at once:
        /api/chat + Explore tab AND the CLI entrypoint `pipeline/agent.py`
        (external callable, read-only). Tools over ALL collected data, not only
        the pool. CLI ships here, not later (user: external call can be early).

Step 5  Layer 3 editing tools (propose -> approve -> version). Optional thin MCP
        wrapper around the CLI only if a richer protocol is wanted.
```

Each step is independently shippable and verifiable.

## 13. Open Questions

```text
1. Pool size target per day: cap at ~20, ~50, or unbounded-high-recall?
2. Daily feed size: DeepSeek Top 5 / 10 / 20 from the pool?
3. Stage B auto-approve: keep manual-only in v1, or auto-approve above a
   confidence threshold?
5. Cross-source 48h windows: which clock — event_at (as_of_safe) only, or also
   snapshot-only signals?
6. x_social threshold N: how many distinct credible authors in 7d counts as a
   signal? And is the X seed-account list the only "credible" set, or also
   high-follower authors found in tweets?

Temporal model (section 4.7), RESOLVED:
P1  windows = 24h (velocity) + 7d (trend), 30d display-only; acceleration is
    self-relative (K ~ 2). No reliance on prior local snapshots.
P2  github velocity/acceleration: free B-class fields first (sparkline, RepoFOMO
    windows, Trending period_stars); stargazers starred_at backfill only for the
    shortlist / off-board movers.
P3  cheap free signals decide the backfill shortlist (high recall).
P4  cadence = one full run per day.

Metric design (section 4.5):
Q-a  RESOLVED: velocity and acceleration are two independent criteria (D1), not a
     blend. Per-source independent thresholds, OR-combined; level = ordinal max of
     per-source level votes (section 4.2), NOT a max of raw black-box numbers.
Q-b  RESOLVED: absolute thresholds gate membership (D2); percentile ranking-only (D3).
Q-d  RESOLVED (calibrated on 2026-05-30): GitHub three-source thresholds and counts
     in section 4.5. RepoFOMO stars_7d is the pool-size lever -> held watch-unless-
     accelerating. Remaining: calibrate npm / hn / hf / x_social thresholds once
     Layer 0 and backfill exist (they need per-entity grouping / API backfill).
Q-e  RESOLVED (section 4.8 source coverage): github_search = discovery + backfill
     shortlist (no level rule); hn_firebase = new HN front-page rule; pypi excluded
     from v1.
Q-c  OPEN: do ecosystem-echo sources (e.g. HF derivative resources) get a
     per-source max_level cap so they cannot reach high_potential alone?
     User undecided; to discuss.
```

## 14. Hosting and Multi-Tenancy (DECIDED)

Deployment is a hosted web app. Decisions taken:

```text
H1  Universe = GLOBAL (option A). Every account sees the SAME radar: one shared
    config.json, one set of sources/queries, ONE daily pipeline run for everyone.
    Pipeline + Potential pool + feed cost is a constant, it does NOT scale with
    the number of users. (Per-account custom monitoring was option B; rejected
    for v1 because it would make the pipeline run per-account and cost O(users).)
H2  Signup = invite-only. No open registration in v1.
H3  Rule/prompt governance:
      v1: every account is admin and edits the GLOBAL rules/prompts (shared).
      v2: each user can edit their OWN version (per-account fork of rules/prompts).
    So rule_versions/prompt_versions are global in v1; v2 adds an optional
    user_id-scoped overlay.
```

Global vs per-account split (the core of cheap multi-tenancy):

```text
GLOBAL (one copy, daily pipeline writes, all accounts read):
  all radar data + analysis + rules/prompts (see section 9 GLOBAL block).
  The expensive work (collection, entity resolution, Potential, L2 feed) runs
  ONCE per day server-side. Cost independent of user count.

PER-ACCOUNT (keyed by user_id):
  auth/session, chat history, human_status, feedback, saved views, usage quota
  (see section 9 PER-ACCOUNT block). This is a thin overlay on the global radar.
```

Cost model:

```text
- L0 / L1 / L2 (pipeline + feed): GLOBAL, once/day. Constant cost.
- L3 chat: PER-USER, interactive. Cost ~ active users x usage.
  -> per-account usage_quota / rate limiting is a HARD requirement (section 9).
- System holds the API keys (DeepSeek / GitHub / etc.) server-side; users do not
  bring their own in v1. Quotas, not per-user keys, control spend.
```

Stack changes from the personal-local assumption:

```text
- DB:        SQLite -> Postgres (SQLite stays for local dev).
- Backend:   the 159-line http.server toy -> a real web framework (FastAPI/Flask)
             + ASGI server, auth middleware, per-account session + quota.
- Pipeline:  local manual run -> a server-side scheduled job (one global daily run)
             + a worker; users read the latest completed run_id.
- External:  the Layer 3 agent is callable over an AUTHENTICATED HTTP API.
             The local CLI and any future MCP are thin wrappers over that API
             (flips the earlier "CLI-first" note, which assumed local single-user).
- Secrets:   .env on a laptop -> server-side secret management.
```

What does NOT change from hosting:

```text
The entire deterministic core (L0 Stage A, L1, momentum, section 4.7 temporal
model, sparkline sourcing, rules.json structure), the metric design (4.5/4.7),
the benchmark calibration, the provider abstraction / model-swap, and the 0/1/2/3
layering are all unaffected. They were already global batch computation.
```
