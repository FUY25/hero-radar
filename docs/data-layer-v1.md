# Data Layer v1

The first layer is a source-level leaderboard, not a cross-platform entity graph.

## Leaderboard Windows

Use the same candidate pool, but view movement through different time windows:

```text
24h
7d
30d
current
```

`24h / 7d / 30d` are ranking windows. They should not shrink the repo/product universe to only newly created things. A six-year-old project can appear if it is suddenly moving now.

## Minimum Item Fields

Every row should have:

```text
source
window
external_id
name
url
description
fetched_at
source_rank
metadata_json
raw_json
```

Meaning:

- `source_rank`: native rank when the source provides a leaderboard.
- `metadata_json`: normalized useful fields for display and scoring.
- `raw_json`: original source payload for debugging.

The SQLite schema still contains legacy `heat`, `velocity`, and `acceleration`
columns, but the source dashboard/export layer treats them as deprecated. Source
tabs should show source-native facts from `metadata_json` / `raw_json`. Any later
candidate scoring layer can compute derived heat/velocity/acceleration in its
own contract.

## Source-Specific Metadata

### GitHub Trending

```text
repo full_name
stars_total
period_stars
language
window: 24h / 7d / 30d
description
rank
```

This is the best current shortcut because GitHub Trending already exposes `stars today`, `stars this week`, and `stars this month`.

### GitHub Search

```text
repo full_name
stars
forks
created_at
pushed_at
language
topics
query
```

This is a discovery source, not a clean velocity source. Any velocity should be
computed later in a derived layer, not shown as source truth.

### GitHub Momentum Boards

These tabs are for third-party GitHub momentum boards that already compute star
velocity / new-star windows for us. They are collected by one adapter
(`github_movers`) but displayed as separate dashboard tabs.

Current sources:

```text
github_movers_trending_repos
  provider: Trending Repos
  windows: daily / weekly / monthly -> 24h / 7d / 30d
  fields: fullName, description, primaryLanguage, topics, starsCount,
          forksCount, rank, languageRank, score, scoreComponents,
          sparkline

github_movers_repofomo
  provider: RepoFOMO
  window: 7d+30d+60d leaderboard row
  fields: total stars, 7d new stars, 30d new stars, 60d new stars,
          forks, new forks, fork growth %, subscribers, star age,
          description, info
```

Both are public web sources and currently need no API key or paid account.
Displayed tabs:

```text
Trending Repos
RepoFOMO
```

Use `python3 pipeline/run_pipeline.py --only github_movers` to refresh these
tabs without refreshing the rest of the dashboard.

### Hacker News Algolia

```text
story id
title
url
points
comments
author
created_at
query
window: 24h / 7d / 30d
HN item URL
```

### Hacker News Firebase

```text
story id
title
url
score
comments
author
created_at_unix
native list: topstories / newstories / beststories
```

This is a current snapshot. It is not a 24h/7d/30d window yet.

### Product Hunt

```text
post id
name
slug
tagline
website
votes
comments
reviews
daily_rank
weekly_rank
created_at
featured_at
topics
makers
```

Direct PH GraphQL is preferable to Apify for v1 because we already have API access.

### Hugging Face

```text
resource type: model / dataset / space
id
likes
downloads
created_at
last_modified
pipeline_tag
tags
rank by trendingScore
```

HF is mostly ecosystem proximity and derivative signal, not always direct product signal.

### X / Twitter via Apify

For targeted account tweet monitoring:

```text
account handle
post id
post text
post url
created_at
likes
replies
reposts
views
author follower count
matched seed account or query
extracted mentioned projects / accounts / known AI-product terms
windows: 24h / 7d / 30d
```

For following-list expansion:

```text
username
display name
bio
followers_count
following_count
profile url
```

Current implementation is two-step:

```text
pipeline/run_apify_x_following.py
  -> data/exports/x_following_ai_seed_candidates_latest.json
  -> top AI-related following accounts by followers_count

pipeline/run_apify_x_tweets.py
  -> actor fastdata/twitter-scraper
  -> data/exports/x_tweets_latest.json
  -> mentions aggregate + individual tweet rows

pipeline/run_pipeline.py
  -> reads x_tweets_latest.json
  -> emits x_project_mentions and x_tweets rows into the X Tweets / Mentions tab
```

## First Dashboard

The first dashboard should show:

```text
source coverage
window coverage
top rows by score
score components
heat / velocity / acceleration
source errors
direct source links
```

Already generated at:

```text
data/exports/dashboard.html
```
