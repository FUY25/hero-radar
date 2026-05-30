#!/usr/bin/env python3
"""Tiny Apify X actor freshness smoke test.

This intentionally runs very small jobs before any 50x30 scrape. The pass/fail
criterion is not "did it return rows"; it is whether the newest returned tweet
looks like a current timeline result.
"""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw" / "apify_smoke"
EXPORT_DIR = DATA_DIR / "exports"
USER_AGENT = "hero-radar-local/0.1"

PRESETS = {
    "kaito": "kaitoeasyapi/twitter-x-data-tweet-scraper-pay-per-result-cheapest",
    "apidojo": "apidojo/tweet-scraper",
    "fastdata": "fastdata/twitter-scraper",
}


def load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(ts: dt.datetime) -> str:
    return ts.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def actor_url(actor_id: str) -> str:
    actor_path = urllib.parse.quote(actor_id.replace("/", "~"), safe="")
    return f"https://api.apify.com/v2/acts/{actor_path}/run-sync-get-dataset-items"


def run_actor(actor_id: str, payload: dict[str, Any], token: str, timeout_seconds: int) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"token": token, "timeout": str(timeout_seconds), "clean": "true"})
    req = urllib.request.Request(
        f"{actor_url(actor_id)}?{params}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds + 30) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def parse_time(value: Any) -> dt.datetime | None:
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


def nested(row: dict[str, Any], *path: str) -> Any:
    cur: Any = row
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    tweet_id = row.get("id") or row.get("tweetId") or row.get("tweet_id") or row.get("rest_id")
    author = (
        row.get("author_username")
        or row.get("username")
        or row.get("authorUsername")
        or nested(row, "author", "username")
        or nested(row, "author", "screenName")
        or nested(row, "user", "username")
        or nested(row, "user", "screenName")
    )
    created_raw = (
        row.get("created_at")
        or row.get("createdAt")
        or row.get("created_at_text")
        or row.get("timestamp")
        or row.get("date")
    )
    created = parse_time(created_raw)
    text = row.get("text") or row.get("fullText") or row.get("tweetText") or row.get("content") or ""
    url = row.get("url") or row.get("tweetUrl") or row.get("twitterUrl") or ""
    if not author and url:
        match = re.search(r"(?:x|twitter)\.com/([^/]+)/status/", str(url))
        if match:
            author = match.group(1)
    if not url and author and tweet_id:
        url = f"https://x.com/{str(author).lstrip('@')}/status/{tweet_id}"
    return {
        "tweet_id": str(tweet_id or ""),
        "author": str(author or "").lstrip("@"),
        "created_at": iso(created) if created else None,
        "text": str(text),
        "url": str(url),
    }


def build_payload(
    actor_key: str,
    actor_id: str,
    handles: list[str],
    per_account: int,
    days: int,
    *,
    include_replies: bool = False,
) -> dict[str, Any]:
    since = utc_now() - dt.timedelta(days=days)
    until = utc_now() + dt.timedelta(hours=1)
    since_date = since.date().isoformat()
    since_time = int(since.timestamp())
    until_time = int(until.timestamp())
    handles = [handle.lstrip("@") for handle in handles]

    if actor_key == "kaito" or actor_id.startswith("kaitoeasyapi/"):
        # The actor readme says since/until text filters are unreliable and
        # recommends since_time / until_time UNIX filters.
        reply_filter = "" if include_replies else " -filter:replies"
        terms = [f"from:{handle}{reply_filter} since_time:{since_time} until_time:{until_time}" for handle in handles]
        return {"searchTerms": terms, "maxItems": max(20, per_account)}
    if actor_key == "apidojo" or actor_id.startswith("apidojo/"):
        return {
            "twitterHandles": handles,
            "maxItems": max(50, len(handles) * per_account),
            "sort": "Latest",
        }
    return {
        "twitterHandles": handles,
        "mode": "tweets",
        "maxTweets": len(handles) * per_account,
        "maxTweetsPerAccount": per_account,
        "includeReplies": False,
        "includeRetweets": True,
        "deduplicate": True,
    }


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--actor", default="kaito", help="Preset key or full actor id.")
    parser.add_argument("--handles", default="dotey,danshipper", help="Comma-separated X handles.")
    parser.add_argument("--per-account", type=int, default=5)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--freshness-hours", type=float, default=24 * 30)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--include-replies", action="store_true")
    parser.add_argument("--run", action="store_true", help="Actually run the actor. Without this, print payload only.")
    args = parser.parse_args()

    token = os.environ.get("APIFY_TOKEN")
    if args.run and not token:
        raise SystemExit("APIFY_TOKEN is not set")

    actor_id = PRESETS.get(args.actor, args.actor)
    actor_key = args.actor if args.actor in PRESETS else actor_id.split("/", 1)[0]
    handles = [part.strip().lstrip("@") for part in args.handles.split(",") if part.strip()]
    payload = build_payload(
        actor_key,
        actor_id,
        handles,
        args.per_account,
        args.days,
        include_replies=args.include_replies,
    )
    run_id = iso(utc_now()).replace(":", "").replace("-", "")

    if not args.run:
        print("Dry run. No Apify credits used.")
        print(json.dumps({"actor_id": actor_id, "payload": payload}, ensure_ascii=False, indent=2))
        return 0

    rows = run_actor(actor_id, payload, token or "", args.timeout)
    normalized = [normalize_row(row) for row in rows]
    parsed_times = [parse_time(row.get("created_at")) for row in normalized if row.get("created_at")]
    parsed_times = [value for value in parsed_times if value is not None]
    newest = max(parsed_times) if parsed_times else None
    newest_age_hours = max((utc_now() - newest).total_seconds() / 3600.0, 0.0) if newest else None
    passed = newest_age_hours is not None and newest_age_hours <= args.freshness_hours

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / f"x_actor_smoke_{actor_key}_{run_id}.json"
    raw_path.write_text(json.dumps({"actor_id": actor_id, "payload": payload, "rows": rows}, ensure_ascii=False, indent=2))
    report = {
        "actor_id": actor_id,
        "run_id": run_id,
        "handles": handles,
        "per_account_cap": args.per_account,
        "days": args.days,
        "raw_rows": len(rows),
        "newest_created_at": iso(newest) if newest else None,
        "newest_age_hours": round(newest_age_hours, 2) if newest_age_hours is not None else None,
        "freshness_limit_hours": args.freshness_hours,
        "passed": passed,
        "raw_path": str(raw_path.relative_to(ROOT)),
        "samples": normalized[:8],
    }
    report_path = EXPORT_DIR / f"x_actor_smoke_{actor_key}_{run_id}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
