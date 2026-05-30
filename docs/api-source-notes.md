# API Source Notes

Last checked: 2026-05-29.

## Works Without Credentials

- GitHub Trending HTML: `https://github.com/trending?since=daily`
- GitHub Search API, unauthenticated but rate-limited: `https://api.github.com/search/repositories`
- HN Algolia: `https://hn.algolia.com/api`
- HN Firebase: `https://hacker-news.firebaseio.com/v0`
- Hugging Face Hub API: `https://huggingface.co/api/models?sort=trendingScore`

## Works Better With Free Token

- GitHub REST API. Unauthenticated limits observed from this machine:
  - Search: 10 requests/minute.
  - Core: 60 requests/hour.
  A `GITHUB_TOKEN` is not paid and should be added for regular runs.
- Product Hunt API v2. Uses GraphQL at `https://api.producthunt.com/v2/api/graphql` with a bearer token. The pipeline now supports direct PH collection via `PRODUCTHUNT_TOKEN`.
  - The initial full-field query exceeded PH's GraphQL complexity limit, so v1 intentionally keeps the query small: name, slug, tagline, Product Hunt URL, website, votes, comments, daily/weekly rank, created/featured time.

## Currently Flaky

OSSInsight public docs state:

- Base URL: `https://api.ossinsight.io/v1`
- No authentication required.
- Per-IP limit: 600 requests/hour.
- Trending endpoint example: `GET /v1/trends/repos/?period=past_24_hours&language=All`

Live checks from this machine returned HTTP 500 with wrapped upstream messages:

- `{"message":"Request failed with status code 400"}` for `language=All`.
- `{"message":"Request failed with status code 429"}` for `language=Python`.

Conclusion: keep OSSInsight as optional, not blocking.

## OSSInsight Alternatives

Current replacement:

- GitHub Trending HTML is already implemented directly. It gives daily / weekly / monthly trending repos and the important `stars gained in this period` number, which is the practical field we wanted from OSSInsight.
- GitHub Search API is already implemented for broader discovery, then local snapshots provide velocity/acceleration once we have repeated runs.

Useful but not a drop-in replacement:

- Ecosyste.ms Timeline: large GitHub event corpus and repo-level history/enrichment. Good for targeted backfill on selected repos, but not a ready-made "trending repos" endpoint.
- GH Archive / BigQuery: authoritative historical event source, but heavier than needed for v1 daily usage.

External references / fallback scrapers:

- Trendshift and HubLens are useful product references for momentum ranking / summarized OSS discovery, but should be treated as reference surfaces unless their APIs prove stable enough.
- Apify has several GitHub Trending actors that return enriched rows with rank, stars gained this period, topics, README/API enrichment, etc. Since our local GitHub Trending scraper currently works for free, use Apify only as fallback or enrichment, not the default.

## Requires Credentials / Payment Decision

- Reddit API: app credentials needed. Public subreddit JSON returned HTTP 403 from this machine.
- X API: bearer token needed; Post Counts / recent search access depends on current X plan.
- Apify: token and paid actor usage if we use X/Reddit/PH scrapers.
  - `api-ninja/x-twitter-followers-scraper` successfully scraped `fu_yuming/following` with no X cookies in a capped manual run.
  - Keep Apify disabled in the main scheduled pipeline until each recurring actor has a hard cap and cadence.

## Local Secret Handling

Secrets are read from environment variables or `.env`.

Use:

```text
GITHUB_TOKEN
PRODUCTHUNT_TOKEN
PRODUCTHUNT_USER_CONTEXT
APIFY_TOKEN
APIFY_ENABLE_RUNS
```

`.env` is intentionally ignored by git. Do not paste tokens into tracked docs or config files.
