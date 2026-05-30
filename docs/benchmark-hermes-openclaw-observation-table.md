# Hermes Agent / OpenClaw Benchmark Observation Table

Last updated: 2026-05-30.

This document is the formal T-grid backtest table for Hermes Agent and
OpenClaw. It records source observations around each project's primary T0. It
does not define a final aggregate score.

## Bucket Convention

All times are UTC. Buckets are relative to each project's T0:

| Bucket | Interval |
|---|---|
| T-7d | [T0-7d, T0-3d) |
| T-3d | [T0-3d, T0-24h) |
| T-24h | [T0-24h, T0-12h) |
| T-12h | [T0-12h, T0-6h) |
| T-6h | [T0-6h, T0) |
| T0 | anchor timestamp only |
| T+6h | [T0, T0+6h) |
| T+24h | [T0+6h, T0+24h) |
| T+3d | [T0+24h, T0+3d) |
| T+7d | [T0+3d, T0+7d] |

Daily sources such as npm downloads are UTC day-granularity. When a UTC day
overlaps multiple hour buckets, the row note states the ambiguity.

Historical safety labels:

| Label | Meaning |
|---|---|
| as_of_safe | Historical event time is reliable. |
| partial_as_of | Event time is reliable, displayed count may be current/later. |
| snapshot_only | Only available when collected, not historically replayable. |
| current_probe | Live/current check used for identity or forward source behavior. |

## Project Anchors

| Project | T0 UTC | Canonical entity | Required aliases for this table |
|---|---:|---|---|
| Hermes Agent | 2026-03-11T13:51:05Z | `NousResearch/hermes-agent` | `hermes-agent`, `Hermes Agent`, `NousResearch/hermes-agent`, `NousResearch Hermes`; indirect high-recall: `hermes-paperclip-adapter` |
| OpenClaw | 2026-01-26T10:32:47Z | `openclaw/openclaw` | `clawdbot`, `ClawdBot`, `clawdbot/clawdbot`, `openclaw`, `OpenClaw`, `@clawdbot/*`, `@openclaw/*` |

## Hermes Agent Matrix

| Bucket | Interval UTC | GitHub | HN Algolia | Product Hunt | Hugging Face | npm | PyPI | Snapshot-only boards |
|---|---|---|---|---|---|---|---|---|
| T-7d | 2026-03-04T13:51:05Z..2026-03-08T13:51:05Z | no page probe in this pass | 1 strict canonical story on 2026-03-05 | 0 matching launch | 0 useful HF resource in T-grid | 0 useful package event | 0 useful release | not historically replayable |
| T-3d | 2026-03-08T13:51:05Z..2026-03-10T13:51:05Z | no page probe in this pass | 1 strict canonical story on 2026-03-09 | 0 matching launch | 0 useful HF resource | 0 useful package event | 0 useful release | not historically replayable |
| T-24h | 2026-03-10T13:51:05Z..2026-03-11T01:51:05Z | stargazer page 34 spans 3.3k-3.4k stars at 2026-03-10T14:49:51Z..18:32:11Z | 0 strict canonical stories | 0 matching launch | 0 useful HF resource | 0 useful package event | 0 useful release | not historically replayable |
| T-12h | 2026-03-11T01:51:05Z..2026-03-11T07:51:05Z | no page probe in this pass | 0 strict canonical stories | 0 matching launch | 0 useful HF resource | 0 useful package event | 0 useful release | not historically replayable |
| T-6h | 2026-03-11T07:51:05Z..2026-03-11T13:51:05Z | stargazer page 45 spans 4.4k-4.5k stars at 2026-03-11T11:49:29Z..13:08:38Z | 0 strict canonical stories | 0 matching launch | 0 useful HF resource | 0 useful package event | 0 useful release | not historically replayable |
| T0 | 2026-03-11T13:51:05Z | acceleration anchor from prior milestone curve | anchor only | anchor only | anchor only | anchor only | anchor only | anchor only |
| T+6h | 2026-03-11T13:51:05Z..2026-03-11T19:51:05Z | no page probe in this pass | 0 strict canonical stories | 0 matching launch | 0 useful HF resource | 0 useful package event | 0 useful release | not historically replayable |
| T+24h | 2026-03-11T19:51:05Z..2026-03-12T13:51:05Z | stargazer page 55 spans 5.4k-5.5k stars at 2026-03-12T07:28:09Z..09:22:11Z | 1 strict canonical story on 2026-03-11T21:09:43Z | 0 matching launch | 0 useful HF resource | 0 useful package event | 0 useful release | not historically replayable |
| T+3d | 2026-03-12T13:51:05Z..2026-03-14T13:51:05Z | no page probe in this pass | 0 strict canonical stories | 0 matching launch | 0 useful HF resource | `hermes-paperclip-adapter` 2 releases on 2026-03-13; downloads 163 on 2026-03-13 | 0 useful release | not historically replayable |
| T+7d | 2026-03-14T13:51:05Z..2026-03-18T13:51:05Z | no page probe in this pass | 0 strict canonical stories; loose false positive excluded | 0 matching launch | 0 useful HF resource in T-grid | `hermes-paperclip-adapter` downloads rise to 763, 2620, 2331, 4748 on 2026-03-15..18 | 0 useful release | not historically replayable |

## Hermes Agent Observation Rows

| Project | Alias | Source | Bucket | Event At | Metric | Value | Label | Safety | Note | Raw Ref |
|---|---|---|---|---:|---|---:|---|---|---|---|
| Hermes Agent | `NousResearch/hermes-agent` | GitHub stargazers | T-24h | 2026-03-10T14:49:51Z..18:32:11Z | stargazer_page_span | page 34 ~= positions 3301-3400 | early_trigger | as_of_safe for current stargazers | Validates that the repo was entering the 3.4k star band before T0. Missing unstarred/deleted users. | GitHub REST stargazers page 34 |
| Hermes Agent | `NousResearch/hermes-agent` | GitHub stargazers | T-6h | 2026-03-11T11:49:29Z..13:08:38Z | stargazer_page_span | page 45 ~= positions 4401-4500 | early_trigger | as_of_safe for current stargazers | Directly validates the earlier T0 star band around 4.45k stars. | GitHub REST stargazers page 45 |
| Hermes Agent | `NousResearch/hermes-agent` | GitHub stargazers | T+24h | 2026-03-12T07:28:09Z..09:22:11Z | stargazer_page_span | page 55 ~= positions 5401-5500 | confirm_signal | as_of_safe for current stargazers | Shows continued growth after acceleration T0. | GitHub REST stargazers page 55 |
| Hermes Agent | `hermes-agent` alias pack | HN Algolia | T-7d | 2026-03-05T17:08:51Z | strict_hn_story_count | 1 | early_trigger, weak | as_of_safe event; partial_as_of points/comments | HN item `47264225`: `NousResearch/hermes-agent: The agent that grows with you`, URL points to canonical repo. | https://news.ycombinator.com/item?id=47264225 |
| Hermes Agent | `hermes-agent` alias pack | HN Algolia | T-3d | 2026-03-09T10:44:00Z | strict_hn_story_count | 1 | early_trigger, weak | as_of_safe event; partial_as_of points/comments | HN item `47307301`: `Hermes Agent`, URL points to canonical repo. | https://news.ycombinator.com/item?id=47307301 |
| Hermes Agent | `hermes-agent` alias pack | HN Algolia | T-24h..T+6h | n/a | strict_hn_story_count | 0 | no_observation | as_of_safe for queried API window | No strict canonical HN stories in the immediate pre-T0 and first 6h post-T0 buckets. | HN Algolia search_by_date |
| Hermes Agent | `hermes-agent` alias pack | HN Algolia | T+24h | 2026-03-11T21:09:43Z | strict_hn_story_count | 1 | confirm_signal | as_of_safe event; partial_as_of points/comments | HN item `47341909`: `Hermes Agent: The self-improving AI agent`. | https://news.ycombinator.com/item?id=47341909 |
| Hermes Agent | `Hermes Agent` / `NousResearch` | Product Hunt GraphQL | all T-grid buckets | n/a | matching_launch_count | 0 | late_or_not_useful | no matching launch observed | No matching Hermes/Nous launch in T0 +/- 7d. PH pagination/rate-limit means zero rows are lower-confidence than exact HN object IDs. | Product Hunt GraphQL posts(postedAfter, postedBefore) |
| Hermes Agent | `hermes-agent` | Hugging Face API | all T-grid buckets | n/a | hf_resource_created | 0 | late_or_not_useful | n/a | Authless HF API search did not surface useful Hermes resources inside T0 +/- 7d. | HF API search |
| Hermes Agent | `hermes-paperclip-adapter` | npm packument | T+3d | 2026-03-13T00:23:04Z | npm_release_count | 2 | ecosystem_echo / high_recall_only | as_of_safe | First indirect Hermes-related npm package release, about 34.5h after T0; not canonical. | https://registry.npmjs.org/hermes-paperclip-adapter |
| Hermes Agent | `hermes-paperclip-adapter` | npm downloads | T+3d | 2026-03-13 | daily_downloads | 163 | ecosystem_echo, weak | as_of_safe day-granularity | First nonzero daily downloads for indirect package. | npm downloads range |
| Hermes Agent | `hermes-paperclip-adapter` | npm downloads | T+7d | 2026-03-15..2026-03-18 | daily_downloads | 763 -> 4748 | ecosystem_echo, moderate | as_of_safe day-granularity | Indirect package becomes visible, but only after T0 and not canonical. | npm downloads range |
| Hermes Agent | `hermes-agent` | PyPI JSON | all T-grid buckets | n/a | pypi_release_count | 0 | late_or_not_useful | n/a | Canonical PyPI package appears later, outside the T-grid. | https://pypi.org/pypi/hermes-agent/json |
| Hermes Agent | `hermes-agent` | Hugging Face API | outside T-grid | 2026-03-28..2026-04-05 | derivative_hf_resources | 3+ | confirm_signal / ecosystem_echo | createdAt as_of_safe; likes/downloads current | HF derivative datasets/spaces become useful only after T+7d. | HF API search |
| Hermes Agent | `hermes-agent` | npm / PyPI | outside T-grid | 2026-05-14..2026-05-25 | canonical_package_release | 2 late package surfaces | late_or_not_useful | as_of_safe | Exact npm and PyPI packages are far too late for Hermes T0 detection. | npm/PyPI package APIs |

## OpenClaw Matrix

| Bucket | Interval UTC | GitHub | HN Algolia | Product Hunt | Hugging Face | npm | PyPI | Snapshot-only boards |
|---|---|---|---|---|---|---|---|---|
| T-7d | 2026-01-19T10:32:47Z..2026-01-23T10:32:47Z | no page probe in this pass | 2 strict old-alias stories | 0 primary launch | 0 useful HF resource | `clawdbot` and `@clawdbot/*` already releasing; `clawdbot` downloads 2.2k-5.8k/day | 0 useful release | not historically replayable |
| T-3d | 2026-01-23T10:32:47Z..2026-01-25T10:32:47Z | no page probe in this pass | 7 strict old-alias stories | 0 primary launch | 0 useful HF resource | `clawdbot` release burst starts; downloads reach 16,242 on 2026-01-24 | 0 useful release | not historically replayable |
| T-24h | 2026-01-25T10:32:47Z..2026-01-25T22:32:47Z | no page probe in this pass | 3 strict old-alias stories | 0 primary launch | 0 useful HF resource | `clawdbot` 4 releases; `@clawdbot/*` 4-package synchronized release; downloads 70,768 on UTC day | 0 useful release | not historically replayable |
| T-12h | 2026-01-25T22:32:47Z..2026-01-26T04:32:47Z | no page probe in this pass | 4 strict old-alias stories, including canonical `clawdbot/clawdbot` repo URL | 0 primary launch | 0 useful HF resource | daily npm count overlaps this bucket | 0 useful release | not historically replayable |
| T-6h | 2026-01-26T04:32:47Z..2026-01-26T10:32:47Z | stargazer pages 233, 305, and 310 span about 23.3k to 31.0k stars between 04:42 and 10:33 | 0 strict stories | 0 primary launch | 1 model + 2 spaces under `clawdbot` | daily npm count overlaps this bucket | 0 useful release | not historically replayable |
| T0 | 2026-01-26T10:32:47Z | acceleration/velocity anchor from prior milestone curve | anchor only | anchor only | anchor only | anchor only | anchor only | anchor only |
| T+6h | 2026-01-26T10:32:47Z..2026-01-26T16:32:47Z | stargazer page 377 spans about 37.6k-37.7k stars at 16:11..16:17 | 3 strict old-alias stories | 0 primary launch | no new strong HF row | `clawdbot` UTC-day downloads 106,024 | 0 useful release | not historically replayable |
| T+24h | 2026-01-26T16:32:47Z..2026-01-27T10:32:47Z | no page probe in this pass | 12 strict old-alias stories | PH `OpenClaw` launch at 2026-01-27T08:01:00Z; daily rank 2, weekly rank 3 | no new strong HF row | `clawdbot` 2026-01-27 daily downloads 139,824, day-granularity overlaps T+3d | 0 useful release | not historically replayable |
| T+3d | 2026-01-27T10:32:47Z..2026-01-29T10:32:47Z | no page probe in this pass | 35 strict stories, rename/security wave | 0 primary launch | additional `clawdbot` spaces | `clawdbot` downloads remain above 100k/day on 2026-01-27 and 2026-01-28 | 0 useful release | not historically replayable |
| T+7d | 2026-01-29T10:32:47Z..2026-02-02T10:32:47Z | no page probe in this pass | 82 strict stories; new `OpenClaw` name becomes visible | 0 primary launch | `openclaw` spaces/models begin | new-name npm `openclaw` package appears; downloads jump above 100k/day | 0 useful release | not historically replayable |

## OpenClaw Observation Rows

| Project | Alias | Source | Bucket | Event At | Metric | Value | Label | Safety | Note | Raw Ref |
|---|---|---|---|---:|---|---:|---|---|---|---|
| OpenClaw | `clawdbot/clawdbot` -> `openclaw/openclaw` | GitHub repo API | identity | 2026-05-30 current probe | redirect_repo_id | 1103012935 | alias_proof | current_probe | Live request to old repo redirects to canonical repo id. Use repo id as primary key. | https://api.github.com/repos/clawdbot/clawdbot |
| OpenClaw | `openclaw/openclaw` | GitHub stargazers | T-6h | 2026-01-26T04:42:28Z..04:49:38Z | stargazer_page_span | page 233 ~= positions 23201-23300 | early_trigger | as_of_safe for current stargazers | Enters prior T0 lower star band inside final 6h. | GitHub REST stargazers page 233 |
| OpenClaw | `openclaw/openclaw` | GitHub stargazers | T-6h | 2026-01-26T09:59:33Z..10:05:30Z | stargazer_page_span | page 305 ~= positions 30401-30500 | early_trigger | as_of_safe for current stargazers | Very close to T0, already around 30.5k stars. | GitHub REST stargazers page 305 |
| OpenClaw | `openclaw/openclaw` | GitHub stargazers | T-6h/T+6h boundary | 2026-01-26T10:28:07Z..10:33:55Z | stargazer_page_span | page 310 ~= positions 30901-31000 | early_trigger | as_of_safe for current stargazers | Straddles exact T0 and validates midpoint around 30.5k. | GitHub REST stargazers page 310 |
| OpenClaw | `openclaw/openclaw` | GitHub stargazers | T+6h | 2026-01-26T16:11:08Z..16:17:06Z | stargazer_page_span | page 377 ~= positions 37601-37700 | confirm_signal | as_of_safe for current stargazers | Massive same-day continuation after T0. | GitHub REST stargazers page 377 |
| OpenClaw | `clawdbot` alias pack | HN Algolia | T-7d | 2026-01-21T23:59:47Z | strict_hn_story_count | 2 | early_trigger | as_of_safe event; partial_as_of points/comments | Old-name signal already present. | HN item 46713437 |
| OpenClaw | `clawdbot` alias pack | HN Algolia | T-3d | 2026-01-24T19:39:23Z | strict_hn_story_count | 7 | early_trigger, strong | as_of_safe event; partial_as_of points/comments | Dense pre-T0 old-name wave. | HN item 46749955 |
| OpenClaw | `clawdbot` alias pack | HN Algolia | T-24h | 2026-01-25T12:59:23Z | strict_hn_story_count | 3 | early_trigger, strong | as_of_safe event; partial_as_of points/comments | Pre-T0 discussion intensifies. | HN item 46755123 |
| OpenClaw | `clawdbot/clawdbot` | HN Algolia | T-12h | 2026-01-26T00:27:41Z | strict_hn_story_count | 4 | early_trigger, strong | as_of_safe event; partial_as_of points/comments | Strong canonical HN hit: `Clawdbot - open source personal AI assistant`, URL to old GitHub repo. | HN item 46760237 |
| OpenClaw | `clawdbot` alias pack | HN Algolia | T-6h | n/a | strict_hn_story_count | 0 | no_observation | as_of_safe for queried API window | No strict HN story in final 6h before T0; prior HN buckets already strong. | HN Algolia search_by_date |
| OpenClaw | `clawdbot` alias pack | HN Algolia | T+6h | 2026-01-26T10:55:35Z | strict_hn_story_count | 3 | early_trigger, strong | as_of_safe event; partial_as_of points/comments | Immediate post-T0 continuation under old alias. | HN item 46764139 |
| OpenClaw | `clawdbot` alias pack | HN Algolia | T+24h | 2026-01-26T16:45:24Z | strict_hn_story_count | 12 | early_trigger, strong | as_of_safe event; partial_as_of points/comments | Heavy old-name discussion and rename setup. | HN item 46776904 |
| OpenClaw | `clawdbot` / `moltbot` wave | HN Algolia | T+3d | 2026-01-27T10:45:24Z | strict_hn_story_count | 35 | confirm_signal / ecosystem_echo | as_of_safe event; partial_as_of points/comments | Rename/security/derivative discussion dominates. | HN item 46783863 |
| OpenClaw | `clawdbot` / `openclaw` alias pack | HN Algolia | T+7d | 2026-01-29T12:55:29Z | strict_hn_story_count | 82 | confirm_signal / ecosystem_echo | as_of_safe event; partial_as_of points/comments | New `OpenClaw` name becomes visible after T0. | HN item 46820783 |
| OpenClaw | `OpenClaw` / `products/clawdbot-2/launches/openclaw` | Product Hunt GraphQL | T+24h | 2026-01-27T08:01:00Z | ph_launch_found | 1 | early_trigger / confirm_signal | featuredAt mostly as_of_safe; votes/comments partial_as_of | PH launch: daily rank 2, weekly rank 3, votes 835, comments 53 at observation. | https://www.producthunt.com/products/clawdbot-2/launches/openclaw |
| OpenClaw | `clawdbot` | Hugging Face model API | T-6h | 2026-01-26T05:29:27Z | hf_model_created | 1 | early_trigger / ecosystem_echo | createdAt as_of_safe; likes/downloads current | `KALLLA/clawdbot` model appears 5.1h before T0. | https://huggingface.co/KALLLA/clawdbot |
| OpenClaw | `clawdbot` | Hugging Face spaces API | T-6h | 2026-01-26T08:18:13Z..08:43:05Z | hf_space_created | 2 | early_trigger / ecosystem_echo | createdAt as_of_safe; likes current | `mbrycey/clawdbot-agent-2` and `acpr123/clawdbot` appear before T0. | HF API spaces search |
| OpenClaw | `clawdbot` | Hugging Face spaces API | T+3d | 2026-01-27T21:23:59Z..2026-01-29T08:09:05Z | hf_space_created | 4 | ecosystem_echo | createdAt as_of_safe; likes current | Additional old-alias spaces appear after T0. | HF API spaces search |
| OpenClaw | `openclaw` | Hugging Face spaces/models API | T+7d | 2026-01-30T04:27:54Z..2026-02-01T15:39:53Z | hf_resource_created | 3+ | confirm_signal / ecosystem_echo | createdAt as_of_safe; likes/downloads current | New-name HF wave starts inside T+7d. | HF API search |
| OpenClaw | `clawdbot` | npm packument | T-7d | 2026-01-21T06:56:57Z | npm_release_count | 7 | early_trigger, weak | as_of_safe | Old-name package already had active release cadence before T-3d. | https://registry.npmjs.org/clawdbot |
| OpenClaw | `clawdbot` | npm downloads | T-7d | 2026-01-21..2026-01-22 | daily_downloads | 5431 -> 5869 | ecosystem_echo, weak | as_of_safe day-granularity | Nontrivial package use before the main wave. | npm downloads range |
| OpenClaw | `clawdbot` | npm packument | T-3d | 2026-01-24T12:41:17Z..13:30:15Z | npm_release_count | 2 | early_trigger, strong | as_of_safe | Start of tight pre-T0 release burst. | https://registry.npmjs.org/clawdbot |
| OpenClaw | `clawdbot` | npm downloads | T-3d | 2026-01-24 | daily_downloads | 16242 | early_trigger, strong | as_of_safe day-granularity | First sharp daily-download step before T0. | npm downloads range |
| OpenClaw | `clawdbot` | npm packument | T-24h | 2026-01-25T13:48:00Z..15:21:46Z | npm_release_count | 4 | early_trigger, strong | as_of_safe | Four old-name releases inside the T-24h bucket. | https://registry.npmjs.org/clawdbot |
| OpenClaw | `@clawdbot/*` | npm packument | T-24h | 2026-01-25T13:17:53Z..13:18:18Z | scoped_package_burst | 4 packages | early_trigger, strong | as_of_safe | `@clawdbot/matrix`, `msteams`, `voice-call`, and `zalo` release in the same minute. | npm registry scoped packages |
| OpenClaw | `clawdbot` | npm downloads | T-24h | 2026-01-25 | daily_downloads | 70768 | early_trigger, strong | as_of_safe day-granularity | UTC day overlaps T-24h/T-12h; assigned to T-24h because release burst is inside this bucket. | npm downloads range |
| OpenClaw | `clawdbot` | npm downloads | T+6h | 2026-01-26 | daily_downloads | 106024 | early_trigger, strong | as_of_safe day-granularity | T0 UTC day crosses 100k downloads; day overlaps multiple hour buckets. | npm downloads range |
| OpenClaw | `clawdbot` | npm downloads | T+24h | 2026-01-27 | daily_downloads | 139824 | early_trigger, strong | as_of_safe day-granularity | Old-name package peaks in the queried range on the day after T0. | npm downloads range |
| OpenClaw | `openclaw` | npm packument | T+7d | 2026-01-29T11:08:12Z | npm_release_count | 9 | confirm_signal / alias_rename | as_of_safe | New-name package appears about 3 days after T0. | https://registry.npmjs.org/openclaw |
| OpenClaw | `openclaw` | npm downloads | T+7d | 2026-01-30..2026-02-01 | daily_downloads | 104954 -> 156674 -> 154369 | confirm_signal, strong | as_of_safe day-granularity | New-name package becomes very strong after the rename. | npm downloads range |
| OpenClaw | `openclaw` / `clawdbot` | PyPI JSON | all T-grid buckets | n/a | pypi_release_count | 0 | late_or_not_useful | n/a | PyPI packages appear weeks later. | PyPI JSON |

## Snapshot-Only Board Rows

These are already wired into the dashboard, but current source endpoints do not
expose historical `as_of` replay. They should be used for forward daily capture,
not as historical T0 proof.

| Project | Source | Event At | Metric | Value | Safety | Note |
|---|---|---:|---|---|---|---|
| Hermes Agent | Trending Repos direct current source | 2026-05-30 | daily / weekly / monthly ranks | 9 / 9 / 4 | snapshot_only | Daily row: language rank 5, `source_score=873.03`, `stars_velocity=1290.0`, `forks_velocity=330.0`, `freshness_bonus=0.2913`, sparkline `[1291,1231,1558,1449,1560,1364,1216]`. |
| Hermes Agent | RepoFOMO direct current source | 2026-05-30 | rank and windows | rank 10; 7d=910; 30d=5673; 60d=23204; new_forks=1227 | snapshot_only | Useful going forward; not historical T0 evidence. |
| OpenClaw | Trending Repos direct current source | 2026-05-30 | daily / monthly ranks | 86 / 63 | snapshot_only | Daily row: language rank 26, `source_score=150.93`, `stars_velocity=213.5`, `forks_velocity=76.0`, `freshness_bonus=0.3357`, sparkline `[187,228,213,246,220,212,215]`. |
| OpenClaw | RepoFOMO direct current source | 2026-05-30 | rank and windows | rank 136; 7d=226; 30d=1735; 60d=6816; new_forks=546 | snapshot_only | Useful going forward; not historical T0 evidence. |

## GitHub Exact Rollups

These rows were added after the matrix pass from a focused GitHub/HN read-only
check. They use GitHub REST `starred_at` and fork `created_at`; OpenClaw
stargazers after page 400 become lower bounds because GitHub returned page-cap
errors beyond that range.

| Project | Alias | Source | Window | Metric | Value | Label | Safety | Note |
|---|---|---|---|---|---:|---|---|---|
| Hermes Agent | `NousResearch/hermes-agent` | GitHub stargazers | T0..T+24h | star_delta | 1094 | strong_github_acceleration | as_of_safe for current stargazers | Endpoint page cap did not affect this window. |
| Hermes Agent | `NousResearch/hermes-agent` | GitHub stargazers | T-7d..T+7d | star_delta | 6834 | strong_github_acceleration | as_of_safe for current stargazers | Exact within current stargazer set. |
| Hermes Agent | `NousResearch/hermes-agent` | GitHub stargazers | T0..2026-04-06T13:29:32Z | star_delta | 22149 | confirm_window_growth | as_of_safe for current stargazers | Boundary records bracket confirm at 13:29:15Z and 13:30:06Z. |
| Hermes Agent | `NousResearch/hermes-agent` | GitHub forks | T0..T+24h | fork_delta | 94 | fork_velocity | as_of_safe for surviving public forks | Exact from fork `created_at`. |
| Hermes Agent | `NousResearch/hermes-agent` | GitHub forks | T0..T+7d | fork_delta | 449 | fork_velocity | as_of_safe for surviving public forks | Exact from fork `created_at`. |
| Hermes Agent | `NousResearch/hermes-agent` | GitHub forks | T0..2026-04-06T13:29:32Z | fork_delta | 2880 | confirm_window_fork_growth | as_of_safe for surviving public forks | Exact from fork `created_at`. |
| OpenClaw | `openclaw/openclaw` | GitHub stargazers | T-24h..T0 | star_delta | 19760 | pre_t0_star_surge | as_of_safe for current stargazers | Boundary at T0 was 10:32:43Z then 10:32:48Z. |
| OpenClaw | `openclaw/openclaw` | GitHub stargazers | T0..2026-01-26T19:17:49Z | star_delta | >=9024 | strong_github_acceleration | lower_bound_due_page_cap | GitHub rejected stargazer page 401, so T0..T+24h and T0..T+7d are lower bounds only. |
| OpenClaw | `openclaw/openclaw` | GitHub stargazers | T-7d..2026-01-26T19:17:49Z | star_delta | >=35140 | extreme_github_acceleration | lower_bound_due_page_cap | Actual T-7d..T+7d is higher. |
| OpenClaw | `openclaw/openclaw` | GitHub forks | T0..T+24h | fork_delta | 3123 | extreme_fork_velocity | as_of_safe for surviving public forks | Early fork names often still used `clawdbot`, confirming alias importance. |
| OpenClaw | `openclaw/openclaw` | GitHub forks | T0..T+7d | fork_delta | 16723 | extreme_fork_velocity | as_of_safe for surviving public forks | Exact from fork `created_at`. |
| OpenClaw | `openclaw/openclaw` | GitHub forks | T-7d..T+7d | fork_delta | 19294 | extreme_fork_velocity | as_of_safe for surviving public forks | Exact from fork `created_at`. |

## Benchmark Updates From This Table

The table supports these changes to the benchmark standard:

1. Hermes should pass if GitHub acceleration fires by T-6h/T0, even if PH, HF,
   exact npm, and PyPI are zero. HN strict canonical stories are weak
   corroboration, not a required source.

2. OpenClaw should pass no later than T-24h if alias-aware HN and npm are used,
   and no later than T-6h if GitHub/HF are used. A benchmark that only queries
   `openclaw` and misses `clawdbot` should fail.

3. HN trigger should be split into:
   - weak: 1 strict canonical/alias story in T-7d..T+24h;
   - strong: >=3 strict dedup stories in any bucket or >=5 in 48h.

4. npm trigger should include:
   - daily downloads >=10k;
   - >=2 versions of the same package in 48h;
   - >=3 packages in the same scoped package family in 24h.

5. HF trigger should include:
   - >=2 exact-alias models/datasets/spaces in 48h;
   - one exact-alias HF resource within 6h of a GitHub/npm/HN strong signal.

6. PH launch with daily rank <=5 is strong corroboration, but for T0 detection
   it should be allowed to arrive in T+24h. It should not be required for
   Hermes-like cases.

7. Trending Repos and RepoFOMO should remain forward-looking daily snapshot
   triggers. Historical T0 benchmark rows should not claim exact replay of
   `source_score`, `freshnessBonus`, ranks, or sparkline.
