# Hero Radar

Internal tool for finding AI product/application-layer opportunities that are starting to move.

Current focus:

- Collect many low-friction public data sources.
- Keep the source dashboard factual: show each source's own ranks, counts, text, links, and metadata.
- Save raw snapshots so a later derived scoring layer can compute heat, velocity, and acceleration separately.
- Keep the system lightweight enough to use daily.

Primary spec: [docs/product-spec-v0.7.md](/Users/fuyuming/Documents/Hero%20radar/docs/product-spec-v0.7.md)

## First Pipeline Slice

Run:

```bash
python3 pipeline/run_pipeline.py
```

Run only the GitHub momentum adapter without refreshing the other channels:

```bash
python3 pipeline/run_pipeline.py --only github_movers
```

This exports two dashboard tabs: `Trending Repos` and `RepoFOMO`.

Regenerate exports from the latest local snapshots without collecting anything:

```bash
python3 pipeline/run_pipeline.py --export-only
```

Outputs:

- `data/hero_radar.sqlite`
- `data/exports/latest_items.json`
- `data/exports/latest_scores.md`
- `data/exports/dashboard.html`

Open the first local dashboard:

```bash
open data/exports/dashboard.html
```

Or serve the dashboard with the local backend:

```bash
python3 pipeline/server.py --port 8787
open http://127.0.0.1:8787/
```

Local backend endpoints:

- `GET /api/config`: read `pipeline/config.json` plus schedule metadata.
- `POST /api/config`: replace `pipeline/config.json`; a timestamped backup is created first. Changes take effect on the next pipeline run.
- `POST /api/run`: manually trigger `pipeline/run_pipeline.py`; pass `{"only":["github_movers"]}` to run selected adapters.

Cron is not configured yet. The current product assumption is one full run every 24 hours once scheduling is added.

## Decision Pipeline Slice

Run the deterministic pre-Layer2 decision pipeline:

```bash
python3 -m pipeline.decision.run_decision \
  --db data/hero_radar.sqlite \
  --export-json data/exports/candidates_latest.json
```

This reads the latest source snapshots, performs Stage A entity resolution,
evaluates deterministic source rules, writes `potential_candidates`,
`edge_watch_candidates`, `backfill_jobs`, and `evidence_rows`, then exports
`data/exports/candidates_latest.json`.

This command does not call any LLM and does not run Layer 2 Daily Feed selection.

Optional environment variables:

- `GITHUB_TOKEN`: increases GitHub Search/Core API rate limits.
- `PRODUCTHUNT_TOKEN`: enables Product Hunt GraphQL launches/posts collection.
- `PRODUCTHUNT_USER_CONTEXT`: optional Product Hunt user context.
- `APIFY_TOKEN`: used by the manual X following / X tweets Apify scripts.
- `APIFY_ENABLE_RUNS`: must be `true` before any Apify actor execution is allowed.
- `DEEPSEEK_API_KEY`: for the next LLM-analysis slice, not required yet.

## X Following Seed Expansion

This is intentionally separate from the main pipeline because it spends Apify credits.

Dry run:

```bash
python3 pipeline/run_apify_x_following.py --max-results 600 --top 100
```

Real capped run:

```bash
APIFY_ENABLE_RUNS=true python3 pipeline/run_apify_x_following.py --run --max-results 600 --top 100
```

Latest exports:

- `data/exports/x_following_top100_latest.md`
- `data/exports/x_following_ai_seed_candidates_latest.md`

## X Tweets / Mention Signals

This is also separate from the main pipeline because it spends Apify credits. It
uses the top AI-related accounts from your X following export, then scrapes
recent tweets for the dashboard:

- `x_tweets`: single-tweet rows with text, author, created time, and extracted mentions. Engagement is retained in metadata but is not a dashboard ranking signal.

`x_project_mentions` was removed from the source dashboard because it was mostly
X accounts/keywords rather than reliable project entities. Keep the raw tweet
evidence first; derive project candidates later in a separate scoring layer.

Dry run:

```bash
python3 pipeline/run_apify_x_tweets.py --accounts 50 --per-account 30 --since-days 30
```

Real capped run:

```bash
APIFY_ENABLE_RUNS=true python3 pipeline/run_apify_x_tweets.py --run --accounts 50 --per-account 30 --since-days 30
```

Latest exports:

- `data/exports/x_tweets_latest.json`
- `data/exports/x_tweets_latest.md`

The X actor output is also upserted into SQLite by `tweet_id`:

- `x_tweets_store`: all normalized tweets fetched from the actor, including rows outside the current dashboard windows.
- `x_account_cursor`: latest seen tweet per seed account.

The dashboard X tab is generated from `x_tweets_store` by filtering `created_at`
into the configured `24h / 7d / 30d / 30d+` windows. `30d+` keeps older seed
account tweets visible as background evidence while recent windows stay truthful.
This means repeated actor runs can overlap safely without duplicating tweets.

After refreshing X tweets, run the main pipeline again:

```bash
python3 pipeline/run_pipeline.py
open data/exports/dashboard.html
```
