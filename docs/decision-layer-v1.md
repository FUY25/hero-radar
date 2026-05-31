# Decision Layer v1

This is the design source of truth for the layer that sits on top of the existing
collection pipeline. The collection pipeline (`pipeline/run_pipeline.py`) already
gathers ~15 source channels into `data/hero_radar.sqlite` (`items` table). This
document specifies the layer that turns those raw rows into a daily decision
workflow.

Status: final design review, not yet built. After founder review, write
implementation plans in the build order in section 12.

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
- The LLM does not make opaque final pool decisions. Deterministic rules do.
  Source-specific LLM classifiers can emit versioned evidence rows (initially for
  X tweets), and rules can consume those rows. The LLM explains, ranks, and
  proposes; it never silently changes production logic.
```

Deployment target: V1 returns to the product-spec-v0.7 assumption. It is a
local/internal tool first, not a hosted multi-account product. Hosted deployment,
invite-only auth, multi-account overlays, quotas, and server-side tenancy move to
V2. See section 14 for the deferred hosting model.

## 1. Shape

```text
collection layer (exists)
  source adapters -> items (raw_json + metadata_json) in SQLite

decision layer (this doc)
  Layer 0  Entity resolution     items rows -> emergent entity clusters
  Layer 1  Decision pipeline      deterministic rules + source classifiers + backfill
                                  -> potential_candidates + edge_watch + evidence_rows
  Layer 2  Feed/deepdive          card generation + feed selection + feed dedupe
                                  + DeepSeek analysis + bounded Kimi deepdive
                                  -> daily_feed/cards + deepdive_reports
  Layer 3  Explore / QA chatbot   read-only deep-dive + propose rule/prompt changes

provider interface
  one OpenAI-compatible provider abstraction, with task routing:
    DeepSeek -> pipeline / deterministic-adjacent / structured analysis
    Kimi     -> Chat / Explore agent / code or long-context understanding
```

The whole decision layer is additive. It reads `items` and writes new tables. It
does not modify the collection pipeline or the existing `items / scores / analyses`
contracts.

## 2. Locked Design Decisions

```text
LLM provider:   abstract OpenAI-compatible provider interface first. Primary V1
                models are DeepSeek + Kimi/Moonshot, both swappable by config.
                DeepSeek handles pipeline / deterministic-adjacent structured
                work. Kimi handles chatbot / agent / code-understanding work.
                Provider layer must run without model keys for Layer 0 Stage A
                and the core deterministic Layer 1 rule evaluator. X source
                classification, L2 analysis, and agents require model keys.

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
                  (secondary key, lower precision than github_repo_key). Do NOT
                  use shared platform domains as project identity.

name_key          normalized exact name / slug (tertiary key). Conservative:
                  used as an alias candidate by default; direct Stage A union only
                  when it is specific enough and not contradicted by stronger keys.
```

Run union-find over these keys. Output: deterministic `entity` clusters keyed by
the strongest available key (github repo id > domain > name). This is automatic,
generalizes to any future project, and links the 70-80% of serious cases that
carry a github URL somewhere in their source metadata.

Precision rules:

```text
- github_repo_key (redirect/repo-id resolved): strong, always merge.
- domain_key: medium. Merge only if not contradicted by different github keys and
  only if the registrable domain is not a shared host / marketplace / platform.
  Blocklist examples: github.io, vercel.app, netlify.app, huggingface.co,
  npmjs.com, pypi.org, producthunt.com, x.com, twitter.com.
- name_key: weak. Merge only for exact normalized match AND only if the name is
  specific enough. Do not Stage-A-union short/generic names such as agent, open,
  studio, browser, desktop, assistant, code, mcp, ai. Those become orphan/alias
  candidates for Stage B or L2 presentation dedupe.
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
                      repofomo), plus precise stargazers/forks backfill for the
                      shortlist. Each source has its own threshold, OR-combined
                      (section 4.5)
package_family        npm downloads are adoption. Version/scoped-package bursts are
                      launch/supply activity: watch alone, stronger only with downloads
                      or verified corroboration.
hn                    hn_algolia: >= 3 matching stories in 7d (per entity);
                      hn_firebase: HN front page + score >= ~100, or same-day as a github accel
product_hunt          launch confirmation: daily_rank <= 5 or weekly_rank <= 5
hugging_face          HF ecosystem signal: >= 2 matching resources in 48h, or 1
                      canonical/exact resource same-day as another signal
x_social              LLM-assisted source classifier over X tweets, grounded by
                      deterministic tweet/entity aggregates. It emits an auditable
                      x_tier plus rationale/evidence (see section 4.4).
cross_source          counts, but is split by merge quality:
                      - verified_cross_source: distinct source families joined by
                        github_repo_key / domain_key / package repository / PH website
                        / HF card link / HN URL. Can cast or upgrade a level.
                      - fuzzy_cross_source: name-only or semantic match. Confidence and
                        priority only; never the sole reason to enter Potential.

discovery (not a level rule):
github_search         widest net; feeds the entity universe + stargazers-backfill
                      shortlist for off-board movers (section 4.8)
excluded from v1:     pypi (benchmark: late/weak; kept for L3 explore only)
```

Triggers are evaluated over the whole entity cluster (all alias rows), not per raw row.

### 4.2 Deterministic level (no LLM) -- independent sources + verified corroboration

Hard requirement (user): cross-source evidence DOES count, but it cannot rely on
loose project-name aggregation. Entity resolution (Layer 0) is imperfect; the same
project will sometimes split into several un-merged entities, and short names can
merge unrelated projects. So V1 separates independent source votes from
cross-source corroboration quality:

```text
Each source has its OWN independent criterion on its OWN scale (sources differ;
two GitHub boards are black boxes with different definitions). Each source casts a
LEVEL vote (none/watch/potential/high) using its own thresholds. The entity level
is the MAX over those LEVEL votes -- an ordinal max over comparable levels, NOT a
max over incomparable raw numbers. Any single source reaching a band promotes the
entity (OR semantics).

Verified cross-source evidence is itself a deterministic vote if the join key is
strong enough. Fuzzy/name-only cross-source evidence is still useful, but only for
confidence and priority.
```

Why ordinal-max, not raw-number-max: MAX(a,b,c) >= T with one SHARED cutoff T is
mathematically identical to "a>=T OR b>=T OR c>=T". That is fine, but using ONE
shared cutoff across heterogeneous black-box metrics is wrong. So each source gets
its OWN cutoff, the comparison becomes a per-source pass/fail -> level vote, and we
max over the (comparable) level votes.

```text
none            no source votes above its watch threshold
watch           some source votes watch
potential       some source votes potential, OR verified_cross_source has two weak
                source-family signals within 48h
high_potential  some source votes high, OR potential + verified corroboration, OR
                two source families vote potential/strong within 48h
```

The exact momentum definition, normalization, and bands are in section 4.5 and
live in `rules.json` so they are tunable and versioned. Corroboration (section
4.6) can affect level only when it is verified by strong join keys; fuzzy
corroboration cannot gate or create the base level.

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

### 4.4 X / social signal flow (pipeline LLM source classifier)

An extraction flow inside the pipeline that maps recent tweets to entities, asks
an LLM to summarize/judge the X-specific signal, and feeds the `x_social` trigger
family. This is NOT a separate agentic system and NOT the Layer 3 chatbot. It is
a fixed-prompt, versioned, auditable source classifier whose output participates
in the same `none / watch / potential / high_potential` level system as GitHub,
HN, npm, PH, and HF.

Source: the existing `x_tweets_store` table (already populated, ~2.2k rows as of
the 2026-05-30/31 audit; created_at indexed). This flow does not touch source
adapters; it adds one more evidence-producing source into the same Potential
evaluation.

```text
scope        rolling 7d of x_tweets_store (also compute a 24h sub-window).

map tweet -> entity (reuses Layer 0):
  1. github / project links in tweet text -> Stage A deterministic join key
     -> attach mention to the resolved entity.
  2. bare @handle / project name / hashtag with no link -> orphan candidate;
     only goes to Stage B (LLM) if the entity is already moving (same gate as 3.2).

aggregate per candidate entity / mention cluster (7d and 24h):
  distinct_authors          number of distinct authors mentioning the entity
  credible_authors          subset whose handle is in the X seed-account list (config)
  mention_count
  mention_acceleration      24h count vs prior-baseline rate
  expression_summary        extracted recommendation / usage / emotion snippets
  engagement_context        likes/replies/reposts/views, display-only by default
  linked_mentions           repo/domain/product links extracted from tweets
  cross_source_context      existing GH/HN/npm/HF/PH evidence rows for entity binding
                            and corroboration notes, not for inflating x_only_tier

x_social LLM source judgment (DeepSeek, fixed prompt, versioned, auditable):
  input   compact tweet bundle + aggregates + linked_mentions + cross_source_context
  output  x_tier = none | watch | potential | high
          x_summary
          x_rationale
          x_expression_strength = neutral | recommendation | strong_recommendation
                                  | adoption_or_usage | strong_emotion | mixed
          cited_tweet_ids
          detected_project_aliases
          entity_confidence = linked | exact_handle | fuzzy_name
          cross_source_notes = verified | fuzzy | none

Layer 1 consumes x_tier as the X source-family vote, but only if the output cites
tweet ids and entity_confidence is strong enough for the claimed tier. Once
accepted, x_tier is just another source-family level vote. It can move an entity
to watch / potential / high_potential through the same ordinal-max and verified
corroboration rules as every other source.

Initial X-only tier rubric:
  none       generic term, unclear entity, or no meaningful recommendation/emotion.
  watch      one credible/relevant account strongly recommends, uses, or emotionally
             highlights a concrete entity; OR multiple weak mentions with a clear
             entity but weak expression.
  potential  multiple independent credible/relevant authors mention the same
             concrete entity, especially with recommendation, curiosity, usage, or
             emotionally strong language.
  high       multiple independent authors mention the same concrete entity and at
             least several of the tweets show strong recommendation, adoption/usage,
             or strong emotional emphasis. Engagement counts are not required.
```

Design constraints to avoid noise:

```text
- The LLM should primarily consider distinct authors, credible-author quality, and
  expression strength. Engagement is snapshot_only and should be display/context,
  not a tier driver.
- event_at = tweet created_at (as_of_safe). Engagement counts are partial_as_of.
- Generic known_terms are context only unless the LLM can bind them to a concrete
  repo/domain/product/entity with cited tweets. High requires a larger linked-author
  burst, strong content, or verified corroboration, because social text is easy to
  over-merge.
- x_tier is the X-only source judgment. Cross-source context is emitted separately
  as `cross_source_notes`; level upgrades from X + GitHub/HN/npm/HF/PH are handled
  by the verified_cross_source rules in section 4.2/4.6.
- Mention -> entity mapping confidence is carried on the evidence row, same as
  any other alias link (strong = link in tweet, weak = bare name match).
```

Processing architecture (TWO LLM stages: cheap batched triage, then bounded
per-entity detail). The "which tweets matter" filter is semantic and cannot be
deterministic -- many hyping tweets carry no github link and no obvious keyword --
so Stage 1 uses an LLM, but batched and cheap, never one call per tweet.

```text
Stage 0  deterministic pre-extract (free, no LLM)
  Regex github URLs, @handles, #hashtags, explicit mentioned_projects per tweet.
  Catches the easy linked cases and gives Stage 1 structured hints. NOT the filter.

Stage 1  batched LLM triage / normalize (cheap model, BATCHED)
  Send ALL 7d tweets to the LLM in BATCHES (~50-100 tweets per call, never one call
  per tweet). Per tweet output:
    { tweet_id, about_concrete_project, project_refs[], expression_strength, closer_look }
  closer_look filters the noise; project_refs normalize the entity mention. Cost is
  a few dozen cheap calls for the whole 7d window.

Aggregate  deterministic, on closer_look tweets only
  group by project_ref -> per-entity stats: distinct_authors, credible_authors
  (seed list), mention_count, mention_acceleration.
  github/url ref -> Layer 0 Stage A links to an entity; bare-name ref -> matched to
  an existing entity, else held for Layer 0 Stage B (only if already moving).

Stage 2  per-ENTITY tier judgment (LLM, careful, BOUNDED)
  Only entities past a cheap gate (credible_authors >= threshold) get ONE detailed
  call each. Input = that entity's tweet bundle + aggregates + linked_mentions +
  cross_source_context. Output = the x_tier object above. Bounded by the gate.
```

Cost discipline: Stage 1 is cheap + batched (filter the firehose); Stage 2 is the
careful call but bounded by the aggregate gate. Never one LLM call per tweet. Stage 1
may use a cheaper model than Stage 2. (Scope: built in the Layer-2 plan; the
pre-Layer2 slice is fully deterministic and contains no X classifier.)

Evidence rows from this flow use the section 4.3 shape with `source = x_tweets`,
`family = x_social`, and `metric_name` in {distinct_authors, credible_authors,
mention_count, mention_acceleration, x_tier, x_llm_summary,
x_expression_strength}.

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

D4  MAX-of-signals stays for independent source votes: an entity qualifies at a
    level if ANY single signal passes that level's absolute criterion. Verified
    cross-source can also cast/upgrade a deterministic vote; fuzzy cross-source
    only affects confidence/priority. This survives L0 splits without ignoring
    real multi-source resonance.
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
github_trending   GitHub-native stars_today (transparent). Use DAILY period for
                  level thresholds; weekly/monthly are context/priority, not the
                  same threshold scale.
                  watch >= 300 | potential >= 1000 | high >= 3000   (stars/24h)
                  2026-05-30 hits: 19 / 3 / 0
trending_repos    black-box stars_velocity (daily) + sparkline (7-pt) for accel
                  watch >= 300 | potential >= 800 | high >= 2500     (its daily velocity)
                  2026-05-30 DAILY hits: 55 / 20 / 2 ; accel = sparkline slope rising
                  forks_velocity:
                    watch >= 50 | potential >= 75 | high >= 300
                    2026-05-30 DAILY hits: 21 / 21 / 3 at >=75/>=300
repofomo          black-box stars_7d -- a 7-day CUMULATIVE window, so it is laggy
                  velocity: watch >= 200 (stars_7d); standalone TOPS OUT AT WATCH.
                  potential ONLY if also accelerating now:
                    stars_7d/7 > stars_30d/30 > stars_60d/60
                    and stars_7d >= 1000.
                  high >= 5000 (stars_7d) OR new_forks >= 300 with repo momentum.
                  2026-05-30 hits: 302 watch / 40 potential-like / 8 high by s7
                  -> RepoFOMO's threshold is the pool-size LEVER; held to
                     watch-unless-accel keeps the pool sane.
github_search     DISCOVERY only, NOT a level rule. Feeds the universe + the
                  stargazers-backfill shortlist for off-board movers (section 4.8).
precise github backfill  first-class GitHub rule once a repo is shortlisted:
                  stars_24h >= 1000 OR stars_7d >= 2000 OR
                  24h rate >= K x prior-6d daily rate (K ~ 2), from stargazers
                  starred_at. Forks: forks_24h >= 75 OR forks_7d >= 300.
                  Anchored to Hermes (+1094 stars/+94 forks in 24h) and OpenClaw.
```

```text
OR-deduped GitHub pool on 2026-05-30 (per-source thresholds above):
  watch 327 / potential 82 / high 10.
  With RepoFOMO held watch-unless-accel, potential drops to ~20-40 (target size).
  Benchmark check: NousResearch/hermes-agent = 1290 stars/day -> potential. OK.
```

Non-GitHub sources (each its own criterion; per-entity ones need Layer 0 first):

```text
npm           adoption trigger = daily_downloads >= 10k and rising. High if
              daily_downloads >= 100k or strong sustained growth. Version/scoped
              bursts without downloads are watch only; they become potential only
              with downloads rising or verified corroboration. Strong parts need
              npm API backfill; local npm_search only has weekly/monthly downloads.
hn_algolia    strict matching only. 1 strict canonical/repo/domain story = watch;
              >= 3 strict matching stories in 7d = potential; dedupe by HN objectID.
hn_firebase   HN front page (top/best) with score >= ~100 AND a strict URL/entity
              match = potential/high depending score/comments; any front-page story
              same-day as a GitHub accel is corroboration if entity-linked.
product_hunt  daily_rank <= 5 OR weekly_rank <= 5 = potential/confirmation.
              Single-source PH normally tops out at potential; high requires
              verified corroboration or exceptional PH engagement plus product fit.
hugging_face  >= 2 exact/canonical matching resources in 48h = potential. Single HF
              resource same-day as GitHub/npm/HN strong = corroboration. HF alone
              normally tops out at potential unless metrics are extreme and exact.
x_social      LLM-assisted source-family vote over tweet bundles. The LLM can use
              people-count, credible-author quality, and recommendation/emotion
              strength to assign x_tier. Engagement is display/context, not a tier
              driver. Existing cross-source evidence can be used for entity binding
              and cross_source_notes, but x_tier remains X-only. Generic known_terms
              such as "OpenAI", "Claude", "MCP" are context unless bound to a
              concrete entity. Output must cite tweet ids and carry entity/link
              confidence; fuzzy x_tier cannot be the sole reason for Potential.
pypi          EXCLUDED from v1 triggers (benchmark: late/weak); kept for L3 explore.
```

```text
level bands: each source carries its own watch / potential / high_potential
threshold in rules.json. All absolute, benchmark-traceable, versioned. Entity
level = the highest LEVEL VOTE across its sources (section 4.2 ordinal max).
```

Resolved (Q-c): source families CAN have source-specific max levels. GitHub,
precise GitHub backfill, npm downloads, and strict HN can reach high alone when
their own high thresholds pass. PH and HF are usually confirmation/ecosystem echo:
they can reach potential alone, but high normally requires verified corroboration
unless their own native metrics are extreme and exact. X can reach potential from
credible linked mentions; high requires a larger linked-author burst or verified
corroboration.

### 4.6 Corroboration axis

```text
corroboration = count of DISTINCT source families that independently show momentum
                + cross-source-within-48h flag
                + L0/L2 merge confidence
                + cross_source_quality: verified | fuzzy

effects (allowed):
  - raise the candidate's confidence label
  - raise feed priority (Layer 2)
  - verified: cast potential from two weak source-family signals within 48h
    (therefore this is NOT merely an edge_watch case)
  - verified: upgrade potential -> high_potential when the corroborating source is
    itself strong enough
  - fuzzy: add context only; never create Potential or high_potential

effects (forbidden):
  - fuzzy/name-only cross-source can NEVER create base Potential.
  - cross-source can NEVER be required to reach base Potential. Single-source
    momentum must always be able to promote an entity.
```

`edge_watch` / near-miss queue (for Layer 2 deepdive, not a level):

```text
edge_watch is a review queue, not a fourth deterministic level. It contains
watch-level or near-miss entities that do NOT yet satisfy Potential, but are worth
extra bounded inspection.

Examples:
  - one credible X author strongly recommends a concrete entity, but no second
    independent author yet
  - HN / X mentions a project by name but lacks a strong repo/domain binding
  - GitHub Search finds a young/recently pushed repo with enough total stars, but
    no native velocity yet
  - RepoFOMO weekly mover is watch-level but has not passed acceleration
  - fuzzy/name-only cross-source evidence looks interesting but is not verified

Important boundary:
  - two verified weak source-family signals within 48h -> Potential.
  - edge_watch is only for unverified / single-weak / missing-backfill near misses.
  - Layer 2 deepdive can add priority/caveats or propose evidence/backfill/merge
    work, but it cannot directly promote edge_watch to Potential. Promotion only
    happens when deterministic rules rerun over newly verified evidence.
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
  "Backfill" here means precise补数 for a shortlisted entity. It is NOT historical
  replay and NOT a formal backtest. It calls source APIs after a cheap first pass
  says an entity might matter.

  GitHub stargazers starred_at (and npm daily-downloads) is the precise but
  expensive path. NOT run on all repos. Reserved for:
    - the shortlist of repos that already look like movers from free B-class /
      event-count signals, and
    - off-board movers that show signal in HN / npm / X but are on no GitHub board.
  Free signals decide the shortlist; backfill confirms and adds precision.
  Needs GITHUB_TOKEN. Cache pulled star curves to avoid re-pulling within a day.

  Where it acts in the pipeline:
    raw source rows
      -> L0 Stage A entities
      -> cheap/free Layer 1 rules
      -> potential_candidates + edge_watch + backfill_queue
      -> precise backfill for the queue only
      -> new evidence_rows (e.g. precise stars_24h, acceleration ratio, npm downloads)
      -> rerun/recompute affected Layer 1 rules
      -> final none / watch / potential / high_potential for the run

  Backfill can upgrade an entity only by adding verified facts that deterministic
  rules then consume. It is not an agent judgment.
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

### 4.7.1 Daily run, cache, and idempotency

V1 is designed as a daily system even before cron exists. Cron/scheduler wiring is
deferred, but every stage should be built as if it will run once per day against
fresh source snapshots.

```text
daily cadence assumption
  - one full run per day.
  - manual run now; scheduler/cron is a later Plan G.
  - each run has a stable run_id and run_started_at.
  - re-running the same stage for the same run_id should be safe/idempotent.

cache goals
  - avoid paying/rate-limiting for the same backfill or model call every day when
    inputs have not changed.
  - make daily runs resumable after partial failure.
  - preserve enough inputs/outputs to debug why a candidate/card changed.

cache keys
  - source API backfill: source + external_id/repo/package + window + fetched_at date.
  - GitHub star curves: repo_id/full_name + requested window/date. TTL about 24h
    for live runs; keep historical stored result for inspection.
  - npm downloads: package + window/date.
  - X source classifier: prompt_version + model + entity/mention_cluster +
    hash(tweet_ids + tweet_text + linked_mentions + aggregate counts).
  - Layer 2 lightweight analysis: prompt_version + model + entity_id +
    hash(evidence_rows + source_facts + level).
  - bounded deepdive: prompt_version + model + entity_id +
    hash(evidence_rows + fetched repo/web refs) + deepdive_profile_version.

invalidations
  - new source snapshot/run_id can enqueue new work.
  - changed rules.json/rule_version invalidates deterministic candidate outputs,
    not raw backfill cache.
  - changed x_social prompt_version invalidates x_tier classifier outputs.
  - changed feed/deepdive prompt_version invalidates Layer 2 analysis/deepdive outputs.
  - changed entity alias_links can invalidate candidate grouping and feed cards.

failure policy
  - failed backfill/model calls are recorded with error/status and can be retried.
  - a failed expensive stage should not erase previous successful raw data.
  - Layer 1 can still produce candidates without optional backfill/deepdive; absence
    is a caveat, not a negative signal.
```

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
product_hunt        yes    yes           launch confirmation / potential
x_tweets            yes    yes (L0)      x_social
github_search       yes    NO  -> gap 1
hn_firebase         yes    yes           hn family (front-page/top/new/best)
pypi newest/updates yes    NO  -> gap 3
x_seed_accounts     yes    n/a           it is the monitoring list (config), not a signal
```

Decisions:

```text
gap 1  github_search -- DO (early-discovery backbone)
  github_search has no native velocity, but it DOES carry stars + created_at +
  pushed_at, so we compute a CHEAP velocity proxy with zero API cost:
    stars_per_day = stars / days_since_created   (lifetime-average star velocity)
  Off-board backfill shortlist (cheap pre-filter, no backfill yet) = entities with
    stars_per_day >= 50 AND pushed within ~14d (active) AND created within ~180d
    (young, so lifetime avg ~ recent) AND not on any board.
  On 2026-05-31 this narrows github_search 846 -> ~109; rank by stars_per_day and
  hard-cap at backfill_max_jobs (40). ONLY this shortlist gets stargazers backfill,
  which then computes the PRECISE stars_24h/7d that actually votes the level
  (section 4.5 precise-github-backfill rule). stars_per_day is only the selector;
  the precise recent velocity is the decider.
  Also enqueue any repo externally mentioned by HN/npm/HF/PH/X with a verified
  repo/domain link. Limitation: lifetime stars_per_day misses old-dormant-then-
  surging repos (Hermes-style), but those are caught by the boards anyway.

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

### 4.9 Source-rule audit after benchmark

Current source fields and rule reasonableness as of the 2026-05-30/31 local data
audit:

```text
github_trending
  Reasonable if level thresholds use DAILY period only. Weekly/monthly period_stars
  are much larger scales and should be display/priority context, not the same
  300/1000/3000 gate.

trending_repos
  Reasonable as an independent black-box GitHub mover. Use stars_velocity and
  forks_velocity; sparkline/freshness_bonus are priority/acceleration context.
  Do not use freshness_bonus alone as a level rule.

repofomo
  Reasonable but should be tightened. It is useful but laggy, and broad stars_7d
  gates create too many weekly movers. stars_7d >= 200 is watch, not potential.
  Potential requires 7d daily-rate acceleration or strong fork momentum. This keeps
  the pool from exploding while preserving high recall for weekly movers.

github_search
  Reasonable only as discovery/backfill queue. It currently returns many already
  large repos because queries are broad (agent/topic:ai stars:>20), so stars_total
  cannot be a level rule.

hn_algolia / hn_firebase
  Reasonable if strict entity matching and HN objectID dedupe are mandatory.
  Broad keyword hits are evidence to read, not direct Potential triggers.

npm_search / npm registry backfill
  Current local npm_search weekly_downloads is too mature-package biased to be a
  strong rule by itself. The actual V1 rule should call npm package/download APIs
  for daily downloads and publish events on candidates. Downloads are adoption;
  version bursts are supply-side launch activity.

product_hunt
  Good confirmation source, not usually earliest for developer tools. daily/weekly
  rank <= 5 can enter Potential, but single-source PH should not normally make High.

huggingface
  Current fields are rank/trendingScore/likes/downloads/createdAt/lastModified/tags.
  Good for ecosystem echo and demos/models/datasets, but derivative risk is high.
  Exact/canonical matching can make Potential; High needs extreme native metrics or
  verified corroboration.

x_tweets
  LLM-assisted. Deterministic code builds tweet bundles and aggregates, then a
  versioned LLM prompt summarizes and assigns the X source tier with cited tweet
  ids. The main tier drivers are number of distinct relevant accounts and expression
  strength (recommendation, usage/adoption, strong emotion). Engagement is not a
  tier driver. Cross-source context is still useful, but kept separate as
  corroboration/verification rather than mixed into the X-only tier. Generic
  known_terms ("Claude", "OpenAI", "MCP") remain context unless tied to a concrete
  entity.

pypi
  Keep excluded from V1 triggers. It can be weak corroboration in Explore, but the
  Hermes/OpenClaw benchmark did not support it as an early signal.

ossinsight
  Configured optional source but no current rows in the local DB. Treat as optional
  GitHub corroboration only until the endpoint is stable and we inspect fields.
```

## 5. LLM Harness and Providers

Two tiers, because the jobs have different cost, latency, context, and tool-use
profiles. V1 defaults to DeepSeek for pipeline work and Kimi/Moonshot for
agentic/chat/code-understanding work. Both are behind the same OpenAI-compatible
provider abstraction so we can swap models without rewriting the pipeline.

```text
Tier 1  Pipeline provider (single-shot, cost-sensitive, high-volume)
  pipeline/llm_provider.py
    Provider protocol: complete(messages, *, tools=None, json_schema=None) -> response
    Default: DeepSeekProvider.
    Used for:
      - X source classifier (x_tier evidence rows)
      - Layer 2 per-candidate feed analysis
      - deterministic-adjacent semantic judgments
      - structured summaries / JSON outputs
      - cheap batch classification where tools are not central
    Reason: pipeline calls are high-volume, bounded, and mostly structured.
    DeepSeek's JSON output mode fits this shape; a plain API call is enough.

Tier 2  Agentic harness (tool-using, model-swappable). ONE shared architecture,
        multiple configured profiles. They share tools but differ in scope,
        autonomy, limits, and write permissions.
  pipeline/agent_harness.py + a shared tool registry.

  2a  Pipeline judgment agent (constrained, mostly deterministic context)
      Used by Layer 0 Stage B merges and Layer 2 de-dup. These stages are
      deterministic-first and pipeline-like; the agent only runs the LAST step:
      go look at the actual repo/source, decide the merge, and fill structured
      fields. Bounded tool set, structured output, low autonomy.
      Default model: DeepSeek unless the task needs long-context repo/code
      understanding, in which case route to Kimi.

  2b  Bounded deepdive agent (candidate-scoped, limited)
      Backs Layer 2 deepdive. It can web search, inspect selected repo files, read
      product docs/homepages, and synthesize whether a candidate is a meaningful
      product/workflow shift. Hard budgets on pages/files/time/context. It writes
      analysis, caveats, next_questions, and proposals; it never changes levels or
      production rules directly.
      Default model: Kimi/Moonshot for repo/code/web understanding; DeepSeek for
      compact fixed-schema summaries when context is already assembled.

  2c  Explore agent (flexible)
      Backs Layer 3. Broad tool set over ALL collected data, multi-step,
      free-form, higher autonomy. V1 can call it through the local API / CLI;
      authenticated hosted HTTP access is V2.
      Default model: Kimi/Moonshot.
      Reason: this path benefits most from long context, tool use, multi-turn
      exploration, and code/source understanding.

  All profiles share the harness and tool registry; they differ in tool scope,
  autonomy, budgets, write permissions, and output contract. Model is swappable
  (DeepSeek / Kimi / Claude / others), but V1 routing defaults to DeepSeek for
  pipeline and Kimi for agentic/code/web work.

  Framework choice (recommendation): a thin self-written tool-loop over a provider
  abstraction (LiteLLM-style) for model-swap; NOT LangGraph, NOT the DeepSeek SDK
  as a framework. The one genuinely code-heavy capability (2c "explain / search OUR
  code") is delegated to a coding agent used as a single tool. In V2 hosted mode,
  this tool runs IN-PROCESS via the Claude Agent SDK (or an API-based file-reading
  tool over a server-side repo checkout); it does NOT shell out to a local coding
  CLI per request (that does not isolate or scale on a shared server). 2a needs
  only github API + web-fetch + DB tools, no coding agent.

Hard rule: Layer 0 Stage A and the core Layer 1 rule evaluator are LLM-free and
fully deterministic. The x_social classifier is a pipeline evidence producer that
runs before rule evaluation, writes versioned x_tier evidence rows, and then the
deterministic rules consume those rows as data. The rule evaluator never calls an
LLM inline.
```

### 5.1 Model selection per stage (V1, DeepSeek)

X processing and all pipeline LLM work use DeepSeek (user decision). Verified
current 2026-05 from the DeepSeek API docs. Legacy names `deepseek-chat` /
`deepseek-reasoner` retire 2026-07-24 (they map to deepseek-v4-flash non-thinking /
thinking) -- use the V4 names directly.

```text
DeepSeek V4 models:
  deepseek-v4-flash   cost-effective; thinking + non-thinking modes.
                      ~$0.14 in / $0.28 out per 1M tokens (cache-miss).
  deepseek-v4-pro     flagship reasoning; ~$1.74 in / $3.48 out per 1M tokens.
  Both: 1M context, up to 384K output. OpenAI-compatible base_url https://api.deepseek.com.
  JSON: set response_format {"type":"json_object"} AND instruct JSON in the prompt.
  Function calling is NOT supported in thinking/reasoner mode -> use JSON output, not tools.
```

Per-stage routing -- cheap flash for high-volume filtering, latest pro for anything
that JUDGES a project (user: "判定项目要用最新的模型；前面的 stage 没必要用最新那个"):

```text
stage / job                                model              why
X Stage 1  batched tweet triage/normalize  deepseek-v4-flash  high volume, batched, cheap; non-thinking
X Stage 2  per-entity x_tier judgment      deepseek-v4-pro     project-level judgment -> best model
Layer 0 Stage B  orphan merge judgment     deepseek-v4-pro     correctness-affecting, low volume
Layer 2  per-candidate feed analysis       deepseek-v4-pro     user-facing judgment (flash if cost-bound)
Layer 2  candidate de-dup                  deepseek-v4-flash   bounded structured judgment
```

Keep model names in config / rules, not hardcoded, so they can be swapped (V4 ->
next version, or to Kimi/Claude) without code changes. Tier 2 agentic/code/web
(2b/2c) default to Kimi/Moonshot per section 5; confirm current Moonshot model
names at their platform docs.

References (hand these to the implementing engineer):

```text
DeepSeek API docs (root)        https://api-docs.deepseek.com/
Models & pricing                https://api-docs.deepseek.com/quick_start/pricing
Create chat completion (ref)    https://api-docs.deepseek.com/api/create-chat-completion
Reasoning / thinking guide      https://api-docs.deepseek.com/guides/reasoning_model
List models                     https://api-docs.deepseek.com/api/list-models
OpenAI-compat base_url          https://api.deepseek.com   (also /anthropic for Anthropic format)
Moonshot / Kimi (Tier 2)        https://platform.moonshot.ai/docs   (verify current model names)
```

## 6. Daily Feed (Layer 2)

Layer 1 produces a candidate universe, not a reading list. `potential` and
`high_potential` mean "this entity may deserve attention because it passed the
movement rules." They do NOT mean "show every one of these in today's Feed."

Layer 2 is responsible for turning candidates into the actual daily product
surface:

```text
inputs
  - potential_candidates (potential / high_potential)
  - selected edge_watch candidates
  - evidence_rows, source_facts, entity/alias state, backfill outputs

jobs
  - dedupe split candidates into one presentation card
  - generate a concise card for each reviewed candidate
  - assign LLM Priority
  - decide feed visibility and bucket:
      today_focus      must read today
      secondary        worth scanning
      backlog          keep available but not prominent
      suppress         candidate exists, but do not show in Feed by default
  - optionally run bounded deepdive for high-priority / unclear cases

output
  - daily_feed card rows with display_decision / bucket / priority
  - deepdive_reports for candidates that received deeper inspection
  - candidate pool remains separately browseable even when a candidate is not
    selected into the visible Daily Feed
```

Therefore:

```text
Layer 1 level        "is this moving enough to become a candidate?"
Layer 2 feed choice  "which candidate cards should I actually look at today?"
```

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
    structured fields.
effect:  feed shows ONE merged card per project; its evidence is the union of the
         fragments' evidence; momentum is the MAX across fragments.
```

This is L2 aggregation as a safety net over L0, not a replacement for it. It is
the same architecture as Stage B but a separate configured instance (section 5,
"相似架构但是 2 套").

Important boundary:

```text
L0 alias merge      long-lived entity fact. Writes alias_links only after approval
                    or explicit auto-approval policy.
L2 feed dedupe      today's presentation merge. Does NOT modify alias_links or the
                    long-lived entity graph. Store grouped fragment ids on
                    daily_feed / daily_feed_candidate_refs.

If the L2 agent believes a presentation merge should become a permanent alias, it
may create an entity_merge_proposal. It still does not silently apply it.
```

Implementation-plan detail: the exact ordering, cache keys, bucket cutoffs, and
2a invocation points are specified in the plan. The product boundary is locked:
L2 may group for presentation and analysis, but it does not rewrite the long-lived
entity graph.

### 6.1 Per-candidate analysis

```text
input per candidate:  compact JSON context
  { entity, canonical_entity, level, fired_families, evidence_rows (trimmed),
    source_facts }

call:                 one Tier 1 provider call per reviewed merged candidate.
                      Default reviewed set = level >= potential. L2 can also include
                      selected edge_watch, and can suppress low-priority Potential
                      cards from the visible Feed after analysis.

output analysis_json:
  priority            0-100
  thesis              one short paragraph
  why_now
  caveats             e.g. PH missing is not negative; alias confidence weak
  paradigm_judgment   new paradigm / old paradigm new execution / clone / unclear
  next_questions      suggested chatbot follow-ups

cost control:         analyze level >= potential for now; dedupe by (entity, day); cache.
                      If cost becomes real, add a soft daily budget later, not a
                      hidden hard cap on the Potential pool.
```

Reuse the existing `analyses` table (store analysis_json in `analysis_text`, set
`judgment`) plus a new `daily_feed` table for cards / ranking / display decisions.
Feed buckets:

```text
today_focus     high-priority card to actually read today
secondary       worth scanning
backlog         keep in the system, not prominent
suppress        reviewed candidate, not shown in Feed by default
```

### 6.2 Bounded deepdive over Potential+ and edge_watch

Layer 2 includes a bounded deepdive pass. Its job is to decide "is this a
paradigm/product opportunity worth attention?", not "did it have enough movement
to enter the pool?" Movement is still Layer 1.

```text
default scope
  - all level >= potential candidates get lightweight analysis.
  - high_potential, top-priority potential, and selected edge_watch candidates can
    get a deeper Kimi-backed investigation.

edge_watch scope
  - single weak but interesting source signal
  - fuzzy/name-only social or HN signal that needs binding
  - GitHub Search/off-board repo queued for precise backfill
  - watch-level RepoFOMO / X / HN / PH / HF near misses

tools allowed
  repo inspection:
    - read README / docs / examples
    - shallow clone or fetch selected GitHub files when needed
    - inspect package manifests and top-level source tree
    - identify product surface: CLI / web app / MCP / agent / extension / SDK
    - detect wrapper/demo/list/resource repo vs real implementation
  web/product inspection:
    - web search project/entity name with bounded query budget
    - fetch homepage / docs / landing page
    - fetch launch/blog/discussion pages when linked from evidence
  source evidence:
    - read evidence_rows and raw source rows
    - read cited X tweets
    - read HN comments / PH details / npm/HF metadata when relevant

hard limits per candidate
  - max web pages
  - max repo files
  - max runtime
  - max model context budget
  - no arbitrary browsing unless the query is derived from the candidate/entity

output
  priority
  paradigm_judgment
  product_thesis
  workflow_shift
  technical_substance
  cited evidence summaries
  caveats
  next_questions

effects allowed
  - raise/lower LLM Priority
  - write readable analysis / caveats / next questions
  - propose rule / prompt / entity-merge changes
  - enqueue backfill if it discovers a concrete repo/domain/package link

effects forbidden
  - directly change none/watch/potential/high_potential
  - directly write alias_links or production rules
```

Model routing:

```text
DeepSeek   compact fixed-schema analysis over already-assembled evidence.
Kimi       deeper repo/code/web/source inspection when long context or code
           understanding matters.
```

## 7. Explore / QA Agent (Layer 3)

Layer 3 is NOT just a chatbot over the Potential pool (user comment C2). It is a
model-swappable agentic harness (section 5, Tier 2) whose scope is ALL collected
data and the system itself, with several uses:

Boundary vs Layer 2: Layer 2 bounded deepdive is a scheduled, candidate-scoped
investigation with hard limits and structured output. Layer 3 is interactive
free-form exploration over the whole corpus/system. They can share tools and Kimi,
but they are different products and different risk profiles.

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
                                    set by source-specific rules plus verified
                                    cross-source corroboration (section 4.2).
2. LLM Priority Score              0-100, from the Layer 2 provider. Orders the feed.
                                    Does NOT decide pool membership.
3. Feed Display Decision           today_focus / secondary / backlog / suppress.
                                    Set by Layer 2. Decides which cards are shown
                                    prominently today. Does NOT decide pool membership.
4. Human Status                    unreviewed / opened / watching / dismissed /
                                    promoted / needs_followup / rule_feedback.
                                    Set by user actions in the UI.
```

## 9. Storage

V1 target: local/internal SQLite. One user, one local config, one local run
history. Hosted Postgres and multi-account separation are V2 concerns, not V1
requirements. The schema still separates "radar data" from "local user state" so
the later V2 move is straightforward, but V1 does not need auth/user_id/session
plumbing.

```text
V1 RADAR DATA (single local copy; written by the daily/local pipeline)
  decision_runs           run_id, source_snapshot_run_id, started_at, completed_at,
                          status, config_hash, rule_version, note
  entities                cluster id, canonical_entity, canonical_key, key_type, first_seen
  alias_links             entity_id, source, external_id/handle, confidence, origin, approved
  entity_merge_proposals  Stage B LLM proposals (orphan, target, confidence, reason, status)
  potential_candidates    entity_id, run_id, level, fired_families_json, first_trigger_at
  edge_watch_candidates   entity_id, run_id, reason_json, source_refs_json, status
                          (near-miss review queue; not a deterministic level)
  backfill_jobs           entity_id, run_id, source, reason, status, requested_at,
                          completed_at, result_ref
  entity_mentions         entity_id, window (7d/24h), distinct_authors, credible_authors,
                          mention_count, mention_acceleration, run_id (section 4.4)
  evidence_rows           per section 4.3
  daily_feed              run_id, entity_id, display_decision, bucket, priority,
                          card_json, analysis_id
                          (Layer 2 card/visibility decision; Potential does not
                          imply visible Feed placement)
  daily_feed_candidate_refs
                          daily_feed_id, entity_id, fragment_entity_id, reason
                          (L2 presentation grouping; does not modify alias_links)
  deepdive_reports        run_id, entity_id, scope (potential|edge_watch), model,
                          report_json, created_at
  api_cache               cache_key, source, external_id, window, input_hash,
                          response_json, status, fetched_at, expires_at, error
  llm_cache               cache_key, provider, model, prompt_version, input_hash,
                          output_json, status, created_at, error
  rule_versions           version, rules_json, created_at, author, note   (v1: global, any admin)
  prompt_versions         version, kind, prompt_text, created_at, author, note
  rule_proposals          diff, rationale, status, created_from_feedback
  prompt_proposals        diff, rationale, status

V1 LOCAL USER STATE (no auth/user_id)
  chat_sessions           local messages, created_at (L3 history)
  human_status            entity_id, status (watch/dismiss/promote/...)
  feedback_events         payload, created_at
  saved_views             config

V2 HOSTED / MULTI-ACCOUNT ADDITIONS (deferred)
  accounts                id, email, invite/auth, role, created_at
  sessions                auth/session tokens
  usage_quota             user_id, llm_calls, tokens, window
  user_id-scoped overlays for chat_sessions, human_status, feedback_events,
                          saved_views, and optional user-specific rules/prompts
```

Reuse `analyses` (global) for the LLM analysis text/judgment.

Note: in V1, human_status is local state. In V2, it becomes per-account. The
radar level/priority remain shared batch outputs either way.

## 10. UI

V1 UI target: local web app/dashboard. No login, no invite system, no per-account
state. Local APIs can read/write local config and local SQLite. V2 adds hosted
auth, user_id, quotas, and per-account state.

Core stance: make the deterministic reasoning legible and the daily review fast.

### 10.1 Principles

```text
P-a  Feed tab has two internal views:
       1. Daily Feed: Layer 2 selected cards, the actual "read today" surface.
       2. Candidate Pool: the raw Layer 1/L2 candidate universe (Potential,
          high_potential, and edge_watch), including items not selected for Daily
          Feed.
     Daily Feed uses featured cards + compact list. Candidate Pool uses a denser
     table/list with filters. L2 deepdive results appear inside each candidate's
     card/drawer when available, not as a separate top-level product surface.
P-b  Four concepts stay visually SEPARATE on every card (section 8): Level badge
     (rule), Priority chip (LLM), Feed bucket/display decision (Layer 2), Status
     (you). Never one blended score.
P-c  Card = scannable; Drawer = full evidence. One click from any item to the
     deterministic evidence that reconstructs "why in pool".
P-d  L0 imperfection is visible and correctable in the UI; corrections feed the
     proposal / feedback loop.
```

### 10.2 Navigation: four peer top-level entries (in nav order)

```text
1. Explore / Chat (primary)   POST /api/chat   (local sessions)
   Claude.ai-style: left sidebar of saved sessions (new chat, history, rename,
   delete); main streaming chat pane. Free-form agent over ALL collected data (not
   only the pool); read-only tools by default, propose-only for rule/prompt edits.
   V1 can expose the same local API / CLI for external callers. Authenticated
   hosted HTTP access is V2.

2. Feed                  GET /api/feed + GET /api/candidates
   internal tabs:
     Daily Feed
       Layer 2 selected cards only. `today_focus` renders as rich cards with
       DeepSeek/Kimi analysis + key evidence inline. `secondary` renders as a
       compact ranked list. `backlog` is available behind filter; `suppress` is
       hidden by default. Ranked by LLM Priority within buckets.
     Candidate Pool
       Full candidate universe from Layer 1/L2: potential, high_potential, and
       edge_watch. This is the transparent pipeline output, not L2 editorial
       selection. Dense list/table with filters by level/source/reason/status.
   card/drawer: name, canonical entity, Level badge, Feed bucket, LLM Priority
         chip, one-line thesis, evidence chips ("GH +1290/d", "HN front page 142",
         "npm 70k/d"), source-family icons, Human Status, and any L2 study/deepdive
         report attached to that candidate.
   actions: open source, deep-dive in chat, mark noise, promote, watch
         -> write local human_status / feedback_events.
   -> Project Drawer  GET /api/entity/{id}: per-source LEVEL-VOTE table with raw
      numbers + source links (the evidence), full DeepSeek analysis, velocity-over-
      time, alias pack + merge confidence (L0 visible), evidence_rows, "deep-dive
      with chatbot" (carries entity context).

3. Sources               the existing source-native dashboard (each source's own
   facts/ranks/links). A first-class peer entry for raw inspection / transparency.

4. Settings              local config + governance:
   - monitored universe (config.json: sources, queries, seed accounts)
   - rules / prompts: current version, chat-proposed diffs, run benchmark replay,
     approve -> new version (section 7.3)
   - LLM provider / model choice (section 5)
   - source health / run settings / local API key status
```

### 10.3 Decided UI choices

```text
F1  Frontend = small SPA (React + Vite).
F2  Feed top-level tab contains two internal tabs:
      Daily Feed = Layer 2 selected cards, not every Potential. `today_focus`
      renders as rich cards; `secondary` renders as a compact ranked list;
      `backlog` is filterable; `suppress` is hidden by default.
      Candidate Pool = all pipeline/L2 candidates (potential, high_potential,
      edge_watch), including candidates not selected for Daily Feed.
    Ranked by LLM Priority within buckets; human_status are filters/markers, not
    the frame.
F3  Nav order = Explore/Chat | Feed | Sources | Settings, four peer entries.
    Chat is the PRIMARY entry (Claude.ai-style with session management); Sources
    (the raw dashboard) is a first-class peer; Settings is the fourth.
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
It is NOT the definition of success. Layer 1 success is sane Potential clusters
on today's unseen live data. Layer 2 success is an actually useful Feed: a small
set of high-priority cards to read now, plus backlog/suppressed candidates that
remain inspectable.
```

## 12. Build Order

```text
Step 1  Layer 0 Stage A (union-find entity resolution) + Layer 1 (rules.json,
        source-specific level votes, verified_cross_source rules, evidence_rows).
        Pure code, no LLM. Run on the current ~15k live items.
        Includes edge_watch_candidates and backfill_jobs generation. Includes the
        deterministic part of X: link-based tweet -> entity mapping and
        entity_mentions aggregates. Bare-name mention matching waits for Stage B.
        Inspect: does the pool look sane on TODAY's data, do source-specific rules
        survive split entities, and does verified_cross_source avoid fuzzy merges?
        Run benchmark replay as a regression check.

Step 2  X source classifier (pipeline LLM). Add llm_provider.py with DeepSeek,
        versioned x_social prompt, x_tier output with cited_tweet_ids, and
        x_social evidence rows. Rerun Layer 1 so x_tier participates in the same
        watch / potential / high_potential system.

Step 3  Precise backfill for the shortlist. GitHub stargazers/repo metadata,
        npm daily downloads, and similar source APIs run only on backfill_jobs.
        Backfill writes evidence_rows and reruns affected deterministic rules.

Step 4  Layer 2 feed + bounded deepdive. Add Tier 2a feed dedupe agent,
        Tier 2b bounded deepdive agent, daily_feed_candidate_refs, card_json,
        display_decision/buckets, lightweight Potential+ analysis, and bounded
        Kimi deepdive for high_potential / top potential / selected edge_watch.
        Output decides which cards appear in today's Feed; Potential/high_potential
        are eligibility signals, not automatic visible placement.

Step 5  React web app shell + API surfaces:
        Explore / Chat, Feed, Sources, Settings. Sources can initially reuse the
        existing source rows/export contract. Feed contains internal tabs:
        Daily Feed (L2 selected cards) and Candidate Pool (all Potential/High/
        edge_watch candidates). Decision APIs are /api/feed, /api/candidates,
        /api/entity/{id}, /api/evidence, /api/config, /api/run.

Step 6  Layer 3 read-only explore agent (2c) on BOTH surfaces at once:
        /api/chat + Explore tab AND the CLI entrypoint `pipeline/agent.py`
        (external callable, read-only). Tools over ALL collected data, not only
        the pool. CLI ships here, not later (user: external call can be early).

Step 7  Layer 3 editing tools (propose -> approve -> version). Optional thin MCP
        wrapper around the CLI only if a richer protocol is wanted.

Step 8 / Plan G  Scheduler and daily automation. Add cron/job runner only after the
        manual daily pipeline is stable. It should call the same idempotent stages,
        reuse the same caches, and surface failures in Settings/source health.
```

Each step is independently shippable and verifiable.

## 13. Open Questions

```text
1. RESOLVED: Potential pool has NO hard cap in V1. Source-specific thresholds
   keep the pool sane; the UI may group/filter, but rules should not hide valid
   Potential candidates just because a daily quota is full.
2. RESOLVED: Layer 2 / DeepSeek analysis runs for level >= potential for now.
   No fixed Top 5 / 10 / 20 cap. Cost is controlled by thresholds,
   dedupe-by-(entity, day), cache, and later a soft budget if needed.
3. Stage B auto-approve: keep manual-only in v1, or auto-approve above a
   confidence threshold?
5. Cross-source 48h windows: which clock — event_at (as_of_safe) only, or also
   snapshot-only signals?
6. x_social LLM prompt/rubric: architecture is decided as a pipeline source
   classifier; still need exact prompt examples for distinct
   people, seed-account credibility, recommendation/usage/emotion strength, and
   how to emit cross_source_notes separately from the X-only tier.

Temporal model (section 4.7), RESOLVED:
P1  windows = 24h (velocity) + 7d (trend), 30d display-only; acceleration is
    self-relative (K ~ 2). No reliance on prior local snapshots.
P2  github velocity/acceleration: free B-class fields first (sparkline, RepoFOMO
    windows, Trending period_stars); stargazers starred_at/forks backfill for the
    shortlist, off-board movers, and externally verified repo/domain mentions.
P3  cheap free signals decide the backfill shortlist (high recall).
P4  cadence = one full run per day.
P5  RESOLVED: backfill is precise补数 for shortlist entities inside Layer 1, not
    a historical replay and not an agent judgment. It writes evidence_rows and
    then deterministic rules rerun.
P6  RESOLVED: edge_watch is a near-miss review queue, not a fourth level. Two
    verified weak source-family signals within 48h are Potential, not edge_watch.
P7  RESOLVED: Potential/high_potential are candidate eligibility levels, not Feed
    visibility promises. Layer 2 decides card generation, priority, bucket, and
    which candidates are shown in today's Feed.
P8  RESOLVED: design every stage for once-per-day runs now, but defer cron/job
    automation to Plan G. Implement run_id, idempotency, api_cache, and llm_cache
    before scheduled daily automation.

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
Q-c  RESOLVED: source-specific max levels are allowed. GitHub, precise GitHub
     backfill, npm downloads, and strict HN can reach high alone. PH and HF usually
     top out at potential unless native metrics are extreme and exact; otherwise
     high requires verified corroboration. X can reach potential from credible
     linked mentions; high requires a larger linked-author burst or verified
     corroboration. Fuzzy/name-only cross-source never creates level.
```

## 14. Hosting and Multi-Tenancy (V2, Deferred)

Decision: hosted/multi-account is not V1. It moves to V2.

```text
V1  Local/internal tool.
    - SQLite/local storage.
    - Local config and local Settings.
    - No login, no invite system, no accounts table, no sessions table.
    - No per-account quota/metering.
    - One daily/local run, or manual run, against one local dataset.
    - API keys stay local in .env/config and are not exposed in the UI.

V2  Hosted product, if this becomes worth productizing.
    - Hosted web app.
    - Invite-only auth.
    - Postgres.
    - Shared global radar data plus per-account overlays.
    - Server-side secret management.
    - Per-account chat/state/feedback/saved views.
    - Quotas/rate limits for interactive L3 usage.
```

If V2 happens, use the same cheap multi-tenancy split:

```text
GLOBAL (one copy, daily pipeline writes, all accounts read):
  all radar data + analysis + default rules/prompts.
  The expensive work (collection, entity resolution, Potential, L2 feed) runs
  once per day server-side. Cost is independent of user count.

PER-ACCOUNT (keyed by user_id):
  auth/session, chat history, human_status, feedback, saved views, usage quota,
  and optional user-specific rules/prompts. This is a thin overlay on the global
  radar.
```

V2 cost model:

```text
- L0 / L1 / L2 pipeline + feed: GLOBAL, once/day. Constant cost.
- L3 chat: PER-USER, interactive. Cost = active users x usage.
  -> per-account usage_quota / rate limiting becomes a hard requirement.
- System holds the API keys (DeepSeek / Kimi / GitHub / etc.) server-side; users do not
  bring their own keys by default. Quotas, not per-user keys, control spend.
```

V2 stack changes from V1:

```text
- DB:        SQLite -> Postgres.
- Backend:   local http.server/simple API -> a real web framework (FastAPI/Flask)
             + ASGI server, auth middleware, per-account session + quota.
- Pipeline:  local/manual run -> a server-side scheduled job (one global daily run)
             + a worker; users read the latest completed run_id.
- External:  the Layer 3 agent is callable over an AUTHENTICATED HTTP API.
- Secrets:   local .env/config -> server-side secret management.
```

What does not change if/when V2 hosting happens:

```text
The entire deterministic core (L0 Stage A, L1, momentum, section 4.7 temporal
model, sparkline sourcing, rules.json structure), the metric design (4.5/4.7),
the benchmark calibration, the provider abstraction / model-swap, and the 0/1/2/3
layering are all unaffected. They were already global batch computation.
```
