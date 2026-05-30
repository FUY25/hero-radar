# Hermes Agent / OpenClaw Benchmark Notes

Last explored: 2026-05-30.

Formal T-grid observation table:
`docs/benchmark-hermes-openclaw-observation-table.md`.

This benchmark exists to answer one practical question:

```text
Given the source data Hero Radar already collects or can cheaply backfill,
what should have triggered a high-recall "closer look" around the rise of
Hermes Agent and OpenClaw?
```

This is not a prediction ledger and not a final scoring formula. The goal is to
identify source-level facts and trigger rules that would put similar future
projects into a review queue before they are obvious.

## Scope

Included targets:

```text
Hermes Agent:
  canonical GitHub repo: NousResearch/hermes-agent
  acceleration T0: 2026-03-11T13:51:05Z
  velocity confirm point: 2026-04-06T13:29:32Z

OpenClaw:
  canonical GitHub repo: openclaw/openclaw
  required early alias: clawdbot/clawdbot / clawdbot / ClawdBot
  acceleration and velocity T0: 2026-01-26T10:32:47Z
  corroboration window: 2026-01-25 to 2026-01-27
```

Excluded from this first benchmark pass:

```text
Claude Code
X / Twitter
Reddit
Apify actors
final aggregate score
```

Included source families:

```text
GitHub Search
GitHub Trending
Trending Repos
RepoFOMO
HN Algolia
HN Firebase, where current snapshots exist
Product Hunt
Hugging Face
npm
PyPI
OSSInsight, only if usable
```

## Interpretation Rules

Use these labels when evaluating source evidence:

```text
early_trigger:
  Signal appears close enough to T0 that it should put the entity into closer look.

confirm_signal:
  Signal appears after takeoff and confirms momentum, but is likely too late alone.

ecosystem_echo:
  Derivative repos, packages, datasets, spaces, plugins, tutorials, or clones.

late_or_not_useful:
  Signal appears materially after T0 or does not appear for this sample.
```

Use these labels for historical safety:

```text
as_of_safe:
  The historical event time is reliable, for example GitHub starred_at, npm
  publish/download date, HN story created_at, HF createdAt, PH featuredAt.

partial_as_of:
  The event time is reliable, but a displayed count may be current or later
  than the benchmark time. Examples: PH votes/comments, HN points/comments.

snapshot_only:
  The field is only available when we collected it or when a third-party board
  exposed it. Examples: Trending Repos source_score, freshness_bonus, sparkline.
```

## Benchmark Time Grid

Use this grid for each primary T0:

```text
T-7d
T-3d
T-24h
T-12h
T-6h
T0
T+6h
T+24h
T+3d
T+7d
```

Not every source has hour-level precision. npm downloads are daily. Product Hunt
launches are timestamped, but votes/comments need as-of caution. Hugging Face
createdAt is timestamped. Trending Repos and RepoFOMO are source snapshots only
unless collected at the time.

## Historical Backfill Method

The benchmark should not depend on local SQLite having existed at T0. Local
SQLite is useful for understanding the dashboard fields and forward snapshots,
but historical benchmark evidence should come from the same APIs or source
families that are already wired:

```text
GitHub REST stargazers with starred_at -> star history / velocity / acceleration
HN Algolia search_by_date -> historical story events
Product Hunt GraphQL postedAfter / postedBefore -> launch history
Hugging Face Hub API search -> model/dataset/space createdAt
npm registry packument + downloads API -> publish bursts and daily downloads
PyPI JSON -> release upload timestamps
Trending Repos / RepoFOMO direct current endpoints -> source-native forward snapshots only
```

The current local SQLite database does not contain the benchmark T0 timestamps
as historical observations. Local target coverage starts much later:

```text
github_search target rows: first local target row 2026-05-28T18:01:15Z
github_trending target rows: first local target row 2026-05-28T18:11:24Z
github_movers target rows: first local target row 2026-05-29T07:43:23Z
```

Local SQLite also has no row proving `clawdbot/clawdbot` as the historical
pre-rename identity. The alias relationship comes from read-only live GitHub
redirect / repository-id checks and cross-source historical API records such as
npm, HN, PH, and HF. Therefore, this document separates:

```text
1. local snapshot evidence for current dashboard fields;
2. direct historical backfill evidence from public APIs;
3. prior milestone-derived derivative peaks supplied by the earlier study.
```

For Trending Repos and RepoFOMO specifically, the wired adapters fetch current
source pages: `https://trending-repos.com/` and `https://repofomo.com/data.js`.
Those expose useful fields such as `source_score`, `starsVelocity`,
`forksVelocity`, `freshnessBonus`, `sparkline`, `stars_7d`, `stars_30d`, and
`new_forks`, but there is no historical `as_of` parameter in the current
adapter. For this benchmark, their current direct-source rows explain what the
dashboard can use going forward; they are not treated as historical proof for
the January/March T0 windows.

## Hermes Agent

### Anchor

| Marker | Timestamp UTC | Meaning | Star band |
|---|---:|---|---:|
| T0 | 2026-03-11T13:51:05Z | second-derivative / acceleration peak | 3.4k-5.5k, midpoint about 4.45k |
| Confirm point | 2026-04-06T13:29:32Z | first-derivative / velocity peak | 23.3k-37.7k, midpoint about 30.5k |

The useful detection point is T0. Waiting until the 2026-04-06 velocity peak is
likely too late for the intended closer-look workflow.

### Source Evidence

| Source | Window | Evidence | Label | Historical safety | Notes |
|---|---|---|---|---|---|
| GitHub repo metadata | current API check | Repo id `1024554267`; created 2025-07-22T22:22:28Z; current topics include `hermes-agent`, `clawdbot`, `moltbot`, and `openclaw`. | discovery / alias enrichment | current snapshot | Current topics are not historical facts, but they are useful for alias expansion. |
| GitHub stars | T0 | Prior milestone analysis found acceleration peak at 2026-03-11T13:51:05Z, about 3.4k-5.5k stars. Direct stargazer API page probes: page 34 spans 2026-03-10T14:49:51Z..18:32:11Z; page 45 spans 2026-03-11T11:49:29Z..13:08:38Z; page 55 spans 2026-03-12T07:28:09Z..09:22:11Z. | early_trigger | as_of_safe with stargazer caveat | With 100 stargazers per page, page 45 is roughly current-stargazer positions 4,401-4,500, validating the earlier T0 star band. GitHub stargazers can be backfilled with `starred_at`; unstarred/deleted history is missing. |
| GitHub stars | Confirm point | Direct stargazer API page probe: page 267 spans 2026-04-06T12:35:27Z..13:34:35Z. | confirm_signal | as_of_safe with stargazer caveat | Page 267 is roughly positions 26,601-26,700, validating the later velocity peak region as already much larger than the T0 region. |
| HN Algolia | 2026-03-04..2026-03-18 | Query `hermes-agent` returned 4 stories. Notable rows: 2026-03-05 `NousResearch/hermes-agent: The agent that grows with you`, 2026-03-09 `Hermes Agent`, 2026-03-11 `Hermes Agent: The self-improving AI agent`. | early_trigger, weak | story time as_of_safe; points/comments partial_as_of | Weak discussion count, but it corroborates GitHub acceleration on the same day. |
| HN Algolia | 2026-03-30..2026-04-13 | Query `hermes-agent` returned about 10-11 stories around the later velocity peak, depending on exact query/crawl. Examples include guide/tutorial/helper/memory/multi-agent posts. | confirm_signal | story time as_of_safe; points/comments partial_as_of | Useful confirmation, but not the earliest trigger. |
| Product Hunt | 2026-03-04..2026-03-18 and 2026-03-30..2026-04-13 | No matching Hermes / Nous rows in the explored PH result windows. | late_or_not_useful | n/a | Do not require PH for Hermes-like detection. |
| Hugging Face exact repo | current API check | Authless public HF model/dataset/space endpoints for exact `NousResearch/hermes-agent` did not expose a usable public resource in the explored path. | late_or_not_useful | n/a | Exact canonical repo was not enough for HF; derivative terms matter more. |
| Hugging Face derivative datasets/spaces | 2026-03-28 onward | `kazukaraya12/Hermes-agent` appeared 2026-03-28T02:10:56Z but had timestamp inconsistency and no engagement. `lambda/hermes-agent-reasoning-traces` created 2026-03-30T15:48:57Z with 346 likes / 3295 downloads at observation time. `DJLougen/hermes-agent-traces-filtered` created 2026-04-04T14:57:25Z. | confirm_signal / ecosystem_echo | createdAt as_of_safe; likes/downloads snapshot/current | HF becomes useful after the GitHub acceleration, especially near the velocity confirmation period. |
| npm exact package | T0 and confirm windows | `hermes-agent` npm package first published 2026-05-25T13:05:16Z. Downloads were 0 in 2026-03-30..2026-04-13 range. | late_or_not_useful | publish/download dates as_of_safe | Exact package is not useful for Hermes T0. |
| npm indirect package | 2026-03-13 onward | `hermes-paperclip-adapter` first release around 2026-03-13T00:23:04Z; npm search showed high current weekly/monthly downloads. | early-ish indirect / high-recall only | publish time as_of_safe; current downloads snapshot/current | This is about 1.4 days after T0 and is not the canonical package, but it is useful for high recall because it references Hermes Agent. |
| PyPI | T0 and confirm windows | `hermes-agent` first release appeared 2026-05-14T18:49:29Z. | late_or_not_useful | upload time as_of_safe | Not useful for Hermes T0. |
| Trending Repos | 2026-05-30 direct current source check | Daily rank 9, weekly rank 9, monthly rank 4. Daily row: language rank 5, `source_score=873.03`, `stars_velocity=1290.0`, `forks_velocity=330.0`, `freshness_bonus=0.2913`, sparkline `[1291,1231,1558,1449,1560,1364,1216]`. | confirm_signal for current runs | snapshot_only | Strong board-native mover evidence going forward, but not historical evidence for 2026-03-11 unless archived. |
| RepoFOMO | 2026-05-30 direct current source check | FomoRank 10, `stars_7d=910`, `stars_30d=5673`, `stars_60d=23204`, `new_forks=1227`, `star_age_days=38`. | confirm_signal for current runs | snapshot_only | Good sustained-growth evidence going forward. |

### Hermes Takeaway

Hermes is a GitHub-acceleration-first case. The closer-look mechanism should be
able to fire on GitHub second-derivative movement plus weak HN corroboration,
without waiting for Product Hunt, exact npm/PyPI packages, or HF. The one npm
exception is indirect package evidence such as `hermes-paperclip-adapter`, which
is useful for high recall but should be weighted as indirect. HF becomes
meaningful later as an ecosystem echo. The velocity peak is useful for
validation, not early detection.

## OpenClaw

### Anchor

| Marker | Timestamp UTC | Meaning | Star band |
|---|---:|---|---:|
| T0 | 2026-01-26T10:32:47Z | first-derivative and second-derivative peak | 23.3k-37.7k, midpoint about 30.5k |
| Corroboration window | 2026-01-25..2026-01-27 | non-GitHub sources light up around the same time | n/a |

OpenClaw is an alias-chain and cross-source resonance case. The early wave uses
`clawdbot` heavily; the `openclaw` name appears later in several sources.

### Alias Pack

Minimal v1 aliases:

```text
canonical:
  openclaw/openclaw

strong aliases:
  clawdbot/clawdbot
  clawdbot
  ClawdBot
  openclaw
  OpenClaw

lower-confidence related aliases:
  moltbot
  Moltbot

package aliases:
  npm: clawdbot
  npm: openclaw
  npm: @clawdbot/*
  npm: @openclaw/*

Product Hunt:
  products/clawdbot-2/launches/openclaw
```

Read-only GitHub-side exploration found that `clawdbot/clawdbot` redirects to
canonical `openclaw/openclaw`, with GitHub repository id `1103012935`. For this
benchmark, GitHub repo id is the safest primary entity key.

### Source Evidence

| Source | Window | Evidence | Label | Historical safety | Notes |
|---|---|---|---|---|---|
| GitHub repo metadata | current API check | Repo id `1103012935`; created 2025-11-24T10:16:47Z. Live request to `clawdbot/clawdbot` redirects to `https://api.github.com/repositories/1103012935`, canonical `openclaw/openclaw`. | alias proof | current redirect + stable repo id | This is the strongest GitHub-side proof that the old repo identity should resolve into the OpenClaw entity. |
| GitHub stars | T0 | Prior milestone analysis found T0 at 2026-01-26T10:32:47Z, about 23.3k-37.7k stars. Direct stargazer API page probes: page 233 spans 2026-01-26T04:42:28Z..04:49:38Z; page 305 spans 2026-01-26T09:59:33Z..10:05:30Z; page 310 spans 2026-01-26T10:28:07Z..10:33:55Z; page 377 spans 2026-01-26T16:11:08Z..16:17:06Z. | early_trigger | as_of_safe with stargazer caveat | With 100 stargazers per page, page 310 is roughly current-stargazer positions 30,901-31,000 and directly validates the earlier T0 estimate around 30.5k. |
| npm `clawdbot` | 2026-01-24..2026-01-31 | Daily downloads: 2026-01-24 16,242; 01-25 70,768; 01-26 106,024; 01-27 139,824; 01-28 114,796; 01-29 104,606; 01-30 37,710; 01-31 16,015. | early_trigger, strong | as_of_safe | This is one of the strongest non-GitHub early signals. |
| npm `clawdbot` releases | 2026-01-24..2026-01-25 | Release burst includes `2026.1.23`, `2026.1.23-1`, `2026.1.24`, `2026.1.24-1`, `2026.1.24-2`, `2026.1.24-3`, from about 2026-01-24T12:41:17Z through 2026-01-25T15:21:46Z. | early_trigger, strong | publish time as_of_safe | Release cadence acceleration precedes T0 by roughly 19-46 hours. |
| npm `@clawdbot/*` scoped packages | 2026-01-25 | `@clawdbot/matrix`, `@clawdbot/msteams`, `@clawdbot/voice-call`, and `@clawdbot/zalo` all had synchronized releases around 2026-01-25T13:17:53Z..13:18:18Z. | early_trigger, strong | publish time as_of_safe | Multiple exact-scope packages in minutes is a high-recall package-family trigger. |
| npm `openclaw` | 2026-01-29..2026-01-31 | Package first published 2026-01-29T11:08:12Z. Downloads: 01-29 70; 01-30 104,954; 01-31 156,674. | confirm_signal | as_of_safe | New-name signal appears about 3 days after T0. Alias handling is required. |
| HN Algolia `clawdbot` / `ClawdBot` | 2026-01-19..2026-02-02 | Query runs returned about 100+ stories under the old alias. In the tight 2026-01-25..2026-01-27 window, mixed-case alias queries returned about 42 stories. | early_trigger, strong | story time as_of_safe; points/comments partial_as_of | Strong discussion signal under old alias. Counts vary by exact query/casing/tokenization, but all runs agree the old alias dominates the tight window. |
| HN Algolia `openclaw` / `OpenClaw` | 2026-01-19..2026-02-02 | Broad-window query runs returned about 68-91 stories, but the tight 2026-01-25..2026-01-27 window returned 0 for the new name. | confirm_signal after rename | story time as_of_safe; points/comments partial_as_of | If only `openclaw` is queried, early HN signal is missed. |
| Product Hunt | 2026-01-27T08:01:00Z | `OpenClaw` launch under `products/clawdbot-2/launches/openclaw`; PH API returned daily rank 2, weekly rank 3, votes 835, comments 53 in current query result. Page exploration also showed strong launch-page traction such as day rank 2 / week rank 3. | early_trigger / confirm_signal | featuredAt and ranks mostly as_of_safe; votes/comments partial_as_of | Very strong corroboration and also alias-chain proof. |
| Product Hunt follow-ons | 2026-02-25..2026-02-27 | OpenClaw-derived launches such as `KiloClaw` and `MaxClaw by MiniMax` appear later. | ecosystem_echo | launch time mostly as_of_safe; engagement partial_as_of | Useful for ecosystem tracking, outside the T0 window. |
| Hugging Face model | 2026-01-26T05:29:27Z | `KALLLA/clawdbot` model created on the same UTC day before T0. | early_trigger | createdAt as_of_safe; likes/downloads snapshot/current | Strong ecosystem echo around T0, even with low engagement. |
| Hugging Face spaces | 2026-01-26 | `mbrycey/clawdbot-agent-2` created 2026-01-26T08:18:13Z; `acpr123/clawdbot` created 2026-01-26T08:43:05Z with nonzero likes at observation time. | early_trigger / ecosystem_echo | createdAt as_of_safe | Multiple same-day HF spaces strengthen cross-source resonance. |
| Hugging Face `openclaw` wave | 2026-01-30 onward | First collected `openclaw` spaces appear around 2026-01-30, with models/datasets following from 2026-02-01 onward. | confirm_signal / ecosystem_echo | createdAt as_of_safe; likes/downloads snapshot/current | New-name HF wave is later than the `clawdbot` T0 signal. |
| PyPI `openclaw` | 2026-02-24T14:27:19Z | First release appears nearly a month after T0. | late_or_not_useful for T0 | upload time as_of_safe | May be useful for later ecosystem tracking, not early detection. |
| PyPI `clawdbot` | 2026-02-27T04:24:25Z | First release appears after the main wave. | late_or_not_useful for T0 | upload time as_of_safe | Not an early trigger for this sample. |
| Trending Repos | 2026-05-30 direct current source check | Daily rank 86 and monthly rank 63. Daily row: language rank 26, `source_score=150.93`, `stars_velocity=213.5`, `forks_velocity=76.0`, `freshness_bonus=0.3357`, sparkline `[187,228,213,246,220,212,215]`. | confirm_signal for current runs | snapshot_only | Even modest global rank should trigger review when language rank and fork velocity are strong, but this is current source state rather than T0 history. |
| RepoFOMO | 2026-05-30 direct current source check | FomoRank 136, `stars_7d=226`, `stars_30d=1735`, `stars_60d=6816`, `new_forks=546`, `star_age_days=26`. | confirm_signal for current runs | snapshot_only | Strong enough for closer look despite not being top-ranked globally. |

### OpenClaw Takeaway

OpenClaw would be misread unless aliases are first-class. The early wave is
visible through `clawdbot` npm downloads, HN discussion, HF resources, and GitHub
star acceleration. The `openclaw` package and openclaw HN discussion appear
later. A high-recall system should treat alias discovery and redirect resolution
as part of the signal, not post-processing.

## Source Capability Matrix

| Source / field | Historical capability | Benchmark use |
|---|---|---|
| GitHub stargazers `starred_at` | Backfillable for current stargazers | Reconstruct star curves, velocity, acceleration, and 7d/30d windows. |
| GitHub forks `created_at` | Partly backfillable for surviving forks | Reconstruct fork windows and developer-copy signal. |
| GitHub repo metadata | Current snapshot unless repeatedly stored | Discovery/enrichment, not historical state by itself. |
| GitHub Search rank | Snapshot-only | Discovery. Do not treat historical rank as reproducible. |
| GitHub Trending rank / `period_stars` | Snapshot-only unless archived | Positive-only signal. Absence should not count against a candidate. |
| Trending Repos `source_score` / velocity / freshness / sparkline | Source-native snapshot-only from the current page | Use going forward; approximate historical analogs from GitHub backfill if needed, but do not claim exact reconstruction of `source_score`, `freshnessBonus`, or `sparkline`. |
| RepoFOMO rank / windows | Source-native snapshot-only from `data.js` | Use going forward; approximate star/fork windows from GitHub backfill. |
| HN Algolia story created_at | Backfillable | Good historical event signal; points/comments need caution. |
| Product Hunt launch `featuredAt` | Backfillable through PH API | Strong launch corroboration; votes/comments need caution. |
| Hugging Face createdAt / lastModified | Backfillable through HF API search | Ecosystem echo and derivative-resource timing. |
| npm publish time / daily downloads | Backfillable | Strong package-adoption signal, especially for OpenClaw. |
| npm search rank | Snapshot-only | Useful current discovery, not historical rank. |
| PyPI release upload time | Backfillable | Package ecosystem timing; not early for these two samples. |
| PyPI downloads | Backfillable via public BigQuery, not simple JSON | Future enhancement if Python ecosystem becomes important. |
| OSSInsight | Currently unreliable from this machine | Do not block on it. |

## High-Recall Closer-Look Rules

Do not compute a final aggregate score yet. Use trigger families and keep the
reason visible.

An entity should enter closer look if any strong trigger fires:

```text
GitHub acceleration trigger:
  star acceleration is unusually positive, or backfilled 24h/7d star velocity
  is in the high tail for the tracked candidate pool.

Mover-board trigger:
  Trending Repos daily rank <= 100, or monthly rank <= 75, or language_rank <= 30,
  especially with stars_velocity >= 150 or forks_velocity >= 50.

RepoFOMO trigger:
  stars_7d >= 200, stars_30d >= 1000, stars_60d >= 5000, or new_forks >= 100.

Package trigger:
  npm daily downloads exceed 10k and are rising quickly, or a package/plugin
  family appears around the same entity within a short window.

HN trigger:
  at least 3 matching HN stories in 7 days, or any meaningful HN story on the
  same day as a GitHub acceleration event.

Product Hunt trigger:
  launch appears with daily_rank <= 5 or clear launch metadata matching the entity.

Hugging Face trigger:
  at least 2 matching models/datasets/spaces appear within 48 hours, or one
  canonical resource appears on the same day as another strong source signal.

Cross-source trigger:
  any two weak signals from different source families appear within 48 hours.
```

These rules intentionally favor recall. Entering closer look is cheap; missing a
Hermes/OpenClaw-like event is expensive.

## Entity Resolution Requirements

For v1, use a lightweight alias system before any scoring or closer-look logic:

```text
1. Resolve GitHub redirects and store GitHub repository id when possible.
2. Normalize GitHub repository URLs found in npm, PH, HF, and HN records.
3. Keep known aliases as source-specific handles, not just string synonyms.
4. Attach confidence to aliases:
   - strong: redirect, same repo id, package repository URL, PH product path
   - medium: exact package/name/slug match
   - weak: related term appearing in title or article URL only
5. Evaluate triggers over the whole alias pack.
```

The OpenClaw benchmark should fail if `clawdbot` and `openclaw` are treated as
separate unrelated entities.

## Recommended Next Work

1. Turn the manual table in `docs/benchmark-hermes-openclaw-observation-table.md`
   into a read-only benchmark observation extractor for these two entities.
2. Emit rows in this shape:

```text
project
canonical_entity
alias
source
event_at
relative_to_T0
metric_name
metric_value
signal_label
historical_safety
note
raw_url_or_ref
```

3. Generate a Markdown table from that extractor.
4. Add a closer-look explanation layer that lists fired trigger families.
5. Defer final aggregate scoring until the benchmark table is reviewed.

## Benchmark Updates From T-Grid Backtest

The formal table changes the practical standard in four ways:

1. Hermes passes with GitHub acceleration alone as the strong trigger. HN is
   weak corroboration, while PH / HF / exact npm / PyPI should not be required.
2. OpenClaw must pass through alias-aware matching. `clawdbot` and
   `openclaw` must be evaluated as one entity before source triggers run.
3. npm package-family behavior should be a first-class trigger: daily downloads
   above 10k, repeated versions in 48h, or multiple scoped packages in 24h.
4. Trending Repos and RepoFOMO are forward snapshot triggers. They should be
   used going forward, but the historical benchmark should not claim exact
   replay of their ranks, `source_score`, `freshnessBonus`, or sparkline.

## Open Questions

```text
1. Should Moltbot be included as a weak OpenClaw alias by default, or only after
   a source record also mentions clawdbot/openclaw?

2. Should HN points/comments be stored as current observed counts or excluded
   from historical benchmark metrics because of as-of ambiguity?

3. Should Trending Repos / RepoFOMO be used only as forward-looking daily
   signals, or should we implement local approximations from GitHub star/fork
   curves for historical benchmarks?
```
