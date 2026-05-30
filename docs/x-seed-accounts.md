# X Seed Accounts

Current seed list for targeted X monitoring:

| Handle | Person / source | Notes |
|---|---|---|
| `SkylerMiao7` | Skyler Miao | User-provided. Third-party X profile indexes also list this handle. |
| `fortelabs` | Tiago Forte / Forte Labs | User-provided. Useful for workflow, knowledge work, taste/productivity signals. |
| `danshipper` | Dan Shipper | User-provided. Useful for AI product/operator signals via Every. |
| `karpathy` | Andrej Karpathy | AI/product paradigm signal, especially coding agents and model behavior. |
| `sama` | Sam Altman | Frontier AI and product/platform signal. |
| `DarioAmodei` | Dario Amodei | Anthropic/frontier AI signal. |

## Following-List Expansion

Target:

```text
https://x.com/fu_yuming/following
```

Likely constraint:

- X following pages are usually login-gated for complete access.
- Apify following-list actors may need X cookies (`ct0`, `auth_token`) for reliable output.
- Some actors advertise no-login following scrape; test with a hard cap before relying on them.
- The tested no-login actor `api-ninja/x-twitter-followers-scraper` successfully returned the full `fu_yuming/following` list.

Expansion method:

```text
1. Scrape up to 600 followed accounts from fu_yuming.
2. Keep username, display name, bio, follower count, website.
3. Classify AI/product relevance by keywords first.
4. Optional LLM pass for ambiguous accounts.
5. Add selected accounts to `apify.x_seed_accounts`.
```

Implemented script:

```bash
python3 pipeline/run_apify_x_following.py
```

Default behavior is dry-run. It does not call Apify or spend credits.

To run a real scrape:

```bash
APIFY_ENABLE_RUNS=true python3 pipeline/run_apify_x_following.py --run --max-results 600 --top 100
```

The script writes:

```text
data/exports/x_following_top100_latest.json
data/exports/x_following_top100_latest.md
data/exports/x_following_ai_seed_candidates_latest.json
data/exports/x_following_ai_seed_candidates_latest.md
```

The account currently follows about 554 accounts, so `--max-results 600` should cover the full following list and produce the top 100 by follower count across all followed accounts.

Latest completed run:

```text
raw rows: 554
normalized unique accounts: 552
top-100 ranking: followers_count
AI/product seed candidates: simple keyword score + followers_count
```

Initial keyword classifier:

```text
ai, llm, agent, agents, claude, openai, anthropic, cursor, coding,
developer tools, devtools, mcp, workflow, automation, product, founder,
builder, research, ml, machine learning
```
