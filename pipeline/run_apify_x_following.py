#!/usr/bin/env python3
"""Scrape an X following list through Apify, then rank accounts by followers.

This script is intentionally separate from the main pipeline because Apify actor
runs can spend credits and may require X session cookies.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "pipeline" / "config.json"
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw" / "apify"
EXPORT_DIR = DATA_DIR / "exports"
USER_AGENT = "hero-radar-local/0.1"


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


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text())


def actor_url(actor_id: str) -> str:
    actor_path = urllib.parse.quote(actor_id.replace("/", "~"), safe="")
    return f"https://api.apify.com/v2/acts/{actor_path}/run-sync-get-dataset-items"


def build_input(config: dict[str, Any], *, max_results: int | None = None) -> dict[str, Any]:
    settings = config["apify"]["x_following"]
    actor_id = settings["actor_id"]
    limit = int(max_results or settings["max_results"])
    if actor_id == "api-ninja/x-twitter-followers-scraper":
        payload = {
            "urls": settings["source_accounts"],
            "maxResults": limit,
            "scrapeAllResults": False,
        }
    elif actor_id == "patient_discovery/twitter-followings":
        source = settings["source_accounts"][0]
        handle = re.sub(r"^https?://(?:www\\.)?(?:x|twitter)\\.com/", "", source).split("/", 1)[0]
        payload = {
            "userId": handle.lstrip("@"),
            "maxPages": max(1, (limit + 19) // 20),
        }
    else:
        payload = {
            "startUrls": settings["source_accounts"],
            "maxResults": limit,
            "proxyConfiguration": {"useApifyProxy": bool(settings.get("use_apify_proxy", False))},
        }
    auth_token = os.environ.get("X_AUTH_TOKEN")
    ct0 = os.environ.get("X_CT0")
    if auth_token:
        payload["authToken"] = auth_token
    if ct0:
        payload["ct0"] = ct0
    return payload


def redacted(payload: dict[str, Any]) -> dict[str, Any]:
    clone = json.loads(json.dumps(payload))
    for key in ["authToken", "ct0"]:
        if clone.get(key):
            clone[key] = "<set>"
    return clone


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


def pick_number(row: dict[str, Any], keys: list[str]) -> int:
    for key in keys:
        value = get_nested(row, key)
        if value is None:
            continue
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            digits = re.sub(r"[^\d]", "", value)
            if digits:
                return int(digits)
    return 0


def get_nested(row: dict[str, Any], key: str) -> Any:
    cur: Any = row
    for part in key.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def pick_text(row: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = get_nested(row, key)
        if value:
            return str(value)
    return ""


def normalize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        username = pick_text(row, ["username", "userName", "screenName", "screen_name", "handle", "legacy.screen_name"])
        username = username.lstrip("@")
        if not username:
            url = pick_text(row, ["url", "twitterUrl", "profileUrl"])
            match = re.search(r"(?:x|twitter)\.com/([^/?#]+)", url)
            username = match.group(1) if match else ""
        if not username or username.lower() in seen:
            continue
        seen.add(username.lower())

        followers = pick_number(
            row,
            [
                "followersCount",
                "followerCount",
                "followers",
                "followers_count",
                "legacy.followers_count",
                "public_metrics.followers_count",
            ],
        )
        following = pick_number(
            row,
            [
                "followingCount",
                "friendsCount",
                "following",
                "friends_count",
                "legacy.friends_count",
                "public_metrics.following_count",
            ],
        )
        normalized.append(
            {
                "username": username,
                "name": pick_text(row, ["name", "displayName", "fullName", "legacy.name"]),
                "followers_count": followers,
                "following_count": following,
                "bio": pick_text(row, ["description", "bio", "legacy.description"]),
                "url": pick_text(row, ["url", "twitterUrl", "profileUrl"]) or f"https://x.com/{username}",
                "raw": row,
            }
        )
    normalized.sort(key=lambda item: item["followers_count"], reverse=True)
    return normalized


def export(rows: list[dict[str, Any]], *, top_n: int, run_id: str) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    top = rows[:top_n]
    json_path = EXPORT_DIR / "x_following_top100_latest.json"
    md_path = EXPORT_DIR / "x_following_top100_latest.md"
    json_path.write_text(json.dumps({"run_id": run_id, "items": top}, ensure_ascii=False, indent=2))

    lines = [
        "# X Following Top Accounts",
        "",
        f"- Run: `{run_id}`",
        f"- Ranked by: `followers_count`",
        "",
        "| Rank | Followers | Following | Account | Bio |",
        "|---:|---:|---:|---|---|",
    ]
    for idx, row in enumerate(top, start=1):
        bio = (row["bio"] or "").replace("|", "\\|").replace("\n", " ")
        name = row["name"] or row["username"]
        account = f"[{name} (@{row['username']})]({row['url']})"
        lines.append(
            f"| {idx} | {row['followers_count']} | {row['following_count']} | {account} | {bio[:220]} |"
        )
    md_path.write_text("\n".join(lines) + "\n")
    print(md_path)


def main() -> int:
    load_dotenv()
    config = read_config()
    settings = config["apify"]["x_following"]
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="Actually run the Apify actor. Otherwise prints a dry run.")
    parser.add_argument("--max-results", type=int, default=int(settings["max_results"]))
    parser.add_argument("--top", type=int, default=int(settings["top_n"]))
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    actor_id = settings["actor_id"]
    payload = build_input(config, max_results=args.max_results)
    if not args.run:
        print("Dry run. No Apify credits used.")
        print(json.dumps({"actor_id": actor_id, "input": redacted(payload)}, ensure_ascii=False, indent=2))
        print("Run with `--run` after setting APIFY_TOKEN and, if needed, X_AUTH_TOKEN/X_CT0.")
        return 0

    if os.environ.get("APIFY_ENABLE_RUNS", "").lower() != "true":
        print("Refusing to run: APIFY_ENABLE_RUNS is not true.", file=sys.stderr)
        return 2
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        print("Refusing to run: APIFY_TOKEN is not set.", file=sys.stderr)
        return 2

    run_id = utc_now().replace(":", "").replace("-", "")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_rows = run_actor(actor_id, payload, token, args.timeout)
    raw_path = RAW_DIR / f"x_following_{run_id}.json"
    raw_path.write_text(json.dumps(raw_rows, ensure_ascii=False, indent=2))
    rows = normalize(raw_rows)
    export(rows, top_n=args.top, run_id=run_id)
    print(f"Raw rows: {len(raw_rows)}; normalized accounts: {len(rows)}; raw: {raw_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
