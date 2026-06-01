# Decision Rules v2 — Unified Floor + Momentum Model

Status: finalized mechanism, pending threshold calibration. This document is the
source of truth for the pre-Layer-2 deterministic decision rules. It supersedes
the per-source absolute thresholds in `pipeline/rules.json` v1 (`rules-v1`).
Engineer implements against this. Numbers marked `[calibrate]` are starting
points to tune against the real candidate-pool distribution; the *shapes* and
*design decisions* are locked.

Cross-references:
- Current code: `pipeline/decision/rules.py`, `pipeline/rules.json`
- Benchmark: `docs/benchmark-hermes-openclaw.md`
- Counts below: measured on the 2026-05-30 snapshot of `data/hero_radar.sqlite`.

## 1. Why this rewrite

v1 shape was `level = f(absolute_amount)`, with velocity/acceleration only a
secondary switch on the middle tier. Two failures:

- Watch tier was a pure absolute floor → big-but-flat projects sat permanently in
  the pool (the "too wide" problem).
- `high_potential` mostly bypassed momentum (RepoFOMO `OR`, npm no-rising) → the
  largest, most obvious projects auto-fired. Those are exactly what a radar does
  not need to surface.

Benchmark target (from `benchmark-hermes-openclaw.md`): Hermes Agent's useful
detection point is **T0 = the acceleration (2nd-derivative) peak at ~4.45k stars**,
26 days *before* the velocity peak at ~30.5k (2026-04-06). We must fire when
something is *speeding up while still small*, not when it is already huge.

**Core principle: absolute magnitude alone is not a signal.** Magnitude is used
exactly once, at the floor (anti-spam: "is this big enough to bother computing
momentum"). Above the floor, every promotion is earned by momentum. There is no
"it's big, so promote" path anywhere in Archetype A — that path only re-imports
noise (e.g. large flat npm utility/MCP packages, react-class libraries) at the
top tier. Detection value comes from *acceleration*, which is what actually caught
OpenClaw/Hermes early; a big-but-flat project is either already-arrived (no radar
value) or a stable utility.

## 2. Two archetypes (the unifying spine)

Every source belongs to exactly one archetype. No source mixes the two.

### Archetype A — Adoption curve (floor + momentum)

Measures a **cumulative adoption count that grows over time** (stars, forks,
downloads). A curve exists → derivatives are meaningful. Model:

```
floor      = absolute minimum, anti-spam only, never promotes by itself
momentum   = scale-free, denominator-STABLE ratio of recent-rate to baseline-rate
level      = momentum band, gated by floor       (tiers are pure-momentum)
```

Two rules make momentum trustworthy:
- **Scale-free**: a ratio cancels project size, so a giant established repo does
  not auto-trip.
- **Denominator-stable**: the baseline must be a window-average (e.g. 30-day or
  7-day mean), never a single recent point. A raw "recent point / recent point"
  ratio explodes near takeoff (tiny denominator) and breaks the ladder — see §4.2.

### Archetype B — Event / attention (salience + recency)

Measures a **discrete event's significance** (an HN discussion, a PH launch, a
KOL tweet). No curve to differentiate; the right model is salience + recency, not
momentum. **Sources: HN, Product Hunt, X.** Do not add momentum to Archetype B
"for consistency" — momentum needs a *scale* to normalize, and significance here
is intrinsic, not scaled. X is the extreme (LLM KOL-semantic tiering); HN and PH
are the same archetype with a numeric salience proxy (points, rank).

## 3. Tier ladder rules (Archetype A) — locked

1. **Tiers are pure-momentum bands.** Floor gates entry; the band (watch /
   potential / high) is decided only by the momentum value.
2. **Strictly nested.** `high ⊂ potential ⊂ watch` by construction (each tier is a
   higher momentum band, e.g. accel ≥ 3 ⊂ ≥ 2 ⊂ ≥ 1.2). Never give a lower tier a
   gate the higher tier lacks. This guarantees a monotonic funnel
   (watch ≥ potential ≥ high) and was the root cause of the v1 inversion.
3. **No absolute-magnitude break-glass.** Removed entirely from Archetype A.
   Magnitude is already spent at the floor; a second magnitude path only re-adds
   noise at the top. (Archetype B keeps a salience break — §5.1 — which is a
   different thing: event significance, not adoption size.)

Default momentum bands (shared starting points, `[calibrate]`):
`watch = 1.2`, `potential = 2.0`, `high = 3.0` (ratio of recent-rate / baseline).

**`MIN_BASELINE` must live in `rules.json`, not hardcoded.** Every momentum ratio
uses `recent_rate / max(baseline, MIN_BASELINE)`; too small and new/tiny entities
blow the ratio up, too large and it crushes real takeoffs. It needs per-magnitude
values because stars (rate ~1s) and downloads (rate ~1000s) are different scales:

```json
"momentum": {
  "min_baseline_stars_per_day": 1.0,
  "min_baseline_daily_downloads": 100.0
}
```

(Values `[calibrate]`.) RepoFOMO and GitHub backfill use the stars baseline; npm
uses the downloads baseline.

**Distinguish a small baseline from a missing baseline.** `MIN_BASELINE` only
guards against a *present but tiny* baseline. A *missing or zero* baseline is not
acceleration evidence — using `MIN_BASELINE` as a substitute would manufacture a
huge ratio and falsely promote to high. So every Archetype A momentum source obeys:

```
if the longer-window baseline is missing or <= 0:
    above floor -> watch only ; else none   (never potential/high)
```

This is already the RepoFOMO `stars_30d <= 0` fallback (§4.1); it applies
identically to npm `downloads_7d` (§4.3) and GitHub backfill `stars_7d`.

## 4. Acceleration authority vs. velocity corroborator

Acceleration requires clean multi-window data. Only some sources have it. This
splits Archetype A's GitHub sources into two roles, unified by the fact that all
GitHub board sources resolve to the same entity by repo URL (§7), so acceleration
computed on one source promotes the shared entity.

- **Acceleration authorities** (clean multi-window data): run the full
  floor + momentum ladder and can reach potential / high.
  → RepoFOMO, GitHub backfill.
- **Velocity / direction corroborators** (single snapshot or noisy series): cap at
  **watch** on their own; higher tiers are delegated to the entity-level
  acceleration authorities.
  → GitHub Trending (single daily snapshot — but see §4.4, kept at velocity tiers
    by explicit decision), Trending Repos (noisy sparkline).

**Implementation warning — two similarly-named sources, opposite treatment.** Do
not conflate or cap both the same way:

| source id | role | tiers emitted |
|---|---|---|
| `github_trending` | official daily snapshot, velocity tiers (§4.4) | watch / potential / high |
| `github_movers_trending_repos` | third-party board, watch-only corroborator (§4.2) | watch only |

### 4.1 RepoFOMO (`github_movers_repofomo`) — acceleration authority
Richest data (`stars_7d / 30d / 60d`, `new_forks`), no backfill needed.

```
r7  = stars_7d  / 7        # recent daily rate
r30 = stars_30d / 30       # baseline daily rate (window-average, stable)
r60 = stars_60d / 60
accel = r7 / max(r30, MIN_BASELINE)

floor:     stars_7d >= 70      [calibrate]   (anti-spam; nearly non-binding,
                                              accel does the work)
watch:     accel >= 1.2
potential: accel >= 2.0
high:      accel >= 3.0
```

- No `r7 > r30 > r60` extra gate on potential (that caused the v1 inversion).
- `new_forks` is corroboration only, not a promoter (no multi-window fork data to
  compute fork acceleration). Do NOT promote on `new_forks` magnitude.
- Fallback when no baseline (`stars_30d <= 0`): cannot compute accel → cap at
  watch using the floor. Do **not** key this off `star_age_days` (see §6, bug 1).
- Distribution check: among repos above floor, median accel = 0.83, p90 = 1.55 —
  most board repos are *decelerating* on a given day. The accel gate correctly
  surfaces only the rising minority.
- Counts (2026-05-30): v1 fired 306 (249/31/26) → **v2 fires 66 (44/13/9)**, clean
  pyramid.
- Small-but-explosive high is intentional, not a bug: pure momentum means a tiny
  repo (e.g. `stars_7d=80, stars_30d=90` → accel ≈ 3.8) can reach high. This is
  the Hermes-at-4.45k case — early/small is exactly what we want. On the current
  snapshot the smallest `stars_7d` reaching v2 high is 1320, so no fragile highs
  appear yet. **Do not pre-emptively raise the floor and do not re-add a volume
  break-glass.** Instead add a calibration test that flags if high ever contains
  too-small / too-fragile projects; only if that fires, consider a *minimum-
  absolute floor on the high tier* (a floor on high, the opposite of a volume
  break-glass — it never promotes, only restrains).

### 4.2 Trending Repos (`github_movers_trending_repos`) — watch-only corroborator
Has `stars_velocity`, `forks_velocity`, and a 7-point daily `sparkline`.

```
direction = regression_slope(sparkline) / max(mean(sparkline), 1)   # rising?
floor:  stars_velocity >= 150  [calibrate]   (benchmark mover floor)
watch:  floor and direction > 0        (i.e. the week is trending up)
        (forks_velocity >= 50 also qualifies as a strong floor)
potential / high: NOT emitted by this source — delegated to the entity-level
                  acceleration authority (RepoFOMO / backfill) after combine (§7).
```

**Sparkline length is not always 7.** Observed daily rows have lengths 1, 2, 3, 4,
and 7 (distribution on 2026-05-30: `{1:17, 2:7, 3:7, 4:4, 7:65}`). The slope is
undefined below 3 points. A short-sparkline row has a velocity floor but no
direction — if it were admitted to watch it would also be ineligible for the
direction-gated backfill (§4.2 guard 1) and unreachable by family-deduped
cross-source (§8), i.e. permanently stuck at watch. To avoid that dead zone:

```
len(sparkline) < 3 : NOT admitted to the candidate rule at all (visible only in
                     the source tab). It never enters watch.
len(sparkline) >= 3: compute normalized regression slope as direction; admit to
                     watch iff stars_velocity >= 150 (or forks_velocity >= 50)
                     AND direction > 0.
```

Tests must cover short and noisy series, e.g. `[146,221]` (len 2 → excluded) and
`[1374,10,361,...]` (len 7, spiky → slope handles it).

**Mandatory: watch must enqueue a GitHub stargazer backfill** so a repo seen only
on Trending Repos (not on RepoFOMO) can still reach an acceleration authority and
be promoted — otherwise it is stuck at watch forever (this is why §8's
family-deduped cross-source rule cannot rescue it). Enqueue with three guards to
bound API cost:

```
1. only when direction > 0 (rising)         — do not backfill a decaying row
2. skip if the entity is already on RepoFOMO — clean acceleration already exists
3. respect backfill_max_jobs and priority    — rank below confirmed potential
                                               candidates. If over budget this run
                                               the job is simply dropped (matches
                                               the current truncate-at-max
                                               behavior); the next snapshot
                                               re-evaluates and re-enqueues it if
                                               the repo is still on the board and
                                               still rising. Do NOT add a
                                               persistent pending-backfill queue.
```

Why watch-only (this is a finding, not a preference): the sparkline cannot give a
clean 3-tier ladder. Its momentum distribution is **bimodal** — repos either
clearly take off (e.g. `[31,33,37,23,29,190,410]`) or decay (`[807,...,307]`),
with an almost-empty middle (slope/mean: median 0.027, p75 0.083, then jumps to
p90 0.333). Any band forced into that gap inverts the funnel. The raw
`mean(last3)/mean(first4)` ratio is worse — it explodes near takeoff because the
early-window denominator is tiny (Crossta → 6.76). **Use the normalized
regression slope, not a mean-ratio, and only as a binary rising/not-rising
direction.** Acceleration magnitude comes from the clean sibling source.

- Counts (2026-05-30): v1 fired 186 (121/59/6) → **v2 fires 52 (52/0/0)** watch.
  Exact filter: `(stars_velocity >= 150 OR forks_velocity >= 50) AND
  len(sparkline) >= 3 AND regression_slope(sparkline) > 0`, daily period only.
  (The earlier "42" used a stray `len >= 7` gate and was wrong; 31 of 100 daily
  rows have len < 3 and are excluded by the rule above, not by length 7.)

### 4.3 npm (`npm_registry`) — acceleration authority (rising-vs-baseline)
Downloads are dominated by install-base, so absolute magnitude is meaningless
here (large flat utility/MCP packages). The signal is **always** rising vs the
package's own baseline.

```
velocity = daily_downloads
baseline = downloads_7d / 7
rising   = daily_downloads / max(baseline, MIN_BASELINE)   # scale-free spike

# missing-baseline guard (see §3): if there is no real baseline, rising is not
# proven — MIN_BASELINE must NOT be used to manufacture a high ratio.
if downloads_7d is missing or <= 0:
    daily_downloads >= floor  -> watch only ; else none   (never potential/high)

floor:     daily_downloads >= 3000   [calibrate]   (anti-micro-package)
watch:     rising >= 1.3   [calibrate]
potential: rising >= 2.0
high:      rising >= 3.0
```

- `rising` is required at **all** tiers (v1's no-rising high let react/lodash/MCP
  packages sit permanently at high — removed).
- No absolute break-glass: a 500k-downloads flat package is not a signal.
- **v2 core npm rule is `daily_downloads` / `downloads_7d` rising only** (the block
  above). Nothing else ships in the first version.
- **Deferred to a separate task (not in v2 core): package-family burst** —
  multiple scoped packages (`@scope/*`) published within 24h, or repeated versions
  of one package in 48h. The direction is right (it was OpenClaw's strongest early
  npm signal, `@clawdbot/*` synchronized releases), but it is underspecified and
  must not be mixed into v2 core. Its own task must first decide: threshold
  (`>= 2` vs `>= 3`); whether members must share a repo/domain binding; how to
  avoid routine monorepo-release false positives; and whether package publish-time
  is reliably present in the source data. Open it behind an eval, not by default.
- Counts (2026-05-30, **approximate** — see §6 bug 2): v1 fired ~167 (89/35/43) →
  **v2 fires ~25 (19/4/2)**. Flat MCP/utility giants correctly excluded
  (accel ≈ 1).

### 4.4 GitHub Trending (`github_trending`) — velocity tiers (explicit decision)
`stars_today` (`period`=daily `period_stars`) is GitHub-official, the freshest
single-day velocity. Acceleration is NOT computed (single snapshot; fetching
yesterday's value each run is not worth the cost).

```
velocity = stars_today
watch:     stars_today >= 300    [calibrate]
potential: stars_today >= 1000
high:      stars_today >= 3000
```

Decision: github_trending **keeps velocity tiers** (it is the only official source
and `stars_today` is the freshest, cleanest velocity number we have — "+3000 stars
in one day" is a strong signal on its own). It is the one velocity-tier exception
to the "corroborators cap at watch" rule, justified by source quality. If it is
genuinely accelerating it will also promote via RepoFOMO / backfill at the entity
level; if it is a post-peak decel day, its velocity tier still flags it for review,
which is acceptable for the official board.

- Counts (2026-05-30, daily only): **19 (16/3/0)** — matches the prior benchmark
  observation (19/3/0).

### 4.5 GitHub backfill result (`extra_github_signals`) — acceleration authority
After stargazer backfill we have precise `stars_24h` and `stars_7d`.

```
velocity = stars_24h
baseline = stars_7d / 7
accel    = stars_24h / max(baseline, MIN_BASELINE)

floor:     stars_24h >= 300   [calibrate]
watch:     accel >= 1.2
potential: accel >= 2.0
high:      accel >= 3.0
```

Same ladder as RepoFOMO. v1 thresholded each metric independently against the
trending table with no acceleration — fixed here. (No local backfill rows exist
yet to count.)

### 4.6 GitHub Search backfill prefilter — unchanged role
`stars_per_day = stars / age_days` + freshness gates (pushed ≤14d, created ≤180d).
This is a **budget prefilter** deciding who to spend stargazer-backfill on, not a
verdict. It is lifetime-average velocity (coarse); real recent acceleration
arrives after backfill (§4.5). Keep v1 values (`min_stars_per_day = 50`).

### 4.7 Hugging Face (`huggingface`) — echo momentum, corroboration only
Count of derivative resources created in 48h (already a creation-rate, floor = 1).

```
watch:     >= 1 resource in 48h
potential: >= 2 resources in 48h AND the resolved canonical entity has an approved
           GitHub repo link
otherwise: cap at watch
```

Classify HF as **ecosystem-echo corroboration**, not a primary growth curve: it
measures derivative activity around an entity, not the entity's own adoption, and
HF owner/name fragmentation means same-project uploads do not merge. The explicit
GitHub-link condition resolves the prior ambiguity: HF can reach potential, but
only when corroborated by a verified GitHub repo — never on resource count alone.

## 5. Archetype B — finalized (salience + recency, no momentum)

### 5.1 HN (`hn`)
7d window, max points across qualified stories. Salience proxy = points.

```
watch:     max_points >= 30
potential: max_points >= 200      (salience break — `points_breakthrough_potential`)
corroboration: >= 3 qualified stories in 7d (benchmark)
```

Low-trust source: caps at watch alone; the `>= 200` breakthrough is the only
single-source path to potential. This salience break is NOT the magnitude
break-glass removed in §3 — it is event significance, not adoption size. No
momentum. Unchanged from current behavior (no classifier re-run needed).

### 5.2 Product Hunt (`product_hunt`)
One-day launch event. Salience proxy = rank. `watch: daily_rank <= 10`,
`potential: daily_rank <= 5`. No momentum. Unchanged.

### 5.3 X (`x_tweets`) — out of scope this round
Same archetype as HN/PH; salience judged by LLM KOL-semantic tiering, governed by
its own classifier (`x_social_tier`). Not part of the floor+momentum rewrite.
Unchanged (no classifier re-run needed).

## 6. Data-reliability bugs found while calibrating (Engineer must handle)

1. **`star_age_days` (RepoFOMO) is unreliable.** A sample row shows
   `star_age_days = 7` while `stars_60d` is fully populated (contradictory).
   Routing the "new repo" fallback off `star_age_days < 60` misfired on 470/500
   repos and effectively disabled the whole momentum redesign. **Fix: key the
   no-baseline fallback off `stars_30d <= 0`, not `star_age_days`.** (With that,
   the fallback hits only 4/500.)

2. **npm `daily_downloads` is not in the `items` table.** `items` only carries
   `weekly_downloads` / `monthly_downloads` (from `npm_search`); the real rule
   operates on backfilled `npm_registry` daily evidence. The npm counts in §4.3
   are approximated from weekly/monthly and are **not exact** — true values
   require the backfilled daily series.

3. **Sparkline (Trending Repos) is volatile and bimodal.** Use a normalized
   regression slope as a binary rising/not-rising direction, never a
   `mean(recent)/mean(early)` ratio (it explodes near takeoff). See §4.2.

## 7. GitHub entity combination (why corroborators can delegate)

Entity resolution (Stage A union-find) clusters board rows by GitHub repo URL /
repo id. All GitHub board sources for one repo resolve to the **same `entity_id`
→ same `EntityState`**; their evaluators promote the same state in sequence
(`pipeline/decision/rules.py`). Consequence:

- A repo on a corroborator (Trending Repos / github_trending) + an acceleration
  authority (RepoFOMO / backfill) gets its potential/high tier from the authority;
  the corroborator only needs to contribute watch-level velocity + direction.
- A repo only on a corroborator would otherwise stall at watch with no clean
  acceleration to justify higher. **This is closed by the mandatory Trending-Repos
  watch + rising → backfill enqueue (§4.2)**, which routes it to an acceleration
  authority on a later run.

## 8. Meta-rules (unchanged, orthogonal)

- `verified_cross_source`: 2 weak source-families within 48h → potential. An
  aggregation rule across sources, not a per-source curve. Keep.
  - Note: it dedupes by **family**, and RepoFOMO / Trending Repos / GitHub Trending
    all share `family = github`, so they can never combine into "two weak
    families" with each other. This is consistent with v2 (GitHub-internal
    promotion happens via the entity-level acceleration authority, not via
    cross-source), but it is the reason a GitHub-only, Trending-Repos-only rising
    repo would stall at watch — hence the mandatory backfill enqueue in §4.2.

## 9. What is locked vs. calibrated

Locked (mechanism):
- Two-archetype split and per-source archetype assignment.
- Archetype A: floor + pure-momentum tiers; momentum = scale-free AND
  denominator-stable ratio.
- Strictly nested tiers (guaranteed monotonic funnel); **no absolute-magnitude
  break-glass** in Archetype A.
- Acceleration authorities (RepoFOMO, backfill) run the full ladder; velocity
  corroborators (Trending Repos) cap at watch; github_trending keeps velocity
  tiers by explicit decision (§4.4); GitHub sources combine per entity.
- Archetype B stays salience + recency; HN keeps its `>= 200` salience break.
- Trending Repos watch enqueues a backfill (3 guards, §4.2); `MIN_BASELINE` lives
  in `rules.json` per magnitude (§3).
- npm v2 core ships `daily/7d` rising only; **package-family burst is deferred to
  its own task behind an eval** (§4.3), not in v2 core.

Calibrated against pool distribution before final lock (`[calibrate]`):
- All absolute floors; momentum bands (`1.2 / 2.0 / 3.0`; npm rising
  `1.3 / 2.0 / 3.0`); `min_baseline_stars_per_day` / `min_baseline_daily_downloads`;
  Trending Repos `stars_velocity` floor.
- Constraint (user): floors are absolute and always present; **percentiles are NOT
  used** for tier bands — on a quiet day a percentile gate would force false
  positives. Bands are fixed absolute ratios.
- Add a calibration test that flags too-small / too-fragile entries in the high
  tier (§4.1); only act on it if it fires.

## Appendix — measured pool sizes (2026-05-30 snapshot)

| Source | v1 fired (w/p/h) | v2 fired (w/p/h) | notes |
|---|---|---|---|
| RepoFOMO | 306 (249/31/26) | 66 (44/13/9) | accel authority, clean pyramid |
| Trending Repos | 186 (121/59/6) | 52 (52/0/0) | watch-only; sv≥150 & len(spark)≥3 & slope>0 |
| GitHub Trending (daily) | 19 (16/3/0) | 19 (16/3/0) | velocity tiers, unchanged (opt B) |
| npm | ~167 (89/35/43) | ~25 (19/4/2) | approx; needs backfilled daily |

Single-source, single-day counts. After repo-URL combine + OR-dedup, the real
GitHub pool is smaller than the row sum.
