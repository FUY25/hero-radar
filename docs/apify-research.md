# Apify Research Notes

Last checked: 2026-05-29.

## Current Decision

Do not run Apify actors automatically inside the main scheduled pipeline yet.

Reason: Apify actors can consume credits through actor-specific pricing plus platform usage. We can run controlled one-off experiments, but scheduled runs should only be enabled after choosing exact actor, input shape, result cap, and cadence.

The pipeline has a safety adapter:

```text
apify.enabled = false
APIFY_ENABLE_RUNS=false
```

Both must be deliberately changed before any actor execution is possible.

## How Apify Runs Work

Apify supports running actors/tasks via API:

- Async run: start an actor/task, then poll/fetch the dataset.
- Sync run: use a `run-sync` or `run-sync-get-dataset-items` endpoint for short runs.
- Actor IDs are usually written as `username~actor-name` in API URLs.
- Results are normally stored in the actor run's default dataset.

Useful docs:

- [Run actor and retrieve data](https://docs.apify.com/academy/api/run-actor-and-retrieve-data-via-api)
- [Actor tasks API](https://docs.apify.com/api/v2/actor-tasks)

## Pricing Rule Of Thumb

There are two cost layers:

1. Platform usage: compute units, storage, traffic, API operations.
2. Actor-specific pricing: many Store actors use pay-per-result or pay-per-event pricing.

For our use case, prefer pay-per-result actors with hard `maxItems`/`maxPosts` caps over compute-unit-only crawlers. It makes daily monitoring cost predictable.

## Candidate Actors

### X / Twitter Following List

Candidate actors:

- `api-ninja/x-twitter-followers-scraper`
- `scraperx/twitter-user-following-scraper`
- `brilliant_gum/twitter-followers-scraper`
- `patient_discovery/twitter-followings`
- `xtdata/twitter-x-user-info-scraper`

What current listings / tests say:

- `api-ninja/x-twitter-followers-scraper` works for `fu_yuming/following` and returns `screen_name`, `name`, `description`, `followers_count`, `friends_count`, `website`, etc.
- `scraply/twitter-user-following-scraper` returned 0 rows for `fu_yuming` / `https://x.com/fu_yuming`, even with Apify proxy enabled.
- Some actors can scrape following lists directly from a public username.
- Some actors explicitly require X cookies for following/followers mode, usually `ct0` and `auth_token`.
- Pricing varies by actor; several are pay-per-profile/result. We should test with a small `maxResults` before trusting cost or completeness.

Use case:

```text
Input: fu_yuming following list
Output: followed accounts with username, display name, bio, follower count, URL
Then classify AI/product relevance from bio + recent posts.
```

Completed experiment:

```text
target account: fu_yuming
actor: api-ninja/x-twitter-followers-scraper
input: urls=["fu_yuming/following"], maxResults=600, scrapeAllResults=false
cookies: not needed for this run
raw rows: 554
normalized unique accounts: 552
exports:
  data/exports/x_following_top100_latest.md
  data/exports/x_following_ai_seed_candidates_latest.md
```

Keep this as a controlled manual run for now. If we schedule it later, set a conservative cadence such as weekly, since the following list changes slowly.

### Product Hunt

`logiover/product-hunt-daily-launches-scraper`

Why it matters:

- Uses Product Hunt official GraphQL API.
- Supports daily launches, date ranges, topic filtering, votes, comments, topics, makers.
- Apify page states pricing from about `$3.50 / 1,000 results`.
- Has a built-in token option, but we already have a PH token, so direct API is cheaper and simpler for v1.

Decision:

- Do not use Apify for Product Hunt in v1 unless direct PH API becomes unreliable.
- Direct PH GraphQL is already implemented in `pipeline/run_pipeline.py`.

### X / Twitter Broad Search

`kaitoeasyapi/twitter-x-data-tweet-scraper-pay-per-result-cheapest`

Why it matters:

- Apify listing advertises pay-per-result pricing around `$0.25 / 1,000 tweets`.
- Search-style scraping can monitor keywords like `Claude Code`, `AI agent`, `OpenClaw`, `Hermes Agent`, `agent workflow`, etc.

Risks:

- Tweets are noisy; broad keyword search burns budget quickly.
- A raw tweet feed requires dedupe, author quality heuristics, and spam filtering.
- Terms and actor behavior can change; verify exact input schema before use.

Recommended first experiment:

```text
Run once per day.
Use 10-20 focused queries.
Hard cap: 50 tweets/query/day.
Total cap: <= 1,000 tweets/day.
```

At `$0.25 / 1,000 tweets`, this is cheap enough for a trial, but still controlled.

### X / Twitter Targeted Accounts

Chosen first actor:

```text
fastdata/twitter-scraper
```

Why:

- It supports user timelines directly through `twitterHandles`.
- It supports date filtering with `sinceDate`.
- It supports per-account caps through `maxTweetsPerAccount`.
- Store listing advertises pay-per-result pricing from about `$0.50 / 1,000 results`.
- A one-account test returned full tweet text, author, `createdAt`, likes, replies, retweets, quotes, views, bookmarks, URL, hashtags, mentions, media, and language.

Why it may be more effective:

- Lower noise than broad search.
- Better for "who noticed this early?" signals.
- Works well with a seed list of high-signal accounts.

Recommended first experiment:

```text
50 seed accounts.
Fetch latest 30 posts per account for the first cold start.
Hard cap: <= 1,500 posts/run.
```

This is probably more useful than broad keyword search for early product discovery, provided the account list is good.

Completed experiment:

```text
source accounts: top 50 AI-related personal accounts from fu_yuming following
input: twitterHandles=[...50 handles], maxTweetsPerAccount=30
raw rows: 1414
normalized tweets: 1414
mention aggregates: 0 for the current dashboard windows because the actor's newest returned tweet was 2025-11-18 while the dashboard run date is 2026-05-30.
exports:
  data/exports/x_tweets_latest.json
  data/exports/x_tweets_latest.md

Implementation note:

- Do not rely on this actor's `sinceDate` filter by default. In testing it appeared to fetch a per-account batch first and then apply the date filter, which can produce 0 rows for inactive accounts.
- Store actor output by `tweet_id` in SQLite (`x_tweets_store`) and compute 24h/7d/30d/30d+ dashboard windows locally from `created_at`.
```

## Recommended X Strategy

Use both, but staged:

1. Start with targeted accounts from `pipeline/config.json`.
2. Add broad search only for a small query set:
   - `"Claude Code" OR "coding agent"`
   - `"AI agent" "open source"`
   - `"personal AI assistant"`
   - `"browser agent"`
   - `"agent workflow"`
   - `"MCP" "agent"`
3. Keep daily caps low and inspect output quality manually for one week.
4. Only then schedule recurring runs.

Current seed accounts:

```text
SkylerMiao7
fortelabs
danshipper
karpathy
sama
DarioAmodei
```

Potential expansion:

```text
Scrape fu_yuming following list -> classify AI/product accounts -> add selected accounts to seed list.
```

This expansion now has a first-pass output in:

```text
data/exports/x_following_ai_seed_candidates_latest.md
```

## Budget Guardrails

Before enabling Apify:

- Pick exact actor ID.
- Save exact input JSON in `pipeline/config.json`.
- Set `max_results_per_run`.
- Set a daily run frequency.
- Log actor run ID, item count, and estimated cost when available.
- Refuse to run if `max_results_per_run` is missing.
