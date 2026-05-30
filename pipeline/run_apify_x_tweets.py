#!/usr/bin/env python3
"""Scrape recent tweets from the top AI-related X accounts through Apify.

This is intentionally separate from the main pipeline because actor runs spend
credits. The main pipeline only reads the exported `x_tweets_latest.json`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import html
import json
import os
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from run_pipeline import DB_PATH, init_db, is_ai_related_x_seed, is_personal_x_seed, upsert_x_tweet_rows, x_cursor_since_date


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "pipeline" / "config.json"
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw" / "apify"
EXPORT_DIR = DATA_DIR / "exports"
USER_AGENT = "hero-radar-local/0.1"

WINDOW_HOURS = {"24h": 24.0, "7d": 7 * 24.0, "30d": 30 * 24.0}
TERM_PROJECTS = [
    "Claude Code",
    "Claude",
    "Anthropic",
    "OpenAI",
    "ChatGPT",
    "Codex",
    "Cursor",
    "Windsurf",
    "Devin",
    "Lovable",
    "Replit",
    "Perplexity",
    "Manus",
    "Grok",
    "Gemini",
    "Veo",
    "Sora",
    "DeepSeek",
    "Hugging Face",
    "LangChain",
    "LlamaIndex",
    "MCP",
    "Vercel",
    "Supabase",
    "n8n",
    "Composio",
    "ElevenLabs",
    "Midjourney",
    "Runway",
    "Pika",
    "Warp",
    "Sourcegraph",
]


def load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(ts: dt.datetime) -> str:
    return ts.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text())


def actor_url(actor_id: str) -> str:
    actor_path = urllib.parse.quote(actor_id.replace("/", "~"), safe="")
    return f"https://api.apify.com/v2/acts/{actor_path}/run-sync-get-dataset-items"


def read_seed_accounts(config: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    settings = config.get("apify", {}).get("x_seed_from_following", {})
    candidate_path = ROOT / str(settings.get("candidate_file", "data/exports/x_following_ai_seed_candidates_latest.json"))
    rows: list[dict[str, Any]] = []
    if candidate_path.exists():
        data = json.loads(candidate_path.read_text())
        rows = [row for row in data.get("items", []) if isinstance(row, dict)]
        rows = [row for row in rows if int(row.get("followers_count") or 0) > 0]
        rows = [row for row in rows if is_ai_related_x_seed(row) and is_personal_x_seed(row)]
        rows.sort(key=lambda row: int(row.get("followers_count") or 0), reverse=True)

    if not rows:
        rows = [
            {
                "username": handle,
                "name": handle,
                "followers_count": 0,
                "url": f"https://x.com/{handle}",
            }
            for handle in config.get("apify", {}).get("x_seed_accounts", [])
        ]

    seen: set[str] = set()
    selected: list[dict[str, Any]] = []
    for row in rows:
        username = str(row.get("username") or "").lstrip("@")
        if not username or username.lower() in seen:
            continue
        seen.add(username.lower())
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected


def actor_flavor(actor_id: str) -> str:
    if actor_id.startswith("kaitoeasyapi/"):
        return "kaito"
    if actor_id.startswith("apidojo/"):
        return "apidojo"
    return "fastdata"


def since_date_to_datetime(value: str | None, fallback_days: int) -> dt.datetime:
    if value:
        try:
            return dt.datetime.fromisoformat(value).replace(tzinfo=dt.timezone.utc)
        except Exception:
            pass
    return utc_now() - dt.timedelta(days=fallback_days)


def build_input(
    config: dict[str, Any],
    actor_id: str,
    handles: list[str],
    *,
    since_days: int,
    per_account: int,
    since_date: str | None = None,
) -> dict[str, Any]:
    settings = config.get("apify", {}).get("x_tweets", {})
    since_date = since_date or (utc_now() - dt.timedelta(days=since_days)).date().isoformat()
    include_replies = bool(settings.get("include_replies", False))
    include_retweets = bool(settings.get("include_retweets", True))
    flavor = actor_flavor(actor_id)
    if flavor == "kaito":
        since_dt = since_date_to_datetime(since_date, since_days)
        until_dt = utc_now() + dt.timedelta(hours=1)
        filters: list[str] = []
        if not include_replies:
            filters.append("-filter:replies")
        if not include_retweets:
            filters.append("-filter:nativeretweets")
        filter_text = (" " + " ".join(filters)) if filters else ""
        return {
            "searchTerms": [
                f"from:{handle.lstrip('@')}{filter_text} since_time:{int(since_dt.timestamp())} until_time:{int(until_dt.timestamp())}"
                for handle in handles
            ],
            "maxItems": max(20, per_account),
        }
    if flavor == "apidojo":
        return {
            "twitterHandles": [handle.lstrip("@") for handle in handles],
            "maxItems": max(50, len(handles) * per_account),
            "sort": "Latest",
        }
    payload: dict[str, Any] = {
        "twitterHandles": handles,
        "mode": "tweets",
        "maxTweets": len(handles) * per_account,
        "maxTweetsPerAccount": per_account,
        "includeReplies": include_replies,
        "includeRetweets": include_retweets,
        "deduplicate": True,
    }
    if bool(settings.get("use_since_date_filter", False)):
        payload["sinceDate"] = since_date
    language = settings.get("language")
    if language:
        payload["language"] = language
    return payload


def redacted(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload))


def run_actor(actor_id: str, payload: dict[str, Any], token: str, timeout_seconds: int) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"token": token, "timeout": str(timeout_seconds)})
    url = f"{actor_url(actor_id)}?{params}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds + 30) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return []


def fetch_dataset_items(dataset_id: str, token: str, *, limit: int = 10000) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"token": token, "clean": "true", "limit": str(limit)})
    req = urllib.request.Request(
        f"https://api.apify.com/v2/datasets/{urllib.parse.quote(dataset_id, safe='')}/items?{params}",
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def fetch_kv_json(store_id: str, key: str, token: str) -> dict[str, Any] | None:
    req = urllib.request.Request(
        f"https://api.apify.com/v2/key-value-stores/{urllib.parse.quote(store_id, safe='')}/records/{urllib.parse.quote(key, safe='')}?"
        + urllib.parse.urlencode({"token": token}),
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def fetch_run(run_id: str, token: str) -> dict[str, Any] | None:
    req = urllib.request.Request(
        f"https://api.apify.com/v2/actor-runs/{urllib.parse.quote(run_id, safe='')}?"
        + urllib.parse.urlencode({"token": token}),
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None
    item = data.get("data") if isinstance(data, dict) else None
    return item if isinstance(item, dict) else None


def parse_tweet_time(value: Any) -> dt.datetime | None:
    if not value:
        return None
    text = str(value)
    try:
        parsed = email.utils.parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except Exception:
        pass
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except Exception:
        return None


def number(row: dict[str, Any], key: str) -> int:
    value = row.get(key)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        digits = re.sub(r"[^\d]", "", value)
        return int(digits) if digits else 0
    return 0


def author_username(row: dict[str, Any]) -> str:
    author = row.get("author")
    if isinstance(author, dict):
        username = author.get("username") or author.get("userName") or author.get("screenName") or author.get("handle")
        if username:
            return str(username).lstrip("@")
    username = row.get("username") or row.get("authorUsername")
    if username:
        return str(username).lstrip("@")
    url = str(row.get("url") or row.get("twitterUrl") or "")
    match = re.search(r"(?:x|twitter)\.com/([^/]+)/status/", url)
    return match.group(1).lstrip("@") if match else ""


def normalize_list(values: Any, *keys: str) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        if isinstance(value, str):
            text = value
        elif isinstance(value, dict):
            text = ""
            for key in keys:
                if value.get(key):
                    text = str(value[key])
                    break
        else:
            continue
        text = text.strip().lstrip("@")
        if text:
            out.append(text)
    return out


def tweet_mentions(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    values.extend(normalize_list(row.get("mentions"), "username", "screenName", "handle", "name"))
    entities = row.get("entities") if isinstance(row.get("entities"), dict) else {}
    values.extend(normalize_list(entities.get("user_mentions"), "screen_name", "username", "screenName", "name"))
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        lowered = value.lower()
        if lowered and lowered not in seen:
            seen.add(lowered)
            out.append(value)
    return out


def tweet_hashtags(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    values.extend(normalize_list(row.get("hashtags"), "text", "tag"))
    entities = row.get("entities") if isinstance(row.get("entities"), dict) else {}
    values.extend(normalize_list(entities.get("hashtags"), "text", "tag"))
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        lowered = value.lower()
        if lowered and lowered not in seen:
            seen.add(lowered)
            out.append(value)
    return out


def tweet_urls(text: str, row: dict[str, Any]) -> list[str]:
    urls = [match.group(0).rstrip(".,;:!?") for match in re.finditer(r"https?://[^\s)>\"]+", text or "")]
    entities = row.get("entities") if isinstance(row.get("entities"), dict) else {}
    for value in entities.get("urls") or []:
        if not isinstance(value, dict):
            continue
        url = value.get("expanded_url") or value.get("unwound_url") or value.get("url")
        if url:
            urls.append(str(url).rstrip(".,;:!?"))
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        lowered = url.lower()
        if lowered and lowered not in seen:
            seen.add(lowered)
            out.append(url)
    return out


def add_project(projects: list[dict[str, Any]], seen: set[str], *, kind: str, key: str, name: str, url: str = "") -> None:
    normalized_key = key.lower()
    if not normalized_key or normalized_key in seen:
        return
    seen.add(normalized_key)
    projects.append({"kind": kind, "key": key, "name": name, "url": url})


def extract_projects(text: str, row: dict[str, Any], author: str) -> list[dict[str, Any]]:
    clean = html.unescape(text or "")
    projects: list[dict[str, Any]] = []
    seen: set[str] = set()

    for handle in tweet_mentions(row):
        if handle.lower() == author.lower():
            continue
        add_project(projects, seen, kind="x_handle", key=f"@{handle}", name=f"@{handle}", url=f"https://x.com/{handle}")

    for tag in tweet_hashtags(row):
        add_project(projects, seen, kind="hashtag", key=f"#{tag}", name=f"#{tag}")

    for url in tweet_urls(clean, row):
        host = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
        if not host or host in {"x.com", "twitter.com", "t.co", "pic.twitter.com"}:
            continue
        github_match = re.search(r"github\.com/([^/\s]+)/([^/\s#?]+)", url, flags=re.I)
        if github_match:
            repo = f"{github_match.group(1)}/{github_match.group(2).removesuffix('.git')}"
            add_project(projects, seen, kind="github_repo", key=f"github:{repo}", name=repo, url=f"https://github.com/{repo}")
        else:
            add_project(projects, seen, kind="domain", key=f"domain:{host}", name=host, url=f"https://{host}")

    lowered = clean.lower()
    for term in TERM_PROJECTS:
        if re.search(rf"(?<![\w@#]){re.escape(term.lower())}(?![\w-])", lowered):
            key = f"term:{term.lower()}"
            add_project(projects, seen, kind="known_term", key=key, name=term)

    return projects


def tweet_windows(created_at: dt.datetime, now: dt.datetime) -> tuple[list[str], float]:
    age_hours = max((now - created_at).total_seconds() / 3600.0, 0.0)
    windows = [window for window, hours in WINDOW_HOURS.items() if age_hours <= hours]
    return windows, age_hours


def normalize_tweets(
    rows: list[dict[str, Any]],
    now: dt.datetime,
    *,
    include_replies: bool = True,
    include_retweets: bool = True,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        tweet_id = str(row.get("id") or row.get("tweetId") or row.get("tweet_id") or "")
        if not tweet_id or tweet_id in seen:
            continue
        seen.add(tweet_id)
        created = parse_tweet_time(row.get("createdAt") or row.get("created_at"))
        if not created:
            continue
        windows, age_hours = tweet_windows(created, now)
        is_reply = bool(row.get("isReply") or row.get("inReplyToId") or row.get("inReplyToUsername"))
        is_retweet = bool(row.get("isRetweet") or row.get("retweeted_tweet") or row.get("retweeted_tweet_results"))
        if is_reply and not include_replies:
            continue
        if is_retweet and not include_retweets:
            continue
        author = author_username(row)
        text = html.unescape(str(row.get("text") or row.get("fullText") or ""))
        metrics = {
            "likes": number(row, "likeCount"),
            "replies": number(row, "replyCount"),
            "retweets": number(row, "retweetCount"),
            "quotes": number(row, "quoteCount"),
            "views": number(row, "viewCount"),
            "bookmarks": number(row, "bookmarkCount"),
        }
        engagement = metrics["likes"] + metrics["replies"] + metrics["retweets"] + metrics["quotes"]
        normalized.append(
            {
                "id": tweet_id,
                "url": row.get("url") or (f"https://x.com/{author}/status/{tweet_id}" if author else ""),
                "text": text,
                "author_username": author,
                "author_name": (
                    (row.get("author") or {}).get("displayName") or (row.get("author") or {}).get("name")
                    if isinstance(row.get("author"), dict)
                    else ""
                ),
                "created_at": iso(created),
                "age_hours": round(age_hours, 2),
                "windows": windows,
                "metrics": metrics,
                "engagement": engagement,
                "language": row.get("language") or row.get("lang"),
                "is_reply": is_reply,
                "is_retweet": is_retweet,
                "hashtags": tweet_hashtags(row),
                "mentions": tweet_mentions(row),
                "mentioned_projects": extract_projects(text, row, author),
                "raw": row,
            }
        )
    normalized.sort(key=lambda item: item["created_at"], reverse=True)
    return normalized


def cap_tweets_per_author(tweets: list[dict[str, Any]], per_author: int | None) -> list[dict[str, Any]]:
    if per_author is None or per_author <= 0:
        return tweets
    counts: dict[str, int] = {}
    capped: list[dict[str, Any]] = []
    for tweet in tweets:
        author = str(tweet.get("author_username") or "").lower()
        counts[author] = counts.get(author, 0) + 1
        if counts[author] <= per_author:
            capped.append(tweet)
    return capped


def aggregate_mentions(tweets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for tweet in tweets:
        for project in tweet.get("mentioned_projects") or []:
            if not isinstance(project, dict):
                continue
            for window in tweet.get("windows") or []:
                bucket_key = (str(window), str(project.get("key") or project.get("name")))
                bucket = buckets.setdefault(
                    bucket_key,
                    {
                        "window": window,
                        "kind": project.get("kind"),
                        "key": project.get("key"),
                        "name": project.get("name"),
                        "url": project.get("url") or "",
                        "mention_count": 0,
                        "tweet_count": 0,
                        "engagement_sum": 0,
                        "view_sum": 0,
                        "accounts": set(),
                        "sample_tweets": [],
                    },
                )
                bucket["mention_count"] += 1
                bucket["tweet_count"] += 1
                bucket["engagement_sum"] += int(tweet.get("engagement") or 0)
                bucket["view_sum"] += int((tweet.get("metrics") or {}).get("views") or 0)
                if tweet.get("author_username"):
                    bucket["accounts"].add(tweet["author_username"])
                if len(bucket["sample_tweets"]) < 5:
                    bucket["sample_tweets"].append(
                        {
                            "id": tweet.get("id"),
                            "url": tweet.get("url"),
                            "author": tweet.get("author_username"),
                            "created_at": tweet.get("created_at"),
                            "text": tweet.get("text"),
                            "engagement": tweet.get("engagement"),
                        }
                    )

    rows: list[dict[str, Any]] = []
    for bucket in buckets.values():
        row = dict(bucket)
        row["accounts"] = sorted(row["accounts"])
        rows.append(row)
    rows.sort(
        key=lambda row: (
            {"24h": 0, "7d": 1, "30d": 2}.get(str(row["window"]), 99),
            -int(row["tweet_count"]),
            -int(row["engagement_sum"]),
            str(row["name"] or ""),
        )
    )
    return rows


def export(
    run_id: str,
    actor_id: str,
    payload: dict[str, Any],
    seed_accounts: list[dict[str, Any]],
    raw_rows: list[dict[str, Any]],
    *,
    include_replies: bool = True,
    include_retweets: bool = True,
    max_tweets_per_author: int | None = None,
    max_newest_age_hours: float | None = None,
) -> bool:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    now = utc_now()
    tweets = normalize_tweets(raw_rows, now, include_replies=include_replies, include_retweets=include_retweets)
    tweets = cap_tweets_per_author(tweets, max_tweets_per_author)

    raw_path = RAW_DIR / f"x_tweets_{run_id}.json"
    raw_path.write_text(json.dumps(raw_rows, ensure_ascii=False, indent=2))

    newest_created = max((parse_tweet_time(tweet.get("created_at")) for tweet in tweets), default=None)
    newest_age_hours = None
    if newest_created:
        newest_age_hours = max((now - newest_created).total_seconds() / 3600.0, 0.0)
    if max_newest_age_hours is not None and (newest_age_hours is None or newest_age_hours > max_newest_age_hours):
        rejected_data = {
            "run_id": run_id,
            "actor_id": actor_id,
            "fetched_at": iso(now),
            "input": redacted(payload),
            "seed_accounts": seed_accounts,
            "raw_rows": len(raw_rows),
            "normalized_tweets": len(tweets),
            "include_replies": include_replies,
            "include_retweets": include_retweets,
            "max_tweets_per_author": max_tweets_per_author,
            "newest_created_at": iso(newest_created) if newest_created else None,
            "newest_age_hours": round(newest_age_hours, 2) if newest_age_hours is not None else None,
            "max_newest_age_hours": max_newest_age_hours,
            "reason": "rejected_stale_x_tweets",
            "raw_path": str(raw_path.relative_to(ROOT)),
        }
        rejected_path = EXPORT_DIR / f"x_tweets_rejected_{run_id}.json"
        rejected_path.write_text(json.dumps(rejected_data, ensure_ascii=False, indent=2))
        print(rejected_path)
        print(
            "Rejected stale X tweets: "
            f"raw rows: {len(raw_rows)}; normalized tweets: {len(tweets)}; "
            f"newest: {rejected_data['newest_created_at']}; "
            f"newest_age_hours: {rejected_data['newest_age_hours']}; "
            f"limit: {max_newest_age_hours}; raw: {raw_path}"
        )
        return False

    mentions = aggregate_mentions(tweets)
    conn = sqlite3.connect(DB_PATH)
    try:
        init_db(conn)
        conn.execute("delete from x_tweets_store where last_import_run_id = ?", (run_id,))
        upsert_x_tweet_rows(conn, tweets, fetched_at=iso(now), import_run_id=run_id)
    finally:
        conn.close()

    export_data = {
        "run_id": run_id,
        "actor_id": actor_id,
        "fetched_at": iso(now),
        "input": redacted(payload),
        "seed_accounts": seed_accounts,
        "include_replies": include_replies,
        "include_retweets": include_retweets,
        "max_tweets_per_author": max_tweets_per_author,
        "items": tweets,
        "mentions": mentions,
    }
    latest_path = EXPORT_DIR / "x_tweets_latest.json"
    latest_path.write_text(json.dumps(export_data, ensure_ascii=False, indent=2))

    lines = [
        "# X Tweets Latest",
        "",
        f"- Run: `{run_id}`",
        f"- Actor: `{actor_id}`",
        f"- Raw rows: `{len(raw_rows)}`",
        f"- Normalized tweets: `{len(tweets)}`",
        f"- Mention aggregates: `{len(mentions)}`",
        "",
        "## Mention Aggregates",
        "",
        "| Window | Mentions | Engagement | Accounts | Mention | Sample |",
        "|---|---:|---:|---|---|---|",
    ]
    for row in mentions[:80]:
        sample = row.get("sample_tweets", [{}])[0] if row.get("sample_tweets") else {}
        sample_text = str(sample.get("text") or "").replace("|", "\\|").replace("\n", " ")[:180]
        accounts = ", ".join(f"@{a}" for a in row.get("accounts", [])[:5])
        name = str(row.get("name") or "").replace("|", "\\|")
        lines.append(
            f"| `{row.get('window')}` | {row.get('tweet_count')} | {row.get('engagement_sum')} | {accounts} | {name} | {sample_text} |"
        )
    lines.extend(["", "## Recent Tweets", "", "| Created | Author | Engagement | Text |", "|---|---|---:|---|"])
    for row in tweets[:80]:
        text = str(row.get("text") or "").replace("|", "\\|").replace("\n", " ")[:220]
        lines.append(f"| `{row.get('created_at')}` | @{row.get('author_username')} | {row.get('engagement')} | {text} |")
    (EXPORT_DIR / "x_tweets_latest.md").write_text("\n".join(lines) + "\n")

    print(latest_path)
    print(f"Raw rows: {len(raw_rows)}; normalized tweets: {len(tweets)}; mention aggregates: {len(mentions)}; raw: {raw_path}")
    return True


def main() -> int:
    load_dotenv()
    config = read_config()
    settings = config.get("apify", {}).get("x_tweets", {})
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="Actually run the Apify actor. Otherwise prints a dry run.")
    parser.add_argument("--accounts", type=int, default=int(settings.get("accounts_limit", 50)))
    parser.add_argument("--per-account", type=int, default=int(settings.get("max_tweets_per_account", 20)))
    parser.add_argument("--since-days", type=int, default=30)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--dataset-id", help="Import an existing Apify dataset without starting a new actor run.")
    parser.add_argument("--apify-run-id", help="Optional Apify run id; used to recover original INPUT and stable run metadata.")
    args = parser.parse_args()

    actor_id = str(settings.get("actor_id") or "fastdata/twitter-scraper")
    seed_accounts = read_seed_accounts(config, args.accounts)
    handles = [str(row.get("username") or "").lstrip("@") for row in seed_accounts]
    since_date = x_cursor_since_date(handles, fallback_days=args.since_days, safety_days=2)
    payload = build_input(config, actor_id, handles, since_days=args.since_days, per_account=args.per_account, since_date=since_date)

    if not args.run and not args.dataset_id:
        print("Dry run. No Apify credits used.")
        print(json.dumps({"actor_id": actor_id, "input": redacted(payload)}, ensure_ascii=False, indent=2))
        print("Run with `APIFY_ENABLE_RUNS=true python3 pipeline/run_apify_x_tweets.py --run`.")
        return 0

    if args.dataset_id:
        token = os.environ.get("APIFY_TOKEN")
        if not token:
            print("Refusing to import dataset: APIFY_TOKEN is not set.", file=sys.stderr)
            return 2
        raw_rows = fetch_dataset_items(args.dataset_id, token)
        run = fetch_run(args.apify_run_id, token) if args.apify_run_id else None
        if run:
            actor_id = str(run.get("actId") or actor_id)
            input_payload = fetch_kv_json(str(run.get("defaultKeyValueStoreId") or ""), "INPUT", token) or payload
            started_at = parse_tweet_time(run.get("startedAt")) or utc_now()
            run_id = iso(started_at).replace(":", "").replace("-", "")
        else:
            input_payload = payload
            run_id = iso(utc_now()).replace(":", "").replace("-", "")
        freshness_hours = settings.get("freshness_max_age_hours", 720)
        max_newest_age_hours = None if freshness_hours in (None, "", False) else float(freshness_hours)
        ok = export(
            run_id,
            actor_id,
            input_payload,
            seed_accounts,
            raw_rows,
            include_replies=bool(settings.get("include_replies", False)),
            include_retweets=bool(settings.get("include_retweets", True)),
            max_tweets_per_author=args.per_account,
            max_newest_age_hours=max_newest_age_hours,
        )
        return 0 if ok else 3

    if os.environ.get("APIFY_ENABLE_RUNS", "").lower() != "true":
        print("Refusing to run: APIFY_ENABLE_RUNS is not true.", file=sys.stderr)
        return 2
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        print("Refusing to run: APIFY_TOKEN is not set.", file=sys.stderr)
        return 2
    if not handles:
        print("Refusing to run: no seed handles found.", file=sys.stderr)
        return 2

    run_id = iso(utc_now()).replace(":", "").replace("-", "")
    raw_rows = run_actor(actor_id, payload, token, args.timeout)
    freshness_hours = settings.get("freshness_max_age_hours", 36)
    max_newest_age_hours = None if freshness_hours in (None, "", False) else float(freshness_hours)
    ok = export(
        run_id,
        actor_id,
        payload,
        seed_accounts,
        raw_rows,
        include_replies=bool(settings.get("include_replies", False)),
        include_retweets=bool(settings.get("include_retweets", True)),
        max_tweets_per_author=args.per_account,
        max_newest_age_hours=max_newest_age_hours,
    )
    if not ok:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
