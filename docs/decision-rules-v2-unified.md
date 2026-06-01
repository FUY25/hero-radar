# Decision Rules v2 — Unified Floor + Momentum Model

Status: finalized mechanism. This document is the source of truth for the
pre-Layer-2 deterministic decision rules. It supersedes the per-source ad-hoc
thresholds in `pipeline/rules.json` v1 (`rules-v1`). Engineer implements against
this; exact numeric values marked `[calibrate]` are tuned against the real
candidate-pool distribution before lock.

Cross-references:
- Current code: `pipeline/decision/rules.py`, `pipeline/rules.json`
- Benchmark: `docs/benchmark-hermes-openclaw.md`

## 1. Why this rewrite

v1 shape was `level = f(absolute_amount)`, with velocity/acceleration only acting
as a secondary switch on the middle tier. Consequences:

- Watch tier is a pure absolute floor → big-but-flat projects sit permanently in
  the pool (the "too wide" problem).
- `high_potential` mostly bypasses momentum (RepoFOMO `OR`, npm no-rising) → the
  largest, most obvious projects auto-fire. Those are exactly what a radar does
  not need to surface.

Benchmark target: Hermes Agent's useful detection point is **T0 = acceleration
(2nd-derivative) peak at ~4.45k stars**, long before the velocity peak at ~30.5k.
We must fire when something is *speeding up while still small*, not when it is
already huge. Therefore: **momentum is primary, absolute amount is a noise floor.**

## 2. Two archetypes (the unifying principle)

Every source falls into exactly one archetype. This is the explainability spine:
no source should mix the two.

### Archetype A — Adoption curve (floor + momentum)

Measures a **cumulative adoption count that grows over time** (stars, forks,
downloads, derivative-resource count). A curve exists → derivatives are
meaningful. Model:

```
floor      = absolute minimum, anti-spam only, never promotes by itself
velocity   = recent daily rate
baseline   = longer-window daily rate (or N-day average)
accel      = velocity / baseline          # scale-free, removes size bias
level      = momentum(accel, velocity) gated by floor
```

Scale-free `accel` is the crux: a ratio cancels the project's size, so a giant
established repo does not auto-trip.

### Archetype B — Event / attention (salience + recency)

Measures a **discrete event's significance** (an HN discussion, a PH launch, a
KOL tweet). No curve to differentiate. The right model is salience + recency, not
momentum. Forcing acceleration here is wrong. **Sources: HN, Product Hunt, X.**
X is the extreme (semantic/KOL); HN and PH are the same archetype with a numeric
salience proxy (points, rank). This is a design rule, not an oversight: do not
add momentum to archetype B "for consistency".

Reason it is structurally different: momentum needs a *scale* to normalize.
Archetype B has no scale — significance is intrinsic.

## 3. Shared momentum primitive (Archetype A helper)

All Archetype A sources call one helper so the mechanism is uniform:

```
momentum_level(velocity, baseline, *, floor, accel_watch, accel_promote,
               v_potential, v_high, breakglass_v, breakglass_extra=None):

  if velocity < floor:              return "none"
  accel = velocity / max(baseline, MIN_BASELINE)   # guard tiny denominators

  level = "none"
  if accel >= accel_watch:                                   level = "watch"
  if accel >= accel_promote and velocity >= v_potential:     level = "potential"
  if accel >= accel_promote and velocity >= v_high:          level = "high_potential"

  # break-glass: extreme magnitude flags regardless of accel (rare, recall)
  if velocity >= breakglass_v or (breakglass_extra and breakglass_extra fires):
      level = max(level, "high_potential")
  return level
```

Guards:
- `MIN_BASELINE` prevents divide-by-near-zero blowups.
- New entities with no real long window (`age < baseline_window`) skip the accel
  branch and fall back to **velocity-only** thresholds (cannot fabricate a
  baseline). RepoFOMO exposes `star_age_days`; use it to route.

### Break-glass (`破例通道`) — design rationale

`high_potential` is normally "fast AND big" (momentum-gated). Break-glass is a
deliberate, rare absolute ceiling that flags an entity even without acceleration,
so a genuinely massive one-shot or already-past-peak event is not missed. It is
NOT the routine high tier; thresholds are set high enough to stay rare.
Aligns with benchmark philosophy: closer-look is cheap, missing a Hermes/OpenClaw
event is expensive.

Default accel knobs (shared starting points, `[calibrate]`):
`accel_watch = 1.2`, `accel_promote = 2.0`.

## 4. Archetype A — finalized per-source rules

All `[calibrate]` numbers are starting points pending pool-distribution review;
the *shape* is locked.

### 4.1 RepoFOMO (`github_movers_repofomo`)
Richest data (7/30/60d), computes both velocity and acceleration with no backfill.

```
r7  = stars_7d  / 7        # velocity
r30 = stars_30d / 30       # baseline
r60 = stars_60d / 60
accel = r7 / r30

floor:        stars_7d >= 70           [calibrate]   (r7 ~ 10/day, anti-spam)
watch:        accel >= 1.2  and floor
potential:    accel >= 2.0  and stars_7d >= 300  and (r7 > r30 > r60)   [calibrate]
high:         accel >= 2.0  and stars_7d >= 1500                        [calibrate]
break-glass:  stars_7d >= 8000  or  new_forks >= 500                    [calibrate]
new-repo fallback (star_age_days < 60): use velocity-only (stars_7d tiers), skip accel
```

Removed: the v1 pure-absolute `stars_7d>=5000 OR new_forks>=300` high path.
Validation: OpenClaw 2026-05-30 snapshot (r7=32, r30=58 → accel 0.55, decelerating)
correctly does NOT fire potential — it had already peaked.

### 4.2 Trending Repos (`github_movers_trending_repos`)
Has `stars_velocity`, `forks_velocity`, and a 7-point `sparkline` (currently the
sparkline is unused — that is where acceleration comes from).

```
velocity = stars_velocity                        (also evaluate forks_velocity)
accel    = mean(sparkline[-3:]) / mean(sparkline[:4])   # recent vs early slope

floor:        stars_velocity >= 150   [calibrate]   (benchmark mover floor)
watch:        floor and sparkline rising (accel >= 1.2)
potential:    stars_velocity >= 800 and accel >= 2.0           [calibrate]
high:         stars_velocity >= 2500 and accel >= 2.0          [calibrate]
break-glass:  stars_velocity >= 5000 or forks_velocity >= 300  [calibrate]
forks track:  forks_velocity >= 50 counts as a strong floor (dev-adoption signal)
```

Validation: Hermes 2026-05-30 sparkline `[1291,1231,1558,1449,1560,1364,1216]`
is flat/declining (accel ~1.0) → would NOT pass the new potential gate despite
`stars_velocity=1290`, because it is past peak. v1 fired it on velocity alone.

### 4.3 npm (`npm_registry`)
Only daily + 7d available → limited acceleration, use rising-vs-baseline ratio.

```
velocity = daily_downloads
baseline = downloads_7d / 7
rising   = daily_downloads / baseline      # scale-free spike vs own baseline

floor:        daily_downloads >= 3000   [calibrate]   (anti-micro-package)
watch:        rising >= 1.3 and floor                            [calibrate]
potential:    daily_downloads >= 10000 and rising >= 1.3         [calibrate]
high:         daily_downloads >= 50000  and rising >= 1.3        [calibrate]
break-glass:  daily_downloads >= 500000                          [calibrate]
```

- `rising` is now required at ALL promote tiers (v1 high had no rising → react/
  lodash-class packages permanently at high). This is the key npm fix.
- Validation: clawdbot 16k→70k→106k→139k. Rising ratio fires on day 2
  (70k vs ~16k baseline) — earlier than v1's absolute 100k gate on day 3.
- Additional first-class trigger (NEW, package-family burst): multiple scoped
  packages (`@scope/*`) published within 24h, or repeated versions of one package
  in 48h → treat as a momentum signal (publish cadence = acceleration proxy).
  This was OpenClaw's strongest early npm signal and v1 has no rule for it.

### 4.4 GitHub Trending (`github_trending`) — velocity-only by design
`stars_today` (daily delta) is already a velocity. Acceleration is NOT added:
single daily snapshot, no stored history; fetching yesterday's value each run is
not worth the cost.

```
velocity = stars_today
floor:     stars_today >= 300   [calibrate]
watch:     stars_today >= 300
potential: stars_today >= 1000
high:      stars_today >= 3000
```

This is an explicit, explainable exception, NOT an oversight, because:
1. Acceleration is delegated to entity level (see §6): RepoFOMO / Trending Repos
   carry accel for the same repo.
2. Presence on GitHub's official trending board is itself a curation/momentum
   proxy — GitHub already filtered for "surging today".
3. Benchmark: trending is positive-only; absence never counts against a candidate.

### 4.5 GitHub Search backfill prefilter — unchanged role
`stars_per_day = stars / age_days` + freshness gates (pushed ≤14d, created ≤180d).
This is a **budget prefilter** deciding who to spend stargazer-backfill on, not a
final verdict. It is lifetime-average velocity (coarse). Leave as a velocity floor;
real recent velocity arrives after backfill (§4.6). Keep v1 values
(`min_stars_per_day=50`).

### 4.6 GitHub backfill result (`extra_github_signals`) — add acceleration
After stargazer backfill we have precise `stars_24h` and `stars_7d`.

```
velocity = stars_24h
baseline = stars_7d / 7
accel    = stars_24h / baseline

floor:     stars_24h >= 300   [calibrate]   (reuse trending floor)
watch:     accel >= 1.2 and floor
potential: stars_24h >= 1000 and accel >= 2.0     [calibrate]
high:      stars_24h >= 3000 and accel >= 2.0     [calibrate]
break-glass: stars_24h >= 10000                   [calibrate]
```

v1 thresholds each metric independently against the trending table with no accel.
This is the same fix as §4.1–4.3.

### 4.7 Hugging Face (`huggingface`) — echo momentum, corroboration only
Count of derivative resources created in 48h. This is already a momentum shape
(resource-creation rate), floor = 1.

```
floor:     >= 1 resource in 48h  → watch
potential: >= 2 resources in 48h
```

Keep v1 values, but classify HF as **ecosystem-echo corroboration**, not a primary
growth curve. It measures derivative activity around an entity, not the entity's
own adoption (and HF owner/name fragmentation means same-project uploads do not
merge). Use it to corroborate, do not let it solo-promote past watch without a
GitHub link.

## 5. Archetype B — finalized (salience + recency, no momentum)

### 5.1 HN (`hn`)
7d window, max points across qualified stories. Salience proxy = points.

```
floor / watch:      max_points >= 30
break-glass / potential: max_points >= 200   (`points_breakthrough_potential`)
corroboration:      >= 3 qualified stories in 7d (benchmark)
```

Low-trust source: caps at watch on its own; the breakthrough line (`破例`) is the
only single-source path to potential. No momentum/acceleration. (Matches the
existing tiered-trust model.)

### 5.2 Product Hunt (`product_hunt`)
One-day launch event. Salience proxy = rank.

```
watch:     daily_rank <= 10
potential: daily_rank <= 5
```

No momentum concept; current shape is correct. Keep.

### 5.3 X (`x_tweets`) — out of scope for this round
Same archetype as HN/PH but salience is judged by LLM KOL-semantic tiering, not a
number. Governed by its own classifier (`x_social_tier`). Not part of the floor+
momentum rewrite.

## 6. GitHub entity combination (why §4.4 is safe)

Entity resolution (Stage A union-find) clusters board rows by GitHub repo URL /
repo id. All three GitHub board sources for one repo resolve to the **same
`entity_id` → same `EntityState`**; their evaluators promote the same state in
sequence (`rules.py` `evaluate_*` loop). Consequence:

- A repo on github_trending + (repofomo | trending_repos) gets acceleration from
  the siblings at entity level — github_trending only needs to add velocity.
- A repo only on github_trending relies on the board's own curation (acceptable
  per §4.4).

So velocity-only github_trending does not create an acceleration gap at the
entity level whenever a momentum-capable sibling source is present.

## 7. Meta-rules (unchanged, orthogonal to floor+momentum)

- `verified_cross_source`: 2 weak source-families within 48h → potential. An
  aggregation rule across sources; not a per-source curve. Keep.

## 8. What is locked vs. what is calibrated

Locked this round (mechanism):
- Two-archetype split and the per-source archetype assignment.
- Floor + momentum shape for all Archetype A sources; momentum = scale-free ratio.
- `high_potential` requires momentum (no pure-absolute high) except break-glass.
- Break-glass exists as a rare absolute recall path on every Archetype A source.
- github_trending stays velocity-only by design; GitHub sources combine per entity.
- Archetype B stays salience + recency; no momentum added.

Calibrated against pool distribution before final lock (`[calibrate]`):
- All absolute floors and tier velocity values.
- `accel_watch` / `accel_promote` (defaults 1.2 / 2.0), `MIN_BASELINE`, `rising`
  margin (default 1.3), break-glass ceilings.
- The user's stated constraint: floors are absolute (always present); percentiles
  are NOT used, because some days nothing important happens and a percentile gate
  would force false positives on quiet days.
