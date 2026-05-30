# Paradigm Radar - Product Spec v0.7

Internal tool version. This is for personal use first: no selling, no fundraising, no external users, no proof theater.

## 0. One Sentence

Crawl GitHub, HN, Reddit, Product Hunt, X, Hugging Face, and other useful channels; compute what can be computed; rank what is starting to move; automatically analyze the top few items for whether they may represent an application-layer paradigm shift.

Target: product/application-layer paradigm opportunities, not pure research or paper trends. Good examples are Claude Code, Hermes Agent, and OpenClaw-style changes in user behavior, workflow, and product interaction.

## 1. Shape

Four pieces:

```text
1. Aggregation layer
   Source adapters -> raw source items -> source-native facts + raw snapshots -> local storage.

2. Scoring pipeline
   Lightweight weighted ranking -> integrated recommendation list -> top 3 LLM analysis.

3. Settings + newsletter
   Simple local config -> scheduled digest to myself.

4. DeerFlow Agentic Search
   Ask for deeper investigation -> query local DB + call channel APIs live -> analyze with the same framework.
```

## 2. What Changed From v0.6

Cut or push to V2:

- Moat narrative.
- Heavy backtesting and ground truth.
- Prediction ledger.
- False-positive library.
- As-of-time rigor.
- Cross-platform entity graph.
- KOL five-factor quality model.
- Multi-tenant registration/auth.
- Six-stage lifecycle.
- Overly structured LLM output.

Keep:

- More data sources.
- Computable metrics.
- Recommendation ranking.
- Top 3 automatic analysis.
- Newsletter.
- DeerFlow agent with API tools.
- Historical snapshots, because seeing how something rose is directly useful.

## 3. Data Sources

First rule: prefer sources that already expose trending, ranking, deltas, or recent activity so we do not rebuild everything from raw events too early.

### Wave 1: no paid setup, can run now

| Source | Use | Current adapter |
|---|---|---|
| GitHub Trending HTML | Already curated daily/weekly/monthly repo momentum | `github_trending` |
| GitHub Search API | AI/dev/product repo discovery by query | `github_search` |
| HN Algolia | Search recent AI/product/app discussion | `hn_algolia` |
| HN Firebase | Top/new/best HN story snapshots | `hn_firebase` |
| Hugging Face Hub | Built-in model/dataset/space trendingScore | `huggingface_trending` |
| Product Hunt API v2 | Launch confirmation, votes, comments | `product_hunt` |
| OSSInsight API | Ideally GitHub trending + stargazer history | `ossinsight_trending_optional` |

OSSInsight is valuable because its public docs describe a no-auth trending endpoint and a 600 requests/hour IP limit, but live requests currently return HTTP 500 wrapping upstream 400/429 errors. It stays as an optional adapter and should not block the core pipeline.

### Wave 2: needs credentials or setup

| Source | Requirement | Why it matters |
|---|---|---|
| Reddit official API | Reddit app credentials; public JSON is currently blocked from this machine | Focused-community discussion velocity |
| X Post Counts / recent search | X developer bearer token, likely paid tier depending access | Topic discussion volume and early social ignition |
| Apify actors | Apify account/token and paid usage | Backup for X/Reddit/PH scraping when official APIs are too limited |

Current X shortcut: use Apify targeted account scraping first. The system takes the top AI-related accounts from the founder's following list, pulls recent tweets, and derives both single-tweet rows and mention aggregates for 24h / 7d / 30d.

## 4. Source Facts First

The first dashboard layer is not the derived scoring layer. It should show only
facts the source actually gives us, plus raw text and links:

```text
GitHub Trending: stars today / this week / this month, total stars, language, repo description.
Trending Repos / RepoFOMO: their own scores, velocity fields, new-star windows, ranks, sparkline.
HN: points, comments, author, created time, story text, HN item URL.
Product Hunt: votes, comments, daily/weekly rank, website, tagline.
X: who said what, what project/account was mentioned, original tweet URL.
```

Do not mix generic local `heat`, `velocity`, or `acceleration` into the source
dashboard. The database can keep legacy columns for compatibility, but the
dashboard/export contract should not expose them as source truth.

Derived scoring comes later as a separate candidate layer. That layer may use
snapshots to calculate velocity/acceleration, but it should be clearly labeled
as our calculation, not the source's native fact.

## 5. GitHub Star Calibration Anchors

These are not a formal backtest. They are calibration anchors for what "interesting movement" looks like.

| Anchor | Useful detection point | Too-late point |
|---|---|---|
| Claude Code | Around `500-800` stars, about `2.38 days` after repo creation; star velocity was about `92.33 stars/hour` | Waiting until it is already above many thousands of stars |
| Hermes Agent | Around the second-derivative lift at `3.4k-5.5k` stars; acceleration peak was about `0.6439 stars/hour^2` | Waiting until the later `23.3k-37.7k` first-derivative peak |
| OpenClaw | At or before the hours when GH Archive/Reddit/Product Hunt/HF all light up around `2026-01-25` to `2026-01-26` | Only detecting after `23.3k-37.7k` stars |

Takeaway for the later derived layer: first-derivative peak is often too late.
When we build candidate scoring, pay attention to acceleration and recent
velocity, not just total size.

## 6. Scoring

Internal tool scoring should be coarse and hand-adjustable. The first dashboard layer is deliberately not a cross-channel weighted score: it has channel tabs and shows each source's native facts. Weighted scoring belongs in the next recommendation pipeline layer.

Draft formula for the later recommendation layer:

```text
score =
  0.55 * acceleration_score
+ 0.30 * velocity_score
+ 0.15 * heat_score
+ 0.10 * source_trending_score
```

Where:

- `acceleration_score`: normalized positive acceleration.
- `velocity_score`: normalized source-provided or snapshot-derived velocity.
- `heat_score`: normalized current heat.
- `source_trending_score`: rank-derived bonus for sources that already provide trending rank.

Display the components, not just one opaque score. Do not let this formula hide the source-native dashboard facts.

## 7. Top 3 LLM Analysis

Only run LLM analysis on the top 3 items per digest.

Analysis framework:

```text
Product side:
  What new way does this let users do an old task?
  What workflow does it compress or replace?
  Who is the target user?
  Is this new behavior, or an old wrapper?

Technical side:
  Is there a real technical unlock?
  Does the product improve as models/agents/infra improve?
  Is it only a prompt wrapper?

Cross-channel synthesis:
  What does each channel say?
  Are people discussing the project, the category, or the behavior?
  Are signals consistent or contradictory?

Rough judgment:
  new paradigm / old paradigm new execution / clone / unclear
```

The output should be readable prose, not a large rigid schema.

## 8. Storage

Local SQLite is enough for v1.

Tables:

```text
snapshots   one row per source run
items       one row per raw source item per run
scores      ranked items for a run
analyses    future top-3 LLM outputs
settings    local config
```

Historical snapshots are kept because trend lines are useful. They are not maintained as a formal prediction ledger.

Settings is local-only in v1. It contains monitored X accounts, source/search
queries, and source health. Config changes are written to `pipeline/config.json`
and take effect on the next pipeline run. The default cadence is one full run
every 24 hours, but cron is not enabled yet.

## 9. Execution Order

```text
Step 1  Wire Wave 1 sources and write local snapshots. [done]
Step 2  Source-tab dashboard: native facts only, no cross-channel weighting. [done]
Step 3  Add targeted X tweets / mention aggregates through Apify. [done]
Step 4  Add local backend for reading/writing config and manual run trigger. [done]
Step 5  Export daily markdown digest.
Step 6  Add top-3 DeepSeek analysis.
Step 7  Add newsletter delivery.
Step 8  Add Reddit / official X search if useful.
Step 9  Add DeerFlow MCP/skills for live deep-dive search.
```

## 10. API Ask From Founder

Not required to start:

- GitHub token: free, strongly recommended for rate limits.
- Product Hunt developer token: already available for current local run.
- Apify token: already available for manual X following and X tweets runs.

Useful next:

- Reddit app credentials.
- X developer bearer token, only if pricing/access is acceptable.
