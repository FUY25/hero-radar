#!/usr/bin/env python3
"""Hero Radar lightweight data pipeline.

The goal is not perfect data science. It is a pragmatic internal pipeline:
collect many cheap signals, normalize them into one local table, and rank what
is starting to move.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import datetime as dt
import email.utils
import html
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "pipeline" / "config.json"
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
EXPORT_DIR = DATA_DIR / "exports"
DB_PATH = DATA_DIR / "hero_radar.sqlite"

USER_AGENT = "hero-radar-local/0.1"

CHANNEL_ORDER = [
    "github_trending",
    "github_movers_trending_repos",
    "github_movers_repofomo",
    "github_search",
    "hn_search",
    "hn_top",
    "product_hunt",
    "huggingface_models",
    "huggingface_datasets",
    "huggingface_spaces",
    "npm_search",
    "pypi_newest",
    "pypi_updates",
    "x_seed_accounts",
    "x_tweets",
]

WINDOW_ORDER = {"24h": 0, "7d": 1, "30d": 2, "30d+": 3, "current": 4}
X_WINDOW_HOURS = {"24h": 24.0, "7d": 7 * 24.0, "30d": 30 * 24.0}
SOURCE_DASHBOARD_HIDDEN_CHANNELS = {"huggingface_models", "huggingface_datasets", "x_seed_accounts"}
SETTINGS_CHANNEL_ORDER = ["settings_source_health", "settings_search_terms", "x_seed_accounts"]
SETTINGS_CHANNELS = set(SETTINGS_CHANNEL_ORDER)
EXCLUDED_ITEM_SOURCES = {"x_project_mentions"}


@dataclasses.dataclass
class SourceItem:
    source: str
    external_id: str
    name: str
    url: str
    heat: float | None = None
    velocity_seed: float | None = None
    source_rank: int | None = None
    description: str | None = None
    fetched_at: str | None = None
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)
    raw: dict[str, Any] = dataclasses.field(default_factory=dict)


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(ts: dt.datetime) -> str:
    return ts.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    RAW_DIR.mkdir(exist_ok=True)
    EXPORT_DIR.mkdir(exist_ok=True)


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


def read_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text())


def request_text(url: str, *, headers: dict[str, str] | None = None, timeout: int = 30) -> str:
    merged = {"User-Agent": USER_AGENT}
    if headers:
        merged.update(headers)
    req = urllib.request.Request(url, headers=merged)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def request_json(url: str, *, headers: dict[str, str] | None = None, timeout: int = 30) -> Any:
    merged = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if headers:
        merged.update(headers)
    req = urllib.request.Request(url, headers=merged)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def post_json(url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None, timeout: int = 30) -> Any:
    body = json.dumps(payload).encode("utf-8")
    merged = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": USER_AGENT}
    if headers:
        merged.update(headers)
    req = urllib.request.Request(url, data=body, headers=merged, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def strip_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def parse_int(value: str | None) -> int | None:
    if not value:
        return None
    digits = re.sub(r"[^\d]", "", value)
    return int(digits) if digits else None


def period_to_window(period: str) -> str:
    return {
        "daily": "24h",
        "weekly": "7d",
        "monthly": "30d",
        "past_24_hours": "24h",
        "past_week": "7d",
        "past_month": "30d",
    }.get(period, period)


def item_window(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") or {}
    return metadata.get("window") or period_to_window(str(metadata.get("period") or "current"))


def github_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def query_entry(value: str | dict[str, Any]) -> tuple[str, str]:
    if isinstance(value, dict):
        query = str(value.get("query") or "").strip()
        label = str(value.get("label") or query).strip()
        return label or query, query
    query = str(value).strip()
    return query, query


def collect_github_trending(config: dict[str, Any], fetched_at: str) -> tuple[list[SourceItem], str | None]:
    items: list[SourceItem] = []
    settings = config["github_trending"]
    for period in settings["periods"]:
        for language in settings["languages"]:
            path = f"/{language}" if language else ""
            url = f"https://github.com/trending{path}?since={period}"
            html_text = request_text(url, headers={"User-Agent": "Mozilla/5.0"})
            raw_file = RAW_DIR / f"github_trending_{language or 'all'}_{period}_{fetched_at.replace(':', '').replace('-', '')}.html"
            raw_file.write_text(html_text)

            articles = re.findall(r'<article class="Box-row">(.*?)</article>', html_text, flags=re.S)
            for rank, article in enumerate(articles, start=1):
                repo_match = re.search(r'<h2 class="h3 lh-condensed".*?<a\b[^>]*href="/([^/]+)/([^"/]+)"', article, flags=re.S)
                if not repo_match:
                    continue
                owner = strip_html(repo_match.group(1))
                repo = strip_html(repo_match.group(2)).replace(" ", "")
                full_name = f"{owner}/{repo}"
                desc_match = re.search(r'<p[^>]*class="[^"]*\bcolor-fg-muted\b[^"]*"[^>]*>(.*?)</p>', article, flags=re.S)
                description = strip_html(desc_match.group(1)) if desc_match else ""

                lang_match = re.search(r'<span itemprop="programmingLanguage">(.*?)</span>', article, flags=re.S)
                repo_language = strip_html(lang_match.group(1)) if lang_match else None

                stars_total = None
                star_match = re.search(rf'href="/{re.escape(owner)}/{re.escape(repo)}/stargazers".*?>([\s\d,]+)</a>', article, flags=re.S)
                if star_match:
                    stars_total = parse_int(star_match.group(1))

                period_stars = None
                period_match = re.search(r'([\d,]+)\s+stars?\s+(today|this week|this month)', article, flags=re.I)
                if period_match:
                    period_stars = parse_int(period_match.group(1))

                items.append(
                    SourceItem(
                        source="github_trending",
                        external_id=f"{period}:{full_name}",
                        name=full_name,
                        url=f"https://github.com/{full_name}",
                        source_rank=rank,
                        description=description,
                        fetched_at=fetched_at,
                        metadata={
                            "period": period,
                            "window": period_to_window(period),
                            "scope_language": language or "all",
                            "language": repo_language or language or "unknown",
                            "period_stars": period_stars,
                            "stars_total": stars_total,
                            "raw_file": str(raw_file.relative_to(ROOT)),
                        },
                        raw={"full_name": full_name},
                    )
                )
            time.sleep(0.5)
    return items, None


def collect_github_search(config: dict[str, Any], fetched_at: str) -> tuple[list[SourceItem], str | None]:
    settings = config["github_search"]
    items: list[SourceItem] = []
    per_page = max(1, min(100, int(settings.get("per_page", 10))))
    max_results = max(per_page, int(settings.get("max_results_per_query", per_page)))
    pages = max(1, (max_results + per_page - 1) // per_page)
    for query_config in settings["queries"]:
        query_label, query = query_entry(query_config)
        if not query:
            continue
        rank_offset = 0
        for page in range(1, pages + 1):
            params = urllib.parse.urlencode(
                {
                    "q": query,
                    "sort": "stars",
                    "order": "desc",
                    "per_page": str(per_page),
                    "page": str(page),
                }
            )
            url = f"https://api.github.com/search/repositories?{params}"
            data = request_json(url, headers=github_headers())
            raw_file = RAW_DIR / f"github_search_{safe_name(query)}_p{page}_{fetched_at.replace(':', '').replace('-', '')}.json"
            raw_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            repos = data.get("items", [])
            for repo in repos:
                rank = rank_offset + 1
                rank_offset += 1
                if rank > max_results:
                    break
                full_name = repo["full_name"]
                items.append(
                    SourceItem(
                        source="github_search",
                        external_id=f"{query_label}:{full_name}",
                        name=full_name,
                        url=repo["html_url"],
                        source_rank=rank,
                        description=repo.get("description") or "",
                        fetched_at=fetched_at,
                        metadata={
                            "query": query,
                            "query_label": query_label,
                            "window": "current",
                            "stars": repo.get("stargazers_count"),
                            "forks": repo.get("forks_count"),
                            "created_at": repo.get("created_at"),
                            "pushed_at": repo.get("pushed_at"),
                            "language": repo.get("language"),
                            "topics": repo.get("topics", []),
                            "search_page": page,
                        },
                        raw=repo,
                    )
                )
            if len(repos) < per_page or rank_offset >= max_results:
                break
            time.sleep(6.2 if not os.environ.get("GITHUB_TOKEN") else 1.0)
    return items, None


def collect_github_movers(config: dict[str, Any], fetched_at: str) -> tuple[list[SourceItem], str | None]:
    settings = config.get("github_movers", {})
    if not settings.get("enabled", False):
        return [], "disabled"

    items: list[SourceItem] = []
    errors: list[str] = []

    trending_settings = settings.get("trending_repos", {})
    if trending_settings.get("enabled", False):
        try:
            items.extend(collect_trending_repos_movers(trending_settings, fetched_at))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"trending-repos: {type(exc).__name__}: {exc}")

    repofomo_settings = settings.get("repofomo", {})
    if repofomo_settings.get("enabled", False):
        try:
            items.extend(collect_repofomo_movers(repofomo_settings, fetched_at))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"repofomo: {type(exc).__name__}: {exc}")

    return items, "; ".join(errors) if errors else None


def collect_trending_repos_movers(settings: dict[str, Any], fetched_at: str) -> list[SourceItem]:
    url = str(settings.get("url") or "https://trending-repos.com/")
    html_text = request_text(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    raw_file = RAW_DIR / f"github_movers_trending_repos_{fetched_at.replace(':', '').replace('-', '')}.html"
    raw_file.write_text(html_text)

    repositories = extract_trending_repos_initial_repositories(html_text)
    periods = [str(period) for period in settings.get("periods", ["daily", "weekly", "monthly"])]
    limit = int(settings.get("limit_per_period", 100))

    items: list[SourceItem] = []
    for period in periods:
        rows = repositories.get(period) or []
        for row in rows[:limit]:
            full_name = str(row.get("fullName") or "")
            if not full_name:
                continue
            components = row.get("scoreComponents") if isinstance(row.get("scoreComponents"), dict) else {}
            stars_velocity = to_float(components.get("starsVelocity"))
            forks_velocity = to_float(components.get("forksVelocity"))
            score = to_float(row.get("score"))
            stars_count = to_float(row.get("starsCount"))
            sparkline = row.get("sparkline") if isinstance(row.get("sparkline"), list) else []
            items.append(
                SourceItem(
                    source="github_movers_trending_repos",
                    external_id=f"trending-repos:{period}:{full_name}",
                    name=full_name,
                    url=f"https://github.com/{full_name}",
                    source_rank=int(row.get("rank") or len(items) + 1),
                    description=row.get("description") or "",
                    fetched_at=fetched_at,
                    metadata={
                        "provider": "Trending Repos",
                        "period": period,
                        "window": period_to_window(period),
                        "primary_language": row.get("primaryLanguage"),
                        "topics": row.get("topics") or [],
                        "license": row.get("license"),
                        "stars_count": row.get("starsCount"),
                        "forks_count": row.get("forksCount"),
                        "source_score": score,
                        "stars_velocity": stars_velocity,
                        "forks_velocity": forks_velocity,
                        "freshness_bonus": to_float(components.get("freshnessBonus")),
                        "language_rank": row.get("languageRank"),
                        "sparkline": sparkline,
                        "latest_sparkline_delta": sparkline[-1] if sparkline else None,
                        "raw_file": str(raw_file.relative_to(ROOT)),
                    },
                    raw=row,
                )
            )
    return items


def extract_trending_repos_initial_repositories(html_text: str) -> dict[str, list[dict[str, Any]]]:
    chunks: list[str] = []
    for match in re.finditer(r"<script>self\.__next_f\.push\((.*?)\)</script>", html_text, flags=re.S):
        try:
            chunk = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(chunk, list) and len(chunk) > 1 and isinstance(chunk[1], str):
            chunks.append(chunk[1])

    stream = "".join(chunks)
    marker = '"initialRepositories"'
    marker_idx = stream.find(marker)
    if marker_idx < 0:
        raise ValueError("initialRepositories not found in Trending Repos page")
    object_start = stream.find("{", marker_idx + len(marker))
    object_end = find_balanced_json_end(stream, object_start)
    data = json.loads(stream[object_start:object_end])
    return {
        str(period): [row for row in rows if isinstance(row, dict)]
        for period, rows in data.items()
        if isinstance(rows, list)
    }


def find_balanced_json_end(text: str, start: int) -> int:
    if start < 0 or start >= len(text) or text[start] != "{":
        raise ValueError("balanced JSON object start not found")
    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return idx + 1
    raise ValueError("balanced JSON object end not found")


def collect_repofomo_movers(settings: dict[str, Any], fetched_at: str) -> list[SourceItem]:
    data_url = str(settings.get("data_url") or "https://repofomo.com/data.js")
    js_text = request_text(data_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    raw_file = RAW_DIR / f"github_movers_repofomo_{fetched_at.replace(':', '').replace('-', '')}.js"
    raw_file.write_text(js_text)

    match = re.search(r"window\.REPO_DATA\s*=\s*(\[.*?\])\s*;", js_text, flags=re.S)
    if not match:
        raise ValueError("window.REPO_DATA not found")
    rows = [row for row in json.loads(match.group(1)) if isinstance(row, dict)]
    limit = int(settings.get("limit", 200))

    items: list[SourceItem] = []
    for rank, row in enumerate(rows[:limit], start=1):
        full_name = str(row.get("name") or "")
        if not full_name:
            continue
        total_stars = to_float(row.get("tot_stars"))
        stars_7d = to_float(row.get("7d_new"))
        stars_30d = to_float(row.get("30d_new"))
        stars_60d = to_float(row.get("60d_new"))
        items.append(
            SourceItem(
                source="github_movers_repofomo",
                external_id=f"repofomo:{full_name}",
                name=full_name,
                url=f"https://github.com/{full_name}",
                source_rank=rank,
                description=row.get("description") or "",
                fetched_at=fetched_at,
                metadata={
                    "provider": "RepoFOMO",
                    "window": "7d+30d+60d",
                    "fomo_rank": rank,
                    "stars_total": total_stars,
                    "stars_7d": stars_7d,
                    "stars_30d": stars_30d,
                    "stars_60d": stars_60d,
                    "growth_7d_percent": to_float(row.get("7d%")),
                    "growth_30d_percent": to_float(row.get("30d%")),
                    "growth_60d_percent": to_float(row.get("60d%")),
                    "forks": to_float(row.get("forks")),
                    "fork_growth_percent": to_float(row.get("f_growth%")),
                    "new_forks": to_float(row.get("new_forks")),
                    "subscribers": to_float(row.get("subs")),
                    "star_age_days": to_float(row.get("star_age")),
                    "info": row.get("info"),
                    "raw_file": str(raw_file.relative_to(ROOT)),
                },
                raw=row,
            )
        )
    return items


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    text = text.replace(",", "").replace("%", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def collect_hn_algolia(config: dict[str, Any], fetched_at: str) -> tuple[list[SourceItem], str | None]:
    items: list[SourceItem] = []
    now = utc_now()
    windows = config["hn"].get("algolia_windows") or {"7d": 7}
    hits_per_page = max(1, min(1000, int(config["hn"].get("algolia_hits_per_page", 20))))
    for window, days in windows.items():
        since = int((now - dt.timedelta(days=float(days))).timestamp())
        for query_config in config["hn"]["algolia_queries"]:
            query_label, query = query_entry(query_config)
            if not query:
                continue
            params = urllib.parse.urlencode(
                {
                    "query": query,
                    "tags": "story",
                    "numericFilters": f"created_at_i>{since}",
                    "hitsPerPage": str(hits_per_page),
                }
            )
            url = f"https://hn.algolia.com/api/v1/search_by_date?{params}"
            data = request_json(url)
            raw_file = RAW_DIR / f"hn_algolia_{safe_name(query)}_{window}_{fetched_at.replace(':', '').replace('-', '')}.json"
            raw_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            for rank, hit in enumerate(data.get("hits", []), start=1):
                story_id = str(hit.get("objectID") or hit.get("story_id") or "")
                title = hit.get("title") or hit.get("story_title") or "(untitled)"
                points = hit.get("points") or 0
                comments = hit.get("num_comments") or 0
                item_url = hit.get("url") or f"https://news.ycombinator.com/item?id={story_id}"
                items.append(
                    SourceItem(
                        source="hn_algolia",
                        external_id=f"{window}:{query_label}:{story_id}",
                        name=title,
                        url=item_url,
                        source_rank=rank,
                        description=strip_html(hit.get("story_text") or ""),
                        fetched_at=fetched_at,
                        metadata={
                            "query": query,
                            "query_label": query_label,
                            "window": window,
                            "points": points,
                            "comments": comments,
                            "author": hit.get("author"),
                            "created_at": hit.get("created_at"),
                            "hn_url": f"https://news.ycombinator.com/item?id={story_id}",
                        },
                        raw=hit,
                    )
                )
            time.sleep(0.5)
    return items, None


def collect_hn_firebase(config: dict[str, Any], fetched_at: str) -> tuple[list[SourceItem], str | None]:
    items: list[SourceItem] = []
    limit = int(config["hn"].get("firebase_limit", 50))
    workers = max(1, int(config["hn"].get("firebase_workers", 12)))

    def fetch_item(list_name: str, rank: int, item_id: Any) -> SourceItem | None:
        try:
            item = request_json(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json", timeout=8)
        except Exception:
            return None
        if not isinstance(item, dict):
            return None
        title = item.get("title") or ""
        url = item.get("url") or f"https://news.ycombinator.com/item?id={item_id}"
        score = item.get("score") or 0
        comments = item.get("descendants") or 0
        return SourceItem(
            source="hn_firebase",
            external_id=f"{list_name}:{item_id}",
            name=title,
            url=url,
            source_rank=rank,
            description=strip_html(item.get("text") or ""),
            fetched_at=fetched_at,
            metadata={
                "list": list_name,
                "window": "current",
                "score": score,
                "comments": comments,
                "author": item.get("by"),
                "created_at_unix": item.get("time"),
                "hn_url": f"https://news.ycombinator.com/item?id={item_id}",
            },
            raw=item,
        )

    for list_name in config["hn"]["firebase_lists"]:
        ids = request_json(f"https://hacker-news.firebaseio.com/v0/{list_name}.json", timeout=8)
        work = [(list_name, rank, item_id) for rank, item_id in enumerate(ids[:limit], start=1)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(fetch_item, *args) for args in work]
            for future in concurrent.futures.as_completed(futures):
                item = future.result()
                if item is not None:
                    items.append(item)
    return items, None


def collect_product_hunt(config: dict[str, Any], fetched_at: str) -> tuple[list[SourceItem], str | None]:
    if not config.get("product_hunt", {}).get("enabled", False):
        return [], "disabled"
    token = os.environ.get("PRODUCTHUNT_TOKEN")
    if not token:
        return [], "PRODUCTHUNT_TOKEN is not set"

    first = int(config["product_hunt"].get("first", 50))
    query = """
    query HeroRadarProductHuntPosts($first: Int!) {
      posts(first: $first, order: VOTES) {
        edges {
          node {
            id
            name
            slug
            tagline
            url
            website
            votesCount
            commentsCount
            dailyRank
            weeklyRank
            createdAt
            featuredAt
          }
        }
      }
    }
    """
    headers = {"Authorization": f"Bearer {token}"}
    user_context = os.environ.get("PRODUCTHUNT_USER_CONTEXT")
    if user_context:
        headers["X-Product-Hunt-User-Context"] = user_context
    data = post_json(
        "https://api.producthunt.com/v2/api/graphql",
        {"query": query, "variables": {"first": first}},
        headers=headers,
        timeout=30,
    )
    raw_file = RAW_DIR / f"product_hunt_posts_{fetched_at.replace(':', '').replace('-', '')}.json"
    raw_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    if data.get("errors"):
        return [], json.dumps(data["errors"], ensure_ascii=False)[:400]

    items: list[SourceItem] = []
    edges = data.get("data", {}).get("posts", {}).get("edges", [])
    for rank, edge in enumerate(edges, start=1):
        node = edge.get("node") or {}
        item_id = str(node.get("id") or node.get("slug") or "")
        if not item_id:
            continue
        votes = node.get("votesCount") or 0
        comments = node.get("commentsCount") or 0
        items.append(
            SourceItem(
                source="product_hunt",
                external_id=item_id,
                name=node.get("name") or "",
                url=node.get("url") or "",
                source_rank=node.get("dailyRank") or rank,
                description=node.get("tagline") or "",
                fetched_at=fetched_at,
                metadata={
                    "slug": node.get("slug"),
                    "window": "current",
                    "website": node.get("website"),
                    "votes": votes,
                    "comments": comments,
                    "daily_rank": node.get("dailyRank"),
                    "weekly_rank": node.get("weeklyRank"),
                    "created_at": node.get("createdAt"),
                    "featured_at": node.get("featuredAt"),
                },
                raw=node,
            )
        )
    return items, None


def collect_huggingface(config: dict[str, Any], fetched_at: str) -> tuple[list[SourceItem], str | None]:
    items: list[SourceItem] = []
    settings = config["huggingface"]
    for resource in settings["resources"]:
        params = urllib.parse.urlencode(
            {"sort": "trendingScore", "direction": "-1", "limit": str(settings.get("limit", 20))}
        )
        url = f"https://huggingface.co/api/{resource}?{params}"
        data = request_json(url)
        raw_file = RAW_DIR / f"huggingface_{resource}_{fetched_at.replace(':', '').replace('-', '')}.json"
        raw_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        for rank, entry in enumerate(data, start=1):
            entry_id = entry.get("id") or entry.get("name") or ""
            likes = entry.get("likes") or 0
            downloads = entry.get("downloads") or 0
            items.append(
                SourceItem(
                    source=f"huggingface_{resource}",
                    external_id=entry_id,
                    name=entry_id,
                    url=f"https://huggingface.co/{entry_id}",
                    source_rank=rank,
                    description=entry.get("description") or "",
                    fetched_at=fetched_at,
                    metadata={
                        "resource": resource,
                        "window": "current",
                        "likes": likes,
                        "downloads": downloads,
                        "created_at": entry.get("createdAt"),
                        "last_modified": entry.get("lastModified"),
                        "pipeline_tag": entry.get("pipeline_tag"),
                        "tags": entry.get("tags", []),
                    },
                    raw=entry,
                )
            )
        time.sleep(0.5)
    return items, None


def collect_npm_search(config: dict[str, Any], fetched_at: str) -> tuple[list[SourceItem], str | None]:
    settings = config.get("npm", {})
    if not settings.get("enabled", False):
        return [], "disabled"

    items: list[SourceItem] = []
    size = int(settings.get("size", 50))
    for query_config in settings.get("queries", []):
        query_label, query = query_entry(query_config)
        if not query:
            continue
        params = urllib.parse.urlencode({"text": query, "size": str(size)})
        url = f"https://registry.npmjs.org/-/v1/search?{params}"
        data = request_json(url, timeout=45)
        raw_file = RAW_DIR / f"npm_search_{safe_name(query_label)}_{fetched_at.replace(':', '').replace('-', '')}.json"
        raw_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))

        for rank, obj in enumerate(data.get("objects", []), start=1):
            if not isinstance(obj, dict):
                continue
            package = obj.get("package") if isinstance(obj.get("package"), dict) else {}
            score = obj.get("score") if isinstance(obj.get("score"), dict) else {}
            detail = score.get("detail") if isinstance(score.get("detail"), dict) else {}
            downloads = obj.get("downloads") if isinstance(obj.get("downloads"), dict) else {}
            links = package.get("links") if isinstance(package.get("links"), dict) else {}
            name = str(package.get("name") or "")
            if not name:
                continue
            weekly_downloads = to_float(downloads.get("weekly"))
            final_score = to_float(score.get("final") or obj.get("searchScore"))
            items.append(
                SourceItem(
                    source="npm_search",
                    external_id=f"{query_label}:{name}",
                    name=name,
                    url=str(links.get("npm") or f"https://www.npmjs.com/package/{urllib.parse.quote(name, safe='@/')}"),
                    source_rank=rank,
                    description=package.get("description") or "",
                    fetched_at=fetched_at,
                    metadata={
                        "query": query,
                        "query_label": query_label,
                        "window": "current",
                        "version": package.get("version"),
                        "keywords": package.get("keywords") or [],
                        "license": package.get("license"),
                        "publisher": (package.get("publisher") or {}).get("username") if isinstance(package.get("publisher"), dict) else None,
                        "maintainers_count": len(package.get("maintainers") or []),
                        "package_date": package.get("date"),
                        "updated": obj.get("updated"),
                        "weekly_downloads": weekly_downloads,
                        "monthly_downloads": to_float(downloads.get("monthly")),
                        "dependents": to_float(obj.get("dependents")),
                        "search_score": to_float(obj.get("searchScore")),
                        "score_final": final_score,
                        "score_quality": to_float(detail.get("quality")),
                        "score_popularity": to_float(detail.get("popularity")),
                        "score_maintenance": to_float(detail.get("maintenance")),
                        "homepage": links.get("homepage"),
                        "repository": links.get("repository"),
                        "bugs": links.get("bugs"),
                        "raw_file": str(raw_file.relative_to(ROOT)),
                    },
                    raw=obj,
                )
            )
        time.sleep(float(settings.get("sleep_seconds", 0.5)))
    return items, None


def collect_pypi_feeds(config: dict[str, Any], fetched_at: str) -> tuple[list[SourceItem], str | None]:
    settings = config.get("pypi", {})
    if not settings.get("enabled", False):
        return [], "disabled"

    feed_urls = {
        "newest": "https://pypi.org/rss/packages.xml",
        "updates": "https://pypi.org/rss/updates.xml",
    }
    enabled_feeds = settings.get("feeds", ["newest", "updates"])
    limit = int(settings.get("limit_per_feed", 100))
    enrich_limit = int(settings.get("json_enrich_limit_per_feed", 20))
    items: list[SourceItem] = []
    errors: list[str] = []

    for feed_name in enabled_feeds:
        url = feed_urls.get(str(feed_name))
        if not url:
            errors.append(f"unknown feed: {feed_name}")
            continue
        try:
            xml_text = request_text(url, timeout=30)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{feed_name}: {type(exc).__name__}: {exc}")
            continue

        raw_file = RAW_DIR / f"pypi_{feed_name}_{fetched_at.replace(':', '').replace('-', '')}.xml"
        raw_file.write_text(xml_text)
        root = ET.fromstring(xml_text)
        channel = root.find("channel")
        if channel is None:
            errors.append(f"{feed_name}: RSS channel not found")
            continue
        rss_items = channel.findall("item")[:limit]
        for rank, node in enumerate(rss_items, start=1):
            title = node.findtext("title") or ""
            link = node.findtext("link") or ""
            description = strip_html(node.findtext("description") or "")
            pub_date = node.findtext("pubDate") or ""
            author = node.findtext("author") or ""
            package_name, version = parse_pypi_feed_title(str(feed_name), title, link)

            project_json: dict[str, Any] = {}
            if package_name and rank <= enrich_limit:
                try:
                    project_json = request_json(
                        f"https://pypi.org/pypi/{urllib.parse.quote(package_name)}/json",
                        timeout=20,
                    )
                    time.sleep(float(settings.get("json_sleep_seconds", 0.2)))
                except Exception:
                    project_json = {}

            info = project_json.get("info") if isinstance(project_json.get("info"), dict) else {}
            urls = project_json.get("urls") if isinstance(project_json.get("urls"), list) else []
            latest_upload = latest_pypi_upload_time(urls)
            classifiers = info.get("classifiers") if isinstance(info.get("classifiers"), list) else []
            project_urls = info.get("project_urls") if isinstance(info.get("project_urls"), dict) else {}
            parsed_pub = parse_email_datetime(pub_date)
            items.append(
                SourceItem(
                    source=f"pypi_{feed_name}",
                    external_id=f"{feed_name}:{package_name or title}:{version or ''}",
                    name=package_name or title,
                    url=link or f"https://pypi.org/project/{package_name}/",
                    source_rank=rank,
                    description=description or info.get("summary") or "",
                    fetched_at=fetched_at,
                    metadata={
                        "feed": feed_name,
                        "window": "current",
                        "title": title,
                        "version": version,
                        "author": author,
                        "pub_date": pub_date,
                        "summary": info.get("summary"),
                        "latest_version": info.get("version"),
                        "requires_python": info.get("requires_python"),
                        "license": info.get("license"),
                        "keywords": info.get("keywords"),
                        "classifiers": classifiers,
                        "project_urls": project_urls,
                        "home_page": info.get("home_page"),
                        "package_url": info.get("package_url"),
                        "release_url": info.get("release_url"),
                        "latest_upload_time": latest_upload,
                        "raw_file": str(raw_file.relative_to(ROOT)),
                    },
                    raw={
                        "rss": {
                            "title": title,
                            "link": link,
                            "description": description,
                            "pubDate": pub_date,
                            "author": author,
                        },
                        "info": info,
                        "latest_upload_time": latest_upload,
                    },
                )
            )
    return items, "; ".join(errors) if errors else None


def parse_pypi_feed_title(feed_name: str, title: str, link: str) -> tuple[str, str | None]:
    if feed_name == "newest":
        match = re.match(r"(.+?)\s+added to PyPI$", title)
        if match:
            return match.group(1).strip(), None
    if feed_name == "updates":
        match = re.match(r"(.+?)\s+([^\s]+)$", title)
        if match:
            return match.group(1).strip(), match.group(2).strip()
    match = re.search(r"/project/([^/]+)(?:/([^/]+))?/", link)
    if match:
        return urllib.parse.unquote(match.group(1)), urllib.parse.unquote(match.group(2)) if match.group(2) else None
    return title.strip(), None


def parse_email_datetime(value: str) -> dt.datetime | None:
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def latest_pypi_upload_time(urls: list[Any]) -> str | None:
    timestamps = []
    for row in urls:
        if isinstance(row, dict) and row.get("upload_time_iso_8601"):
            timestamps.append(str(row["upload_time_iso_8601"]))
    return max(timestamps) if timestamps else None


def collect_x_seed_accounts(config: dict[str, Any], fetched_at: str) -> tuple[list[SourceItem], str | None]:
    settings = config.get("apify", {}).get("x_seed_from_following", {})
    if not settings.get("enabled", False):
        return [], "disabled"

    candidate_path = ROOT / str(settings.get("candidate_file", "data/exports/x_following_ai_seed_candidates_latest.json"))
    if not candidate_path.exists():
        return [], f"candidate file not found: {candidate_path.relative_to(ROOT)}"

    limit = int(settings.get("limit", 50))
    candidates = selected_x_seed_rows(config, limit)

    items: list[SourceItem] = []
    for rank, row in enumerate(candidates, start=1):
        username = str(row.get("username") or "").lstrip("@")
        if not username:
            continue
        name = row.get("name") or username
        followers = int(row.get("followers_count") or 0)
        following = int(row.get("following_count") or 0)
        bio = row.get("bio") or ""
        items.append(
            SourceItem(
                source="x_seed_accounts",
                external_id=username.lower(),
                name=f"{name} (@{username})",
                url=row.get("url") or f"https://x.com/{username}",
                source_rank=rank,
                description=bio,
                fetched_at=fetched_at,
                metadata={
                    "window": "current",
                    "username": username,
                    "followers_count": followers,
                    "following_count": following,
                    "keyword_score": row.get("keyword_score"),
                    "candidate_file": str(candidate_path.relative_to(ROOT)),
                    "bio": bio,
                },
                raw=row,
            )
        )
    return items, None


def is_ai_related_x_seed(row: dict[str, Any]) -> bool:
    text = " ".join(
        [
            str(row.get("username") or ""),
            str(row.get("name") or ""),
            str(row.get("bio") or ""),
        ]
    ).lower()
    ai_patterns = [
        r"\bai\b",
        r"#ai\b",
        r"\bopenai\b",
        r"\banthropic\b",
        r"\bclaude\b",
        r"\bchatgpt\b",
        r"\bllm\b",
        r"large language",
        r"machine learning",
        r"deep learning",
        r"\bdeepmind\b",
        r"hugging\s*face",
        r"\bhuggingface\b",
        r"\bagent\b",
        r"\bagents\b",
        r"\bagentic\b",
        r"\bcursor\b",
        r"coding agent",
        r"\bneural\b",
        r"\brobot",
        r"\bagi\b",
    ]
    return any(re.search(pattern, text) for pattern in ai_patterns)


def is_personal_x_seed(row: dict[str, Any]) -> bool:
    username = str(row.get("username") or "").strip().lower().lstrip("@")
    name = str(row.get("name") or "").strip().lower()
    bio = str(row.get("bio") or "").strip().lower()
    text = f"{username} {name} {bio}"

    blocked_usernames = {
        "openai",
        "googleai",
        "googledeepmind",
        "claudeai",
        "anthropicai",
        "aiatmeta",
        "huggingface",
        "chatgptapp",
        "msftresearch",
        "deeplearningai",
        "berkeley_ai",
        "stanfordailab",
        "stanfordnlp",
        "a16z",
        "sequoia",
        "foundersfund",
        "vercel",
        "openclaw",
        "yzilabs",
    }
    if username in blocked_usernames:
        return False

    org_patterns = [
        r"\bofficial account\b",
        r"^we\s",
        r"\bwe're\b",
        r"\bwe are\b",
        r"\bour mission\b",
        r"\bcompany\b",
        r"\bresearch lab\b",
        r"\blaboratory\b",
        r"\buniversity\b",
        r"\bcapital\b",
        r"\bventures?\b",
        r"\binvests?\b",
        r"\bcommunity\b",
        r"\bthe ai community\b",
    ]
    if any(re.search(pattern, text) for pattern in org_patterns):
        return False

    personal_patterns = [
        r"\bfounder\b",
        r"\bco-?founder\b",
        r"\bceo\b",
        r"\bcto\b",
        r"\bprof\b",
        r"\bprofessor\b",
        r"\bresearcher\b",
        r"\bengineer\b",
        r"\bbuilding\b",
        r"\bi build\b",
        r"\bcreator\b",
        r"\bwriter\b",
        r"\binvestor\b",
        r"\bpartner\b",
        r"\bscientist\b",
        r"\bmy\b",
        r"\bi\b",
    ]
    return any(re.search(pattern, text) for pattern in personal_patterns)


def selected_x_seed_rows(config: dict[str, Any], limit: int | None = None) -> list[dict[str, Any]]:
    settings = config.get("apify", {}).get("x_seed_from_following", {})
    candidate_path = ROOT / str(settings.get("candidate_file", "data/exports/x_following_ai_seed_candidates_latest.json"))
    rows: list[dict[str, Any]] = []
    if settings.get("enabled", False) and candidate_path.exists():
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
                "following_count": 0,
                "bio": "",
                "url": f"https://x.com/{handle}",
                "keyword_score": None,
            }
            for handle in config.get("apify", {}).get("x_seed_accounts", [])
        ]

    seen: set[str] = set()
    selected: list[dict[str, Any]] = []
    for row in rows:
        username = str(row.get("username") or "").strip().lstrip("@")
        if not username or username.lower() in seen:
            continue
        seen.add(username.lower())
        selected.append(row)
        if limit is not None and len(selected) >= limit:
            break
    return selected


def selected_x_seed_handle_set(config: dict[str, Any], limit: int | None = None) -> set[str]:
    return {str(row.get("username") or "").strip().lstrip("@").lower() for row in selected_x_seed_rows(config, limit) if row.get("username")}


def collect_x_tweets(config: dict[str, Any], fetched_at: str) -> tuple[list[SourceItem], str | None]:
    settings = config.get("apify", {}).get("x_tweets", {})
    if not settings.get("enabled", False):
        return [], "disabled"

    tweet_path = ROOT / str(settings.get("tweet_file", "data/exports/x_tweets_latest.json"))
    imported = 0
    import_error: str | None = None
    if tweet_path.exists():
        data = json.loads(tweet_path.read_text())
        tweet_rows = [row for row in data.get("items", []) if isinstance(row, dict)]
        allowed_handles = selected_x_seed_handle_set(config, int(settings.get("accounts_limit", 50)))
        if allowed_handles:
            tweet_rows = [
                row
                for row in tweet_rows
                if str(row.get("author_username") or row.get("username") or "").strip().lstrip("@").lower() in allowed_handles
            ]
        conn = sqlite3.connect(DB_PATH)
        try:
            init_db(conn)
            imported = upsert_x_tweet_rows(
                conn,
                tweet_rows,
                fetched_at=fetched_at,
                import_run_id=str(data.get("run_id") or ""),
            )
        finally:
            conn.close()
    else:
        import_error = f"tweet file not found: {tweet_path.relative_to(ROOT)}; using local tweet store"

    items = x_store_items(config, fetched_at)
    if not items and import_error:
        return items, import_error
    if not items and imported == 0:
        return items, "tweet store has no rows for configured windows"
    return items, None


def collect_ossinsight_optional(config: dict[str, Any], fetched_at: str) -> tuple[list[SourceItem], str | None]:
    if not config.get("ossinsight", {}).get("enabled", False):
        return [], "disabled"
    items: list[SourceItem] = []
    errors: list[str] = []
    settings = config["ossinsight"]
    for period in settings["periods"]:
        for language in settings["languages"]:
            params = urllib.parse.urlencode({"period": period, "language": language})
            url = f"https://api.ossinsight.io/v1/trends/repos/?{params}"
            try:
                data = request_json(url)
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")
                errors.append(f"{period}/{language}: HTTP {exc.code}: {body[:160]}")
                continue
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{period}/{language}: {type(exc).__name__}: {exc}")
                continue

            raw_file = RAW_DIR / f"ossinsight_{language}_{period}_{fetched_at.replace(':', '').replace('-', '')}.json"
            raw_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            rows = normalize_ossinsight_rows(data)
            for rank, row in enumerate(rows, start=1):
                repo_name = row.get("repo_name") or row.get("full_name") or row.get("repository") or ""
                if not repo_name:
                    continue
                stars = row.get("stars") or row.get("star_count") or row.get("stars_count")
                items.append(
                    SourceItem(
                        source="ossinsight_trending",
                        external_id=f"{period}:{repo_name}",
                        name=repo_name,
                        url=f"https://github.com/{repo_name}",
                        source_rank=rank,
                        description=row.get("description") or "",
                        fetched_at=fetched_at,
                        metadata={"period": period, "window": period_to_window(period), "language": language, **row},
                        raw=row,
                    )
                )
    return items, "; ".join(errors) if errors else None


def collect_apify_configured(config: dict[str, Any], fetched_at: str) -> tuple[list[SourceItem], str | None]:
    """Safety adapter.

    This intentionally does not run Actors unless APIFY_ENABLE_RUNS=true.
    Actor runs can consume credits; choose exact actors and max result budgets
    before enabling this.
    """
    if not config.get("apify", {}).get("enabled", False):
        return [], "disabled"
    if os.environ.get("APIFY_ENABLE_RUNS", "").lower() != "true":
        return [], "APIFY_ENABLE_RUNS is not true; refusing to run paid actors"
    if not os.environ.get("APIFY_TOKEN"):
        return [], "APIFY_TOKEN is not set"
    return [], "Apify actor execution is not implemented until actor + budget are approved"


def normalize_ossinsight_rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("data"), list):
        return [x for x in data["data"] if isinstance(x, dict)]
    if isinstance(data.get("data"), dict) and isinstance(data["data"].get("rows"), list):
        return [x for x in data["data"]["rows"] if isinstance(x, dict)]
    if isinstance(data.get("rows"), list):
        return [x for x in data["rows"] if isinstance(x, dict)]
    return []


def safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("_")[:80] or "query"


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        create table if not exists snapshots (
            id integer primary key autoincrement,
            run_id text not null,
            source text not null,
            fetched_at text not null,
            status text not null,
            item_count integer not null,
            error text
        );

        create table if not exists items (
            id integer primary key autoincrement,
            run_id text not null,
            snapshot_id integer not null,
            source text not null,
            external_id text not null,
            name text not null,
            url text,
            fetched_at text not null,
            heat real,
            velocity real,
            acceleration real,
            source_rank integer,
            description text,
            metadata_json text not null,
            raw_json text not null,
            foreign key(snapshot_id) references snapshots(id)
        );

        create index if not exists idx_items_identity on items(source, external_id, fetched_at);
        create index if not exists idx_items_run on items(run_id);

        create table if not exists scores (
            id integer primary key autoincrement,
            run_id text not null,
            item_id integer not null,
            rank integer not null,
            score real not null,
            components_json text not null,
            foreign key(item_id) references items(id)
        );

        create table if not exists analyses (
            id integer primary key autoincrement,
            run_id text not null,
            item_id integer not null,
            analysis_text text not null,
            judgment text,
            created_at text not null
        );

        create table if not exists settings (
            key text primary key,
            value_json text not null,
            updated_at text not null
        );

        create table if not exists x_tweets_store (
            tweet_id text primary key,
            author_username text not null,
            author_name text,
            text text not null,
            url text,
            created_at text not null,
            metrics_json text not null,
            mentioned_projects_json text not null,
            hashtags_json text not null,
            mentions_json text not null,
            raw_json text not null,
            first_seen_at text not null,
            last_seen_at text not null,
            last_import_run_id text
        );

        create index if not exists idx_x_tweets_created_at on x_tweets_store(created_at);
        create index if not exists idx_x_tweets_author on x_tweets_store(author_username);

        create table if not exists x_account_cursor (
            username text primary key,
            last_seen_tweet_id text,
            last_seen_created_at text,
            last_run_at text,
            last_status text,
            last_error text,
            tweet_count integer not null default 0
        );

        create table if not exists x_archived_import_runs (
            run_id text primary key,
            archived_at text not null,
            reason text
        );
        """
    )
    conn.commit()


def previous_item(conn: sqlite3.Connection, item: SourceItem, fetched_at: str) -> sqlite3.Row | None:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        select heat, velocity, fetched_at
        from items
        where source = ? and external_id = ? and fetched_at < ?
        order by fetched_at desc, id desc
        limit 1
        """,
        (item.source, item.external_id, fetched_at),
    ).fetchone()


def x_tweet_age_hours(created_at: str, now: dt.datetime) -> float | None:
    try:
        created = parse_iso(created_at)
    except Exception:  # noqa: BLE001
        return None
    return max((now - created).total_seconds() / 3600.0, 0.0)


def x_windows_for_created_at(created_at: str, now: dt.datetime, configured_windows: list[str]) -> list[str]:
    age_hours = x_tweet_age_hours(created_at, now)
    if age_hours is None:
        return []
    windows = [
        window
        for window in configured_windows
        if window in X_WINDOW_HOURS and age_hours <= X_WINDOW_HOURS[window]
    ]
    if "30d+" in configured_windows and age_hours > X_WINDOW_HOURS["30d"]:
        windows.append("30d+")
    return windows


def upsert_x_tweet_rows(
    conn: sqlite3.Connection,
    tweet_rows: list[dict[str, Any]],
    *,
    fetched_at: str,
    import_run_id: str | None = None,
) -> int:
    """Persist normalized X tweets by tweet id.

    The Apify actor can return overlapping tweets across daily runs. This store
    deduplicates by tweet_id and lets dashboard windows be computed locally from
    created_at instead of paying to re-fetch the same 30-day window.
    """
    now = fetched_at
    imported = 0
    latest_by_author: dict[str, dict[str, Any]] = {}
    for row in tweet_rows:
        if not isinstance(row, dict):
            continue
        tweet_id = str(row.get("id") or row.get("tweet_id") or row.get("tweetId") or "").strip()
        created_at = str(row.get("created_at") or row.get("createdAt") or "").strip()
        author = str(row.get("author_username") or row.get("username") or "").strip().lstrip("@")
        text = str(row.get("text") or row.get("fullText") or "").strip()
        if not tweet_id or not created_at or not author or not text:
            continue
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        projects = row.get("mentioned_projects") if isinstance(row.get("mentioned_projects"), list) else []
        hashtags = row.get("hashtags") if isinstance(row.get("hashtags"), list) else []
        mentions = row.get("mentions") if isinstance(row.get("mentions"), list) else []
        raw = row.get("raw") if isinstance(row.get("raw"), dict) else row
        conn.execute(
            """
            insert into x_tweets_store(
                tweet_id, author_username, author_name, text, url, created_at,
                metrics_json, mentioned_projects_json, hashtags_json, mentions_json,
                raw_json, first_seen_at, last_seen_at, last_import_run_id
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(tweet_id) do update set
                author_username = excluded.author_username,
                author_name = excluded.author_name,
                text = excluded.text,
                url = excluded.url,
                created_at = excluded.created_at,
                metrics_json = excluded.metrics_json,
                mentioned_projects_json = excluded.mentioned_projects_json,
                hashtags_json = excluded.hashtags_json,
                mentions_json = excluded.mentions_json,
                raw_json = excluded.raw_json,
                last_seen_at = excluded.last_seen_at,
                last_import_run_id = excluded.last_import_run_id
            """,
            (
                tweet_id,
                author,
                row.get("author_name") or "",
                text,
                row.get("url") or (f"https://x.com/{author}/status/{tweet_id}" if author else ""),
                created_at,
                json.dumps(metrics, ensure_ascii=False),
                json.dumps(projects, ensure_ascii=False),
                json.dumps(hashtags, ensure_ascii=False),
                json.dumps(mentions, ensure_ascii=False),
                json.dumps(raw, ensure_ascii=False),
                now,
                now,
                import_run_id or "",
            ),
        )
        imported += 1
        current = latest_by_author.get(author.lower())
        if current is None or str(created_at) > str(current.get("created_at") or ""):
            latest_by_author[author.lower()] = {"username": author, "tweet_id": tweet_id, "created_at": created_at}

    for cursor in latest_by_author.values():
        count = conn.execute(
            "select count(*) from x_tweets_store where lower(author_username) = lower(?)",
            (cursor["username"],),
        ).fetchone()[0]
        conn.execute(
            """
            insert into x_account_cursor(
                username, last_seen_tweet_id, last_seen_created_at, last_run_at,
                last_status, last_error, tweet_count
            )
            values (?, ?, ?, ?, ?, ?, ?)
            on conflict(username) do update set
                last_seen_tweet_id = excluded.last_seen_tweet_id,
                last_seen_created_at = excluded.last_seen_created_at,
                last_run_at = excluded.last_run_at,
                last_status = excluded.last_status,
                last_error = excluded.last_error,
                tweet_count = excluded.tweet_count
            """,
            (
                cursor["username"],
                cursor["tweet_id"],
                cursor["created_at"],
                now,
                "ok",
                None,
                int(count),
            ),
        )
    conn.commit()
    return imported


def x_cursor_since_date(handles: list[str], *, fallback_days: int = 30, safety_days: int = 2) -> str:
    """Return a safe global sinceDate for the X actor.

    The actor accepts one global sinceDate, not a per-account cursor. If every
    selected handle has a cursor we start slightly before the oldest cursor to
    catch late/overlapping results. If any selected handle is new, use the
    fallback cold-start window so the new account gets historical coverage.
    """
    fallback = (utc_now() - dt.timedelta(days=fallback_days)).date().isoformat()
    if not handles or not DB_PATH.exists():
        return fallback
    conn = sqlite3.connect(DB_PATH)
    try:
        init_db(conn)
        lowered = [handle.lower().lstrip("@") for handle in handles if handle]
        if not lowered:
            return fallback
        placeholders = ",".join("?" for _ in lowered)
        rows = conn.execute(
            f"""
            select lower(username) as username, last_seen_created_at
            from x_account_cursor
            where lower(username) in ({placeholders})
            """,
            lowered,
        ).fetchall()
        cursors = {row[0]: row[1] for row in rows if row[1]}
        if len(cursors) < len(set(lowered)):
            return fallback
        parsed = [parse_iso(value) for value in cursors.values()]
        since = min(parsed) - dt.timedelta(days=safety_days)
        return since.date().isoformat()
    finally:
        conn.close()


def x_store_items(config: dict[str, Any], fetched_at: str) -> list[SourceItem]:
    settings = config.get("apify", {}).get("x_tweets", {})
    allowed_handles = selected_x_seed_handle_set(config, int(settings.get("accounts_limit", 50)))
    configured_windows = [
        str(window)
        for window in settings.get("windows", ["24h", "7d", "30d", "30d+"])
        if str(window) in X_WINDOW_HOURS or str(window) == "30d+"
    ] or ["24h", "7d", "30d", "30d+"]
    now = parse_iso(fetched_at)
    limit = int(settings.get("dashboard_tweet_limit", 1500))

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        init_db(conn)
        if "30d+" in configured_windows:
            rows = conn.execute(
                """
                select *
                from x_tweets_store
                where coalesce(last_import_run_id, '') not in (
                    select run_id from x_archived_import_runs
                )
                order by created_at desc, tweet_id desc
                """
            ).fetchall()
        else:
            max_window_hours = max(X_WINDOW_HOURS[window] for window in configured_windows if window in X_WINDOW_HOURS)
            min_created = iso(now - dt.timedelta(hours=max_window_hours))
            rows = conn.execute(
                """
                select *
                from x_tweets_store
                where created_at >= ?
                  and coalesce(last_import_run_id, '') not in (
                    select run_id from x_archived_import_runs
                  )
                order by created_at desc, tweet_id desc
                """,
                (min_created,),
            ).fetchall()
    finally:
        conn.close()

    items: list[SourceItem] = []
    rank = 0
    for row in rows:
        if allowed_handles and str(row["author_username"] or "").strip().lstrip("@").lower() not in allowed_handles:
            continue
        metrics = json.loads(row["metrics_json"] or "{}")
        projects = json.loads(row["mentioned_projects_json"] or "[]")
        hashtags = json.loads(row["hashtags_json"] or "[]")
        mentions = json.loads(row["mentions_json"] or "[]")
        raw = json.loads(row["raw_json"] or "{}")
        age_hours = x_tweet_age_hours(row["created_at"], now)
        for window in x_windows_for_created_at(row["created_at"], now, configured_windows):
            if rank >= limit:
                break
            rank += 1
            author = str(row["author_username"] or "").lstrip("@")
            text = str(row["text"] or "")
            title = f"@{author}: {text[:96]}" if author else text[:110]
            items.append(
                SourceItem(
                    source="x_tweets",
                    external_id=f"{window}:{row['tweet_id']}",
                    name=title or "(empty tweet)",
                    url=str(row["url"] or ""),
                    source_rank=rank,
                    description=text,
                    fetched_at=fetched_at,
                    metadata={
                        "window": window,
                        "tweet_id": row["tweet_id"],
                        "author": author,
                        "author_name": row["author_name"],
                        "author_avatar": (
                            (raw.get("author") or {}).get("profilePicture")
                            if isinstance(raw.get("author"), dict)
                            else None
                        ),
                        "created_at": row["created_at"],
                        "age_hours": round(age_hours or 0, 2),
                        "like_count": metrics.get("likes"),
                        "reply_count": metrics.get("replies"),
                        "retweet_count": metrics.get("retweets"),
                        "quote_count": metrics.get("quotes"),
                        "view_count": metrics.get("views"),
                        "engagement": (
                            int(metrics.get("likes") or 0)
                            + int(metrics.get("replies") or 0)
                            + int(metrics.get("retweets") or 0)
                            + int(metrics.get("quotes") or 0)
                        ),
                        "mentioned_projects": projects,
                        "hashtags": hashtags,
                        "mentions": mentions,
                        "first_seen_at": row["first_seen_at"],
                        "last_seen_at": row["last_seen_at"],
                        "last_import_run_id": row["last_import_run_id"],
                        "from_tweet_store": True,
                    },
                    raw=raw,
                )
            )
    return items


def insert_source_items(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    source: str,
    fetched_at: str,
    items: list[SourceItem],
    error: str | None,
) -> list[int]:
    status = "ok" if error is None else ("partial" if items else "error")
    cur = conn.execute(
        "insert into snapshots(run_id, source, fetched_at, status, item_count, error) values (?, ?, ?, ?, ?, ?)",
        (run_id, source, fetched_at, status, len(items), error),
    )
    snapshot_id = int(cur.lastrowid)

    ids: list[int] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item.source, item.external_id)
        if key in seen:
            continue
        seen.add(key)

        velocity = None
        acceleration = None

        cur = conn.execute(
            """
            insert into items(
                run_id, snapshot_id, source, external_id, name, url, fetched_at,
                heat, velocity, acceleration, source_rank, description, metadata_json, raw_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                snapshot_id,
                item.source,
                item.external_id,
                item.name,
                item.url,
                fetched_at,
                item.heat,
                velocity,
                acceleration,
                item.source_rank,
                item.description,
                json.dumps(item.metadata, ensure_ascii=False),
                json.dumps(item.raw, ensure_ascii=False),
            ),
        )
        ids.append(int(cur.lastrowid))
    conn.commit()
    return ids


def minmax(value: float | None, values: list[float]) -> float:
    if value is None or not values:
        return 0.0
    lo = min(values)
    hi = max(values)
    if hi <= lo:
        return 1.0 if value > 0 else 0.0
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def source_channel(source: str) -> str | None:
    if source in EXCLUDED_ITEM_SOURCES:
        return None
    if source in {"github_movers_trending_repos", "github_movers_repofomo"}:
        return source
    if source == "hn_algolia":
        return "hn_search"
    if source == "hn_firebase":
        return "hn_top"
    if source.startswith("huggingface_"):
        return source
    if source.startswith("pypi_"):
        return source
    if source == "x_tweets":
        return "x_tweets"
    return source


def channel_label(channel: str) -> str:
    return {
        "github_trending": "GitHub Trending",
        "github_movers_trending_repos": "Trending Repos",
        "github_movers_repofomo": "RepoFOMO",
        "github_search": "GitHub Search",
        "hn_search": "HN Search",
        "hn_top": "HN Top",
        "product_hunt": "Product Hunt",
        "huggingface_models": "HF Models",
        "huggingface_datasets": "HF Datasets",
        "huggingface_spaces": "HF Spaces",
        "npm_search": "npm Search",
        "pypi_newest": "PyPI Newest",
        "pypi_updates": "PyPI Updates",
        "x_seed_accounts": "X Accounts",
        "x_tweets": "X Tweets",
        "settings_source_health": "Source Health",
        "settings_search_terms": "Search Terms",
    }.get(channel, channel)


def channel_description(channel: str) -> str:
    return {
        "github_trending": (
            "来源：GitHub Trending 网页。口径：按 GitHub 自己的 daily / weekly / monthly trending 页面抓 repo、描述、语言、总 star、"
            "以及页面显示的 stars today / this week / this month。怎么看：适合看 GitHub 官方趋势榜上的项目；注意它不是全量 GitHub star 速度榜。"
        ),
        "github_movers_trending_repos": (
            "来源：trending-repos.com。口径：抓它的 daily / weekly / monthly momentum 榜，保留 repo、描述、总 star/fork、"
            "scoreComponents 里的 starsVelocity / forksVelocity / freshnessBonus 和 sparkline。怎么看：这是当前最接近“star 动量/曲线形状”的 GitHub 外部榜。"
        ),
        "github_movers_repofomo": (
            "来源：RepoFOMO leaderboard。口径：抓公开榜单里的 FomoRank、repo、总 star、7d / 30d / 60d 新增 star、age 等字段。"
            "怎么看：适合补周级/月级 movers；dashboard 的 7d / 30d / 60d 是 RepoFOMO 原生字段 lens，切换后按对应新增 star 重排当前榜。"
        ),
        "github_search": (
            "来源：GitHub Search API。口径：按 Settings 里的 query 抓 repository search，目前按 stars desc 请求，保留 star、fork、topics、创建/更新时间、README 描述等 API 字段。"
            "怎么看：这是主动关注关键词入口，不是自动 trending；query 设得好坏会直接影响结果。"
        ),
        "hn_search": (
            "来源：HN Algolia API。口径：按 Settings 里的 query 和 24h / 7d / 30d 窗口搜索 story，保留 points、comments、author、created_at、story_text/highlight 等字段。"
            "怎么看：适合看某个概念或关键词在 HN 上是否刚被讨论。"
        ),
        "hn_top": (
            "来源：Hacker News Firebase API。口径：抓 topstories / newstories / beststories 的前 N 条，保留 score、descendants/comments、author、HN item 链接。"
            "怎么看：这是 HN 全站热榜/新榜信号，当前不做 AI 过滤。"
        ),
        "product_hunt": (
            "来源：Product Hunt GraphQL API。口径：按 VOTES 请求 posts，保留 votesCount、commentsCount、dailyRank、weeklyRank、createdAt、featuredAt、website/tagline。"
            "怎么看：偏 launch/消费产品信号，通常比 GitHub/HN 更产品化，但也更容易受 launch 节奏影响。"
        ),
        "huggingface_models": (
            "来源：Hugging Face API models?sort=trendingScore。口径：保留 HF 原生 trendingScore、likes、downloads、pipeline_tag、tags、created/modified。"
            "怎么看：模型资源趋势；当前默认不放主 dashboard，因为目标更偏产品应用层。"
        ),
        "huggingface_datasets": (
            "来源：Hugging Face API datasets?sort=trendingScore。口径：保留 HF 原生 trendingScore、likes、downloads、tags、created/modified。"
            "怎么看：数据集趋势；当前默认不放主 dashboard，因为目标更偏产品应用层。"
        ),
        "huggingface_spaces": (
            "来源：Hugging Face API spaces?sort=trendingScore。口径：保留 HF 原生 trendingScore、likes、sdk、tags、created/modified。"
            "怎么看：比 models/datasets 更接近可体验产品 demo，但仍是 HF 平台内趋势。"
        ),
        "npm_search": (
            "来源：npm registry search API。口径：按 Settings 里的 query 搜包，保留 weekly/monthly downloads、searchScore、quality/popularity/maintenance、links、keywords。"
            "怎么看：适合发现 JS/TS 生态里新工具包或 agent/MCP 相关依赖，不代表产品本身已经出圈。"
        ),
        "pypi_newest": (
            "来源：PyPI 官方 newest packages RSS，并对前 N 条用 PyPI JSON API enrich。口径：RSS 顺序代表新发布，额外保留 summary、classifiers、project_urls、requires_python。"
            "怎么看：适合扫刚发布的 Python 包；不是下载量或增长榜。"
        ),
        "pypi_updates": (
            "来源：PyPI 官方 latest updates RSS，并对前 N 条用 PyPI JSON API enrich。口径：RSS 顺序代表最近更新，额外保留 latest version、classifiers、project_urls 等。"
            "怎么看：适合发现近期活跃维护的 Python 包；更新频繁不等于产品机会。"
        ),
        "x_tweets": (
            "来源：Apify X actor 抓 seed 个人账号 tweets。口径：当前抓 50 个 AI 相关个人账号，最近 30 天，每人最多 30 条，排除 replies；dashboard 按 24h / 7d / 30d 展示。"
            "怎么看：重点读谁说了什么、提到哪些项目或链接；不把 engagement/views 当主指标。"
        ),
        "x_seed_accounts": (
            "来源：你的 X following 候选池和手动 seed list。口径：筛 AI 相关个人账号，去掉官方账号，按 followers_count 等字段排序。"
            "怎么看：这是 X Monitoring 的账号池配置，不是项目榜。"
        ),
        "settings_source_health": "Settings 内部页：展示每个 adapter 本轮是否成功、错误信息、disabled 状态和 token/API 配置情况。",
        "settings_search_terms": "Settings 内部页：展示 GitHub/HN/npm/X keyword queries 等搜索词配置；修改后保存，并在下一次 pipeline run 生效。",
    }.get(channel, f"来源：{channel_label(channel)}。口径：当前没有专门说明，按该 source 的原始返回顺序和字段展示。")


def native_metric(row: sqlite3.Row, metadata: dict[str, Any]) -> dict[str, Any]:
    source = row["source"]
    if source == "github_trending":
        period = metadata.get("period")
        label = {
            "daily": "本窗口新增 star",
            "weekly": "本窗口新增 star",
            "monthly": "本窗口新增 star",
        }.get(period, "窗口新增 star")
        return {
            "label": label,
            "value": metadata.get("period_stars"),
            "help": "GitHub Trending 页面展示的 stars today / this week / this month，不是仓库总 star。",
        }
    if source == "github_search":
        return {"label": "仓库总 star", "value": metadata.get("stars"), "help": "GitHub Search API 返回的当前 stargazers_count。"}
    if source == "github_movers_trending_repos":
        return {
            "label": "TR star速度",
            "value": metadata.get("stars_velocity"),
            "help": "Trending Repos 自己算的 star velocity / momentum 分量，来自它的 daily / weekly / monthly 榜单。",
        }
    if source == "github_movers_repofomo":
        return {
            "label": "7d新增star",
            "value": metadata.get("stars_7d"),
            "help": "RepoFOMO 公开 leaderboard 里的 7-day new stars；同一行详情里也保留 30d / 60d new stars。",
        }
    if source in {"hn_algolia", "hn_firebase"}:
        return {"label": "HN points/comments", "value": metadata.get("points") or metadata.get("score"), "help": "HN 原生数据：Algolia 用 points / num_comments；Firebase 用 score / descendants。"}
    if source == "product_hunt":
        return {"label": "PH votes/comments", "value": metadata.get("votes"), "help": "Product Hunt GraphQL 原生 votesCount / commentsCount。"}
    if source.startswith("huggingface_"):
        return {
            "label": "trendingScore",
            "value": (json.loads(row["raw_json"]) if isinstance(row, sqlite3.Row) and "raw_json" in row.keys() else {}).get("trendingScore"),
            "help": "Hugging Face API 按 trendingScore 排序；若该字段不存在，再看 likes / downloads。",
        }
    if source == "npm_search":
        return {
            "label": "weekly downloads",
            "value": metadata.get("weekly_downloads"),
            "help": "npm registry search 返回的 downloads.weekly，不代表增速；search_score / quality / popularity / maintenance 也在表格中保留。",
        }
    if source == "pypi_newest":
        return {"label": "RSS newest rank", "value": row["source_rank"], "help": "PyPI 官方 newest packages RSS 的当前顺序，越小越新。"}
    if source == "pypi_updates":
        return {"label": "RSS update rank", "value": row["source_rank"], "help": "PyPI 官方 latest updates RSS 的当前顺序，越小越新。"}
    if source == "x_seed_accounts":
        return {"label": "粉丝数", "value": metadata.get("followers_count"), "help": "从你的 following 里筛出的 AI 相关账号，按粉丝数排序。"}
    if source == "x_tweets":
        return {
            "label": "tweet 顺序",
            "value": row["source_rank"],
            "help": "seed accounts 时间窗内抓到的 tweet 顺序；X engagement/views 不作为主判断。",
        }
    return {"label": "原生排序", "value": row["source_rank"], "help": "该 source 的原生顺序或 rank。"}


def row_facts(row: sqlite3.Row, metadata: dict[str, Any]) -> list[str]:
    source = row["source"]
    if source == "github_trending":
        return [
            f"窗口={metadata.get('period')}",
            f"窗口新增star={metadata.get('period_stars')}",
            f"总star={fmt(metadata.get('stars_total'))}",
        ]
    if source == "github_search":
        topics = ", ".join((metadata.get("topics") or [])[:4])
        return [
            f"搜索词={metadata.get('query_label') or metadata.get('query')}",
            f"star={metadata.get('stars')}",
            f"fork={metadata.get('forks')}",
            f"最后push={metadata.get('pushed_at')}",
            f"topics={topics}" if topics else "",
        ]
    if source == "github_movers_trending_repos":
        topics = ", ".join((metadata.get("topics") or [])[:5])
        return [
            f"来源={metadata.get('provider')}",
            f"窗口={metadata.get('period')}",
            f"rank={metadata.get('language_rank') or ''}/{metadata.get('source_score')}",
            f"source_score={fmt(metadata.get('source_score'))}",
            f"star速度={fmt(metadata.get('stars_velocity'))}",
            f"fork速度={fmt(metadata.get('forks_velocity'))}",
            f"最新sparkline={metadata.get('latest_sparkline_delta')}",
            f"topics={topics}" if topics else "",
        ]
    if source == "github_movers_repofomo":
        return [
            f"来源={metadata.get('provider')}",
            f"FomoRank={metadata.get('fomo_rank')}",
            f"7d新增star={fmt(metadata.get('stars_7d'))}",
            f"30d新增star={fmt(metadata.get('stars_30d'))}",
            f"60d新增star={fmt(metadata.get('stars_60d'))}",
            f"总star={fmt(metadata.get('stars_total'))}",
            f"new_forks={fmt(metadata.get('new_forks'))}",
            f"fork增长%={fmt(metadata.get('fork_growth_percent'))}",
            f"star_age天={fmt(metadata.get('star_age_days'))}",
        ]
    if source == "hn_algolia":
        return [
            f"搜索词={metadata.get('query_label') or metadata.get('query')}",
            f"分数={metadata.get('points')}",
            f"评论={metadata.get('comments')}",
            f"作者={metadata.get('author')}",
            f"创建={metadata.get('created_at')}",
        ]
    if source == "hn_firebase":
        return [
            f"榜单={metadata.get('list')}",
            f"分数={metadata.get('score')}",
            f"评论={metadata.get('comments')}",
            f"作者={metadata.get('author')}",
        ]
    if source == "product_hunt":
        return [
            f"票数={metadata.get('votes')}",
            f"评论={metadata.get('comments')}",
            f"日榜={metadata.get('daily_rank')}",
            f"周榜={metadata.get('weekly_rank')}",
            f"featured={metadata.get('featured_at')}",
        ]
    if source.startswith("huggingface_"):
        tags = ", ".join((metadata.get("tags") or [])[:5])
        return [
            f"类型={metadata.get('resource')}",
            f"like={metadata.get('likes')}",
            f"下载={metadata.get('downloads')}",
            f"pipeline={metadata.get('pipeline_tag')}",
            f"tags={tags}" if tags else "",
        ]
    if source == "npm_search":
        keywords = ", ".join((metadata.get("keywords") or [])[:6])
        return [
            f"搜索词={metadata.get('query_label') or metadata.get('query')}",
            f"版本={metadata.get('version')}",
            f"周下载={fmt(metadata.get('weekly_downloads'))}",
            f"月下载={fmt(metadata.get('monthly_downloads'))}",
            f"dependents={fmt(metadata.get('dependents'))}",
            f"score={fmt(metadata.get('score_final'))}",
            f"quality={fmt(metadata.get('score_quality'))}",
            f"popularity={fmt(metadata.get('score_popularity'))}",
            f"maintenance={fmt(metadata.get('score_maintenance'))}",
            f"keywords={keywords}" if keywords else "",
        ]
    if source in {"pypi_newest", "pypi_updates"}:
        classifiers = ", ".join((metadata.get("classifiers") or [])[:4])
        return [
            f"feed={metadata.get('feed')}",
            f"版本={metadata.get('version') or metadata.get('latest_version')}",
            f"发布时间={metadata.get('pub_date')}",
            f"requires_python={metadata.get('requires_python')}",
            f"license={metadata.get('license')}",
            f"classifiers={classifiers}" if classifiers else "",
        ]
    if source == "x_seed_accounts":
        return [
            f"粉丝={metadata.get('followers_count')}",
            f"关注={metadata.get('following_count')}",
            f"AI关键词分={metadata.get('keyword_score')}",
            f"bio={metadata.get('bio')}",
        ]
    if source == "x_tweets":
        projects = metadata.get("mentioned_projects") or []
        project_names = ", ".join(str(p.get("name") or p.get("key")) for p in projects[:5] if isinstance(p, dict))
        return [
            f"作者=@{metadata.get('author')}",
            f"发布时间={metadata.get('created_at')}",
            f"提及={project_names}" if project_names else "",
        ]
    return []


def native_sort_key(row: sqlite3.Row, metadata: dict[str, Any]) -> tuple[Any, ...]:
    source = row["source"]
    window = item_window({"metadata": metadata})
    source_rank = row["source_rank"] if row["source_rank"] is not None else 10_000
    if source == "github_trending":
        scope = str(metadata.get("scope_language") or "all")
        scope_order = 0 if scope == "all" else 1
        return (WINDOW_ORDER.get(window, 99), scope_order, scope, source_rank, row["name"])
    if source == "github_search":
        return (str(metadata.get("query_label") or metadata.get("query") or ""), source_rank, row["name"])
    if source == "hn_algolia":
        return (WINDOW_ORDER.get(window, 99), str(metadata.get("query_label") or metadata.get("query") or ""), source_rank, row["name"])
    if source == "github_movers_trending_repos":
        return (0, WINDOW_ORDER.get(window, 99), source_rank, row["name"])
    if source == "github_movers_repofomo":
        return (1, source_rank, row["name"])
    if source == "hn_firebase":
        list_order = {"topstories": 0, "newstories": 1, "beststories": 2}
        return (list_order.get(str(metadata.get("list")), 99), source_rank, row["name"])
    if source == "huggingface_models":
        return (0, source_rank, row["name"])
    if source == "huggingface_datasets":
        return (1, source_rank, row["name"])
    if source == "huggingface_spaces":
        return (2, source_rank, row["name"])
    if source == "npm_search":
        return (str(metadata.get("query_label") or metadata.get("query") or ""), source_rank, row["name"])
    if source in {"pypi_newest", "pypi_updates"}:
        return (source_rank, row["name"])
    if source == "x_seed_accounts":
        return (source_rank, row["name"])
    if source == "x_tweets":
        return (
            WINDOW_ORDER.get(window, 99),
            1,
            source_rank,
            row["name"],
        )
    return (source_rank, row["name"])


def rank_score(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute("select * from items where run_id = ?", (run_id,)).fetchall()
    return score_rows(conn, rows, run_id, write_scores=True)


def rank_latest_by_item_source(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        select i.*
        from items i
        join (
            select source, max(id) as latest_snapshot_id
            from snapshots
            group by source
        ) latest
          on latest.latest_snapshot_id = i.snapshot_id
        """
    ).fetchall()
    return score_rows(conn, rows, run_id, write_scores=False)


def latest_source_errors(conn: sqlite3.Connection) -> dict[str, str | None]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        select s.source, s.error
        from snapshots s
        join (
            select source, max(id) as latest_id
            from snapshots
            group by source
        ) latest
          on latest.source = s.source
         and latest.latest_id = s.id
        order by s.id
        """
    ).fetchall()
    return {row["source"]: row["error"] for row in rows}


def score_rows(
    conn: sqlite3.Connection,
    rows: list[sqlite3.Row],
    run_id: str,
    *,
    write_scores: bool,
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for r in rows:
        metadata = json.loads(r["metadata_json"])
        raw = json.loads(r["raw_json"])
        channel = source_channel(r["source"])
        if channel is None:
            continue
        metric = native_metric(r, metadata)
        sort_key = native_sort_key(r, metadata)
        scored.append(
            {
                "item_id": r["id"],
                "channel": channel,
                "channel_label": channel_label(channel),
                "source": r["source"],
                "external_id": r["external_id"],
                "name": r["name"],
                "url": r["url"],
                "description": r["description"],
                "source_rank": r["source_rank"],
                "native_metric": metric,
                "facts": [fact for fact in row_facts(r, metadata) if fact],
                "metadata": metadata,
                "raw": compact_raw(r["source"], raw),
                "window": item_window({"metadata": metadata}),
                "_sort_key": sort_key,
            }
        )

    channel_index = {channel: idx for idx, channel in enumerate(CHANNEL_ORDER)}
    scored.sort(key=lambda x: (channel_index.get(x["channel"], 999), x["_sort_key"]))

    channel_ranks: dict[str, int] = {}
    channel_window_ranks: dict[tuple[str, str], int] = {}
    if write_scores:
        conn.execute("delete from scores where run_id = ?", (run_id,))
    for global_rank, row in enumerate(scored, start=1):
        channel_ranks[row["channel"]] = channel_ranks.get(row["channel"], 0) + 1
        window_key = (str(row["channel"]), str(row.get("window") or "current"))
        channel_window_ranks[window_key] = channel_window_ranks.get(window_key, 0) + 1
        row["rank"] = global_rank
        row["channel_rank"] = channel_ranks[row["channel"]]
        row["window_rank"] = channel_window_ranks[window_key]
        row.pop("_sort_key", None)
        if write_scores:
            conn.execute(
                "insert into scores(run_id, item_id, rank, score, components_json) values (?, ?, ?, ?, ?)",
                (
                    run_id,
                    row["item_id"],
                    global_rank,
                    float(row["native_metric"].get("value") or 0),
                    json.dumps({"native_metric": row["native_metric"], "channel": row["channel"]}, ensure_ascii=False),
                ),
            )
    if write_scores:
        conn.commit()
    return scored


def compact_raw(source: str, raw: Any) -> Any:
    if not isinstance(raw, dict):
        return raw
    if source == "github_search":
        license_obj = raw.get("license") if isinstance(raw.get("license"), dict) else {}
        owner_obj = raw.get("owner") if isinstance(raw.get("owner"), dict) else {}
        return {
            "watchers_count": raw.get("watchers_count"),
            "open_issues_count": raw.get("open_issues_count"),
            "license": license_obj.get("spdx_id") or license_obj.get("name"),
            "homepage": raw.get("homepage"),
            "default_branch": raw.get("default_branch"),
            "updated_at": raw.get("updated_at"),
            "created_at": raw.get("created_at"),
            "pushed_at": raw.get("pushed_at"),
            "archived": raw.get("archived"),
            "fork": raw.get("fork"),
            "is_template": raw.get("is_template"),
            "has_issues": raw.get("has_issues"),
            "has_discussions": raw.get("has_discussions"),
            "has_pages": raw.get("has_pages"),
            "size": raw.get("size"),
            "visibility": raw.get("visibility"),
            "owner": owner_obj.get("login"),
        }
    if source.startswith("huggingface_"):
        return {
            "trendingScore": raw.get("trendingScore"),
            "library_name": raw.get("library_name"),
            "modelId": raw.get("modelId"),
            "author": raw.get("author"),
            "gated": raw.get("gated"),
            "disabled": raw.get("disabled"),
            "sha": raw.get("sha"),
            "sdk": raw.get("sdk"),
            "private": raw.get("private"),
        }
    if source == "x_tweets":
        return {k: v for k, v in raw.items() if k != "raw"}
    if source in {"npm_search", "pypi_newest", "pypi_updates"}:
        return raw
    return raw


def settings_rows_from_config(
    config: dict[str, Any],
    source_errors: dict[str, str | None],
    fetched_at: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(channel: str, rank: int, name: str, description: str, metadata: dict[str, Any]) -> None:
        rows.append(
            {
                "item_id": -len(rows) - 1,
                "channel": channel,
                "channel_label": channel_label(channel),
                "source": "settings",
                "external_id": f"{channel}:{rank}:{safe_name(name)}",
                "name": name,
                "url": "",
                "description": description,
                "source_rank": rank,
                "native_metric": {"label": "设置项", "value": rank, "help": "Settings 里的配置或状态行。"},
                "facts": [],
                "metadata": {
                    "window": "current",
                    "takes_effect": "下一次 pipeline run 生效",
                    "default_schedule": "每 24 小时一轮；cron 暂未启用",
                    "fetched_at": fetched_at,
                    **metadata,
                },
                "raw": {},
                "window": "current",
                "rank": 0,
                "channel_rank": rank,
            }
        )

    for rank, (source, error) in enumerate(source_errors.items(), start=1):
        add(
            "settings_source_health",
            rank,
            source,
            error or "正常",
            {
                "setting_type": "source_health",
                "source_name": source,
                "status": "注意" if error else "正常",
                "note": error or "",
            },
        )

    search_rank = 1

    def add_query(group: str, label: str, query: str, enabled: bool = True) -> None:
        nonlocal search_rank
        add(
            "settings_search_terms",
            search_rank,
            label or query,
            query,
            {
                "setting_type": "search_term",
                "group": group,
                "query": query,
                "enabled": enabled,
            },
        )
        search_rank += 1

    add_query("system", "default cadence", "每 24 小时一轮；当前没有启用 cron。")
    for query_config in config.get("github_search", {}).get("queries", []):
        label, query = query_entry(query_config)
        add_query("GitHub Search", label, query)
    for query_config in config.get("hn", {}).get("algolia_queries", []):
        label, query = query_entry(query_config)
        add_query("HN Algolia", label, query)
    for query_config in config.get("npm", {}).get("queries", []):
        label, query = query_entry(query_config)
        add_query("npm Search", label, query)
    for idx, query in enumerate(config.get("apify", {}).get("x_keyword_queries", []), start=1):
        add_query("X keyword queries", f"X keyword {idx}", str(query), bool(config.get("apify", {}).get("enabled", False)))
    return rows


def api_status_payload() -> dict[str, dict[str, Any]]:
    return {
        "github": {
            "label": "GitHub token",
            "configured": bool(os.environ.get("GITHUB_TOKEN")),
            "env": "GITHUB_TOKEN",
            "note": "提高 GitHub Search/Core API rate limit；不显示 token 明文。",
        },
        "product_hunt": {
            "label": "Product Hunt token",
            "configured": bool(os.environ.get("PRODUCTHUNT_TOKEN")),
            "env": "PRODUCTHUNT_TOKEN",
            "note": "启用 Product Hunt GraphQL collection；不显示 token 明文。",
        },
        "product_hunt_context": {
            "label": "Product Hunt user context",
            "configured": bool(os.environ.get("PRODUCTHUNT_USER_CONTEXT")),
            "env": "PRODUCTHUNT_USER_CONTEXT",
            "note": "PH 可选 user context；不显示明文。",
        },
        "apify": {
            "label": "Apify token",
            "configured": bool(os.environ.get("APIFY_TOKEN")),
            "env": "APIFY_TOKEN",
            "note": "用于手动 X following / X tweets actors；不显示 token 明文。",
        },
        "apify_runs": {
            "label": "Apify paid run gate",
            "configured": os.environ.get("APIFY_ENABLE_RUNS", "").lower() == "true",
            "env": "APIFY_ENABLE_RUNS",
            "note": "必须是 true 才允许真正执行付费 Apify actor。",
        },
        "deepseek": {
            "label": "DeepSeek key",
            "configured": bool(os.environ.get("DEEPSEEK_API_KEY")),
            "env": "DEEPSEEK_API_KEY",
            "note": "后续 LLM 分析阶段使用；当前数据 dashboard 不依赖它。",
        },
    }


def export_latest(scored: list[dict[str, Any]], run_id: str, fetched_at: str, source_errors: dict[str, str | None]) -> None:
    json_path = EXPORT_DIR / "latest_items.json"
    json_path.write_text(json.dumps({"run_id": run_id, "fetched_at": fetched_at, "items": scored}, ensure_ascii=False, indent=2))

    lines = [
        f"# Hero Radar Latest Scores",
        "",
        f"- Run: `{run_id}`",
        f"- Fetched at: `{fetched_at}`",
        "",
        "## Source Status",
        "",
        "| Source | Status | Note |",
        "|---|---:|---|",
    ]
    for source, error in source_errors.items():
        status = "ok" if not error else "partial/error"
        lines.append(f"| `{source}` | {status} | {error or ''} |")
    lines.extend(
        [
            "",
            "## Channel Facts",
            "",
            "| Channel Rank | Channel | Window | Source | Name | Native Metric | Facts |",
            "|---:|---|---|---|---|---|---|",
        ]
    )
    per_channel_count: dict[str, int] = {}
    for row in scored:
        channel = row["channel"]
        per_channel_count[channel] = per_channel_count.get(channel, 0) + 1
        if per_channel_count[channel] > 20:
            continue
        name = (row["name"] or "").replace("|", "\\|")
        url = row["url"] or ""
        linked = f"[{name}]({url})" if url else name
        metric = row.get("native_metric") or {}
        metric_text = f"{metric.get('label')}: {fmt(metric.get('value'))}".replace("|", "\\|")
        facts = "; ".join(row.get("facts") or []).replace("|", "\\|")
        lines.append(
            f"| {row['channel_rank']} | {row['channel_label']} | `{row.get('window') or 'current'}` | `{row['source']}` | {linked} | "
            f"{metric_text} | {facts} |"
        )
    (EXPORT_DIR / "latest_scores.md").write_text("\n".join(lines) + "\n")
    export_dashboard_v3(scored, run_id, fetched_at, source_errors)


def export_dashboard_legacy(scored: list[dict[str, Any]], run_id: str, fetched_at: str, source_errors: dict[str, str | None]) -> None:
    channel_counts: dict[str, int] = {}
    window_counts: dict[str, int] = {}
    for row in scored:
        channel_counts[row["channel"]] = channel_counts.get(row["channel"], 0) + 1
        window = row.get("window") or "current"
        window_counts[window] = window_counts.get(window, 0) + 1
    channels = [
        {"id": channel, "label": channel_label(channel), "count": channel_counts[channel]}
        for channel in CHANNEL_ORDER
        if channel in channel_counts and channel not in SOURCE_DASHBOARD_HIDDEN_CHANNELS
    ]
    channels.extend(
        {"id": channel, "label": channel_label(channel), "count": count}
        for channel, count in sorted(channel_counts.items())
        if channel not in CHANNEL_ORDER and channel not in SOURCE_DASHBOARD_HIDDEN_CHANNELS
    )
    settings_channels = [
        {"id": channel, "label": channel_label(channel), "count": channel_counts[channel]}
        for channel in CHANNEL_ORDER
        if channel in channel_counts and channel in SETTINGS_CHANNELS
    ]
    settings_channels.extend(
        {"id": channel, "label": channel_label(channel), "count": count}
        for channel, count in sorted(channel_counts.items())
        if channel not in CHANNEL_ORDER and channel in SETTINGS_CHANNELS
    )

    payload = {
        "run_id": run_id,
        "fetched_at": fetched_at,
        "source_errors": source_errors,
        "channel_counts": channel_counts,
        "channels": channels,
        "settings_channels": settings_channels,
        "window_counts": window_counts,
        "items": scored,
    }
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html_text = f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Hero Radar Dashboard</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #101113;
      --panel: #181a1f;
      --panel2: #20242b;
      --text: #e9edf2;
      --muted: #97a0ad;
      --line: #303640;
      --good: #7dd3a8;
      --warn: #f0c36d;
      --accent: #8fb8ff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif;
    }}
    header {{
      padding: 20px 24px 12px;
      border-bottom: 1px solid var(--line);
      position: sticky;
      top: 0;
      background: rgba(16,17,19,.94);
      backdrop-filter: blur(10px);
      z-index: 2;
    }}
    h1 {{ margin: 0 0 6px; font-size: 22px; }}
    .meta {{ color: var(--muted); }}
    main {{ padding: 18px 24px 40px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin: 12px 0 18px; }}
    .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 12px; }}
    .card .label {{ color: var(--muted); font-size: 12px; }}
    .card .value {{ font-size: 24px; margin-top: 4px; }}
    .tabs {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 14px 0; }}
    button {{
      border: 1px solid var(--line);
      color: var(--text);
      background: var(--panel2);
      border-radius: 8px;
      padding: 7px 10px;
      cursor: pointer;
    }}
    button.active {{ border-color: var(--accent); color: white; box-shadow: inset 0 0 0 1px var(--accent); }}
    table {{ width: 100%; border-collapse: collapse; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 9px 10px; vertical-align: top; }}
    th {{ color: var(--muted); text-align: left; font-size: 12px; background: #15171b; position: sticky; top: 80px; z-index: 1; }}
    td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    a {{ color: var(--accent); text-decoration: none; }}
    .pill {{ display: inline-block; border: 1px solid var(--line); border-radius: 999px; padding: 2px 7px; color: var(--muted); font-size: 12px; }}
    .why {{ color: var(--muted); font-size: 12px; }}
    .error {{ display: none; margin: 12px 0; padding: 12px; border: 1px solid #7f5533; background: #261c14; color: #ffd8a8; border-radius: 8px; }}
    .bar {{ height: 6px; background: #2a3039; border-radius: 999px; overflow: hidden; margin-top: 5px; }}
    .fill {{ height: 100%; background: var(--accent); width: 0; }}
    .status-ok {{ color: var(--good); }}
    .status-bad {{ color: var(--warn); }}
    @media (max-width: 760px) {{
      th:nth-child(6), td:nth-child(6),
      th:nth-child(7), td:nth-child(7),
      th:nth-child(8), td:nth-child(8) {{ display: none; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Hero Radar</h1>
    <div class=\"meta\">Run <code>{html.escape(run_id)}</code> · {html.escape(fetched_at)}</div>
    <div class=\"tabs\" id=\"tabs\"></div>
  </header>
  <main>
    <div class=\"error\" id=\"error\"></div>
    <section class=\"grid\" id=\"cards\"></section>
    <table>
      <thead>
        <tr>
          <th class=\"num\">#</th>
          <th>Window</th>
          <th>Source</th>
          <th>Name</th>
          <th>Native Metric</th>
          <th class=\"num\">Heat</th>
          <th class=\"num\">Velocity</th>
          <th class=\"num\">Acceleration</th>
          <th>Facts</th>
        </tr>
      </thead>
      <tbody id=\"rows\"></tbody>
    </table>
  </main>
  <script id=\"data\" type=\"application/json\">{data_json}</script>
  <script>
    const errorBox = document.getElementById('error');
    function showError(message) {{
      errorBox.style.display = 'block';
      errorBox.textContent = message;
    }}
    let data;
    try {{
      data = JSON.parse(document.getElementById('data').textContent);
    }} catch (error) {{
      showError(`Dashboard data failed to parse: ${{error.message}}`);
      throw error;
    }}
    const channels = data.channels || [];
    const settingsChannels = data.settings_channels || [];
    const allChannels = [...channels, ...settingsChannels];
    let active = channels[0]?.id || settingsChannels[0]?.id || '';
    function fmt(v) {{
      if (v === null || v === undefined || Number.isNaN(Number(v))) return '';
      const n = Number(v);
      return Math.abs(n) >= 1000 ? Math.round(n).toLocaleString() : n.toFixed(2);
    }}
    function escapeText(value) {{
      return String(value ?? '').replace(/[&<>\"']/g, ch => ({{
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '\"': '&quot;',
        "'": '&#039;'
      }}[ch]));
    }}
    function escapeUrl(value) {{
      const text = String(value || '#');
      return text.startsWith('http://') || text.startsWith('https://') ? text.replace(/\"/g, '%22') : '#';
    }}
    function rowWindow(item) {{ return item.window || 'current'; }}
    function channelRows() {{
      return data.items.filter(item => item.channel === active);
    }}
    function filtered() {{
      return channelRows().slice(0, 100);
    }}
    function renderTabs() {{
      const tabs = document.getElementById('tabs');
      tabs.innerHTML = '';
      channels.forEach(channel => {{
        const btn = document.createElement('button');
        btn.className = channel.id === active ? 'active' : '';
        btn.textContent = `${{channel.label}} (${{channel.count}})`;
        btn.onclick = () => {{ active = channel.id; render(); }};
        tabs.appendChild(btn);
      }});
    }}
    function renderCards() {{
      const cards = document.getElementById('cards');
      const ok = Object.entries(data.source_errors).filter(([, e]) => !e).length;
      const bad = Object.entries(data.source_errors).filter(([, e]) => e).length;
      const items = [
        ['Rows', filtered().length],
        ['Channel', channels.find(c => c.id === active)?.label || ''],
        ['Sources OK', ok],
        ['Sources partial', bad],
        ['Total collected', data.items.length],
      ];
      cards.innerHTML = items.map(([label, value]) => `<div class=\"card\"><div class=\"label\">${{label}}</div><div class=\"value\">${{value}}</div></div>`).join('');
    }}
    function renderRows() {{
      const tbody = document.getElementById('rows');
      tbody.innerHTML = '';
      filtered().forEach((item, idx) => {{
        const metric = item.native_metric || {{}};
        const metricText = `${{metric.label || 'metric'}}: ${{fmt(metric.value)}}`;
        const facts = (item.facts || []).map(escapeText).join(' · ');
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td class=\"num\">${{item.channel_rank || idx + 1}}</td>
          <td><span class=\"pill\">${{rowWindow(item)}}</span></td>
          <td><span class=\"pill\">${{escapeText(item.source)}}</span></td>
          <td><a href=\"${{escapeUrl(item.url)}}\" target=\"_blank\" rel=\"noreferrer\">${{escapeText(item.name)}}</a></td>
          <td>${{escapeText(metricText)}}</td>
          <td class=\"num\">${{fmt(item.heat)}}</td>
          <td class=\"num\">${{fmt(item.velocity)}}</td>
          <td class=\"num\">${{fmt(item.acceleration)}}</td>
          <td><div class=\"why\">${{facts}}</div></td>
        `;
        tbody.appendChild(tr);
      }});
    }}
    function render() {{ renderTabs(); renderCards(); renderRows(); }}
    render();
  </script>
</body>
</html>
"""
    (EXPORT_DIR / "dashboard.html").write_text(html_text)


def export_dashboard_v2(scored: list[dict[str, Any]], run_id: str, fetched_at: str, source_errors: dict[str, str | None]) -> None:
    channel_counts: dict[str, int] = {}
    window_counts: dict[str, int] = {}
    for row in scored:
        channel_counts[row["channel"]] = channel_counts.get(row["channel"], 0) + 1
        window = row.get("window") or "current"
        window_counts[window] = window_counts.get(window, 0) + 1

    channels = [
        {"id": channel, "label": channel_label(channel), "count": channel_counts[channel]}
        for channel in CHANNEL_ORDER
        if channel in channel_counts
    ]
    channels.extend(
        {"id": channel, "label": channel_label(channel), "count": count}
        for channel, count in sorted(channel_counts.items())
        if channel not in CHANNEL_ORDER
    )

    payload = {
        "run_id": run_id,
        "fetched_at": fetched_at,
        "source_errors": source_errors,
        "channel_counts": channel_counts,
        "channels": channels,
        "window_counts": window_counts,
        "items": scored,
    }
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html_text = f"""<!doctype html>
<html lang=\"zh-CN\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Hero Radar 数据面板</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0f1115;
      --header: rgba(15, 17, 21, .94);
      --panel: #181b21;
      --panel2: #222732;
      --table-head: #15181e;
      --text: #ebeff5;
      --muted: #9ba5b4;
      --line: #303743;
      --good: #77c59b;
      --warn: #dfb56c;
      --accent: #70b8ff;
      --shadow: 0 18px 40px rgba(0,0,0,.24);
      --tooltip-bg: #eff3f8;
      --tooltip-text: #11151c;
    }}
    body[data-theme=\"light\"] {{
      color-scheme: light;
      --bg: #f5f2eb;
      --header: rgba(245, 242, 235, .94);
      --panel: #fffdf8;
      --panel2: #ece7dc;
      --table-head: #eee9df;
      --text: #17191d;
      --muted: #68707b;
      --line: #d5cec0;
      --good: #2f855a;
      --warn: #a36411;
      --accent: #1266a8;
      --shadow: 0 18px 40px rgba(64,48,26,.10);
      --tooltip-bg: #17191d;
      --tooltip-text: #fffdf8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, \"PingFang SC\", \"Segoe UI\", sans-serif;
    }}
    header {{
      padding: 18px 24px 12px;
      border-bottom: 1px solid var(--line);
      position: sticky;
      top: 0;
      background: var(--header);
      backdrop-filter: blur(10px);
      z-index: 3;
    }}
    .header-row {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
    }}
    h1 {{ margin: 0 0 6px; font-size: 22px; letter-spacing: 0; }}
    .meta {{ color: var(--muted); }}
    main {{ padding: 16px 24px 40px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(176px, 1fr)); gap: 10px; margin: 10px 0; }}
    .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; box-shadow: var(--shadow); min-height: 72px; }}
    .card .label {{ color: var(--muted); font-size: 12px; display: flex; align-items: center; gap: 6px; }}
    .card .value {{ font-size: 21px; margin-top: 4px; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }}
    .card .sub {{ color: var(--muted); font-size: 12px; margin-top: 3px; }}
    .controls {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      margin: 4px 0 12px;
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: var(--shadow);
    }}
    .control-group {{ display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }}
    .control-label {{ color: var(--muted); font-size: 12px; margin-right: 2px; }}
    .tabs {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 14px 0; }}
    button {{
      border: 1px solid var(--line);
      color: var(--text);
      background: var(--panel2);
      border-radius: 8px;
      padding: 7px 10px;
      cursor: pointer;
      font: inherit;
    }}
    button:hover {{ border-color: var(--accent); }}
    button.active {{ border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }}
    .control-button {{ padding: 5px 8px; font-size: 12px; }}
    .theme-button {{ white-space: nowrap; }}
    .status-list {{
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 6px;
      margin: 2px 0 12px;
    }}
    .status-pill {{
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 999px;
      padding: 4px 8px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.2;
      max-width: 240px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .status-pill strong {{ color: var(--text); }}
    .status-pill.problem {{ border-color: color-mix(in srgb, var(--warn) 55%, var(--line)); }}
    .status-details {{ flex-basis: 100%; color: var(--muted); font-size: 12px; margin-top: 2px; }}
    .status-details summary {{ color: var(--accent); cursor: pointer; width: fit-content; }}
    .status-detail-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 6px; margin-top: 8px; }}
    .status-error-line {{
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      padding: 8px 10px;
      overflow-wrap: anywhere;
    }}
    table {{ width: 100%; border-collapse: collapse; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; box-shadow: var(--shadow); }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 9px 10px; vertical-align: top; }}
    th {{ color: var(--muted); text-align: left; font-size: 12px; background: var(--table-head); }}
    td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    a {{ color: var(--accent); text-decoration: none; }}
    .pill {{ display: inline-block; border: 1px solid var(--line); border-radius: 999px; padding: 2px 7px; color: var(--muted); font-size: 12px; }}
    .why {{ color: var(--muted); font-size: 12px; }}
    details {{ color: var(--muted); }}
    summary {{ color: var(--accent); cursor: pointer; }}
    .detail-block {{ margin-top: 8px; max-width: 660px; }}
    .detail-block p {{ margin: 0 0 8px; }}
    .sample {{ margin: 6px 0 0; padding: 7px 8px; border-left: 2px solid var(--line); background: var(--panel2); border-radius: 4px; }}
    .error {{ display: none; margin: 12px 0; padding: 12px; border: 1px solid var(--warn); background: var(--panel); color: var(--warn); border-radius: 8px; }}
    .status-ok {{ color: var(--good); }}
    .status-bad {{ color: var(--warn); }}
    .hint {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 16px;
      height: 16px;
      border: 1px solid var(--line);
      border-radius: 50%;
      color: var(--muted);
      font-size: 11px;
      line-height: 1;
      position: relative;
      cursor: help;
      flex: 0 0 auto;
    }}
    .hint::after {{
      content: attr(data-tip);
      display: none;
      position: absolute;
      left: 50%;
      top: 22px;
      transform: translateX(-50%);
      width: min(460px, 78vw);
      padding: 8px 9px;
      border-radius: 6px;
      background: var(--tooltip-bg);
      color: var(--tooltip-text);
      box-shadow: var(--shadow);
      white-space: normal;
      z-index: 10;
      text-align: left;
      font-weight: 400;
      line-height: 1.35;
    }}
    .hint:hover::after {{ display: block; }}
    @media (max-width: 760px) {{
      th:nth-child(6), td:nth-child(6),
      th:nth-child(7), td:nth-child(7),
      th:nth-child(8), td:nth-child(8) {{ display: none; }}
      header {{ position: static; }}
      th {{ position: static; }}
      .header-row {{ flex-direction: column; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class=\"header-row\">
      <div>
        <h1>Hero Radar 数据面板</h1>
        <div class=\"meta\">Run <code>{html.escape(run_id)}</code> · 抓取时间 {html.escape(fetched_at)}</div>
      </div>
          <button class=\"theme-button\" id=\"themeToggle\" type=\"button\">切换深色</button>
    </div>
    <div class=\"tabs\" id=\"tabs\"></div>
  </header>
  <main>
    <div class=\"error\" id=\"error\"></div>
    <section class=\"grid\" id=\"cards\"></section>
    <section class=\"controls\" id=\"controls\"></section>
    <section class=\"status-list\" id=\"statusList\"></section>
    <table>
      <thead>
        <tr>
          <th class=\"num\">频道排名 <span class=\"hint\" data-tip=\"当前 tab 里面按这个 source 自己的原生排序排出来的名次，不是跨渠道加权榜。\">?</span></th>
          <th>时间窗 <span class=\"hint\" data-tip=\"24h / 7d / 30d 表示这个条目对应的榜单窗口；current 表示 source 只给当前快照。\">?</span></th>
          <th>来源 <span class=\"hint\" data-tip=\"真实数据入口，例如 GitHub Trending、HN Firebase、X tweets。\">?</span></th>
          <th>条目</th>
          <th>原生指标 <span class=\"hint\" data-tip=\"每个 source 自己最有意义的数字，比如 GitHub Trending 的窗口新增 star，HN 的分数+评论，X 的提及 tweet 数。\">?</span></th>
          <th class=\"num\">热度 <span class=\"hint\" data-tip=\"这个渠道的当前量级。不同 source 口径不同，优先看原生指标解释。\">?</span></th>
          <th class=\"num\">速度 <span class=\"hint\" data-tip=\"单位时间增量。GitHub Trending 用窗口新增 star / 小时；其他 source 多轮采集后会从热度变化推导。\">?</span></th>
          <th class=\"num\">加速度 <span class=\"hint\" data-tip=\"速度的变化率。需要同一条目至少两次快照才会出现；空值代表还没有足够历史。\">?</span></th>
          <th>详情 <span class=\"hint\" data-tip=\"尽量把已收集到的 source 原始字段放这里：搜索词、作者、评论、样例 tweet、账号等。\">?</span></th>
        </tr>
      </thead>
      <tbody id=\"rows\"></tbody>
    </table>
  </main>
  <script id=\"data\" type=\"application/json\">{data_json}</script>
  <script>
    const errorBox = document.getElementById('error');
    const themeButton = document.getElementById('themeToggle');
    const railToggle = document.getElementById('railToggle');
    function showError(message) {{
      errorBox.style.display = 'block';
      errorBox.textContent = message;
    }}
    let data;
    try {{
      data = JSON.parse(document.getElementById('data').textContent);
    }} catch (error) {{
      showError(`Dashboard data failed to parse: ${{error.message}}`);
      throw error;
    }}
    const channels = data.channels || [];
    const settingsChannels = data.settings_channels || [];
    const allChannels = [...channels, ...settingsChannels];
    let active = channels[0]?.id || settingsChannels[0]?.id || '';
    const channelState = {{}};
    const sortOptions = [
      ['native', '原生顺序'],
      ['metric', '原生指标'],
      ['velocity', '速度'],
      ['acceleration', '加速度'],
      ['heat', '热度'],
      ['name', '名称'],
    ];
    const savedTheme = localStorage.getItem('heroRadarTheme') || 'light';
    document.body.dataset.theme = savedTheme;
    updateThemeButton();
    function fmt(v) {{
      if (v === null || v === undefined || Number.isNaN(Number(v))) return '';
      const n = Number(v);
      return Math.abs(n) >= 1000 ? Math.round(n).toLocaleString() : n.toFixed(2);
    }}
    function escapeText(value) {{
      return String(value ?? '').replace(/[&<>\"']/g, ch => ({{
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '\"': '&quot;',
        "'": '&#039;'
      }}[ch]));
    }}
    function escapeUrl(value) {{
      const text = String(value || '#');
      return text.startsWith('http://') || text.startsWith('https://') ? text.replace(/\"/g, '%22') : '#';
    }}
    function updateThemeButton() {{
      const isLight = document.body.dataset.theme === 'light';
      themeButton.textContent = isLight ? '切换深色' : '切换浅色';
    }}
    themeButton.onclick = () => {{
      const next = document.body.dataset.theme === 'light' ? 'dark' : 'light';
      document.body.dataset.theme = next;
      localStorage.setItem('heroRadarTheme', next);
      updateThemeButton();
    }};
    function rowWindow(item) {{ return item.window || 'current'; }}
    function currentState() {{
      if (!channelState[active]) channelState[active] = {{ window: 'all', sort: 'native' }};
      return channelState[active];
    }}
    function channelRows() {{
      return data.items.filter(item => item.channel === active);
    }}
    function availableWindows() {{
      const seen = new Set(channelRows().map(rowWindow));
      const order = ['24h', '7d', '30d', '30d+', 'current', '7d+30d+60d'];
      return [...seen].sort((a, b) => {{
        const ai = order.indexOf(a);
        const bi = order.indexOf(b);
        return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi) || String(a).localeCompare(String(b));
      }});
    }}
    function windowedRows() {{
      const state = currentState();
      const rows = channelRows();
      if (state.window === 'all') return rows;
      return rows.filter(item => rowWindow(item) === state.window);
    }}
    function sortValue(item, mode) {{
      if (mode === 'metric') return Number(item.native_metric?.value ?? -Infinity);
      if (mode === 'velocity') return Number(item.velocity ?? -Infinity);
      if (mode === 'acceleration') return Number(item.acceleration ?? -Infinity);
      if (mode === 'heat') return Number(item.heat ?? -Infinity);
      if (mode === 'name') return String(item.name || '').toLowerCase();
      return Number(item.channel_rank || 999999);
    }}
    function sortedRows() {{
      const state = currentState();
      const rows = [...windowedRows()];
      rows.sort((a, b) => {{
        if (state.sort === 'name') return sortValue(a, 'name').localeCompare(sortValue(b, 'name'));
        if (state.sort === 'native') return sortValue(a, 'native') - sortValue(b, 'native');
        const diff = sortValue(b, state.sort) - sortValue(a, state.sort);
        return diff || sortValue(a, 'native') - sortValue(b, 'native');
      }});
      return rows;
    }}
    function filtered() {{
      return sortedRows().slice(0, 100);
    }}
    function windowSummary(rows) {{
      const counts = {{}};
      rows.forEach(item => counts[rowWindow(item)] = (counts[rowWindow(item)] || 0) + 1);
      return Object.entries(counts).map(([k, v]) => `${{k}}:${{v}}`).join(' / ') || '-';
    }}
    function tip(text) {{
      return `<span class=\"hint\" data-tip=\"${{escapeText(text)}}\">?</span>`;
    }}
    function renderTabs() {{
      const tabs = document.getElementById('tabs');
      tabs.innerHTML = '';
      channels.forEach(channel => {{
        const btn = document.createElement('button');
        btn.className = channel.id === active ? 'active' : '';
        btn.textContent = `${{channel.label}} (${{channel.count}})`;
        btn.onclick = () => {{ active = channel.id; render(); }};
        tabs.appendChild(btn);
      }});
    }}
    function renderControls() {{
      const controls = document.getElementById('controls');
      const state = currentState();
      const windows = availableWindows();
      const windowButtons = [['all', '全部'], ...windows.map(w => [w, w])].map(([value, label]) => `
        <button type=\"button\" class=\"control-button ${{state.window === value ? 'active' : ''}}\" data-control=\"window\" data-value=\"${{escapeText(value)}}\">${{escapeText(label)}}</button>
      `).join('');
      const sortButtons = sortOptions.map(([value, label]) => `
        <button type=\"button\" class=\"control-button ${{state.sort === value ? 'active' : ''}}\" data-control=\"sort\" data-value=\"${{escapeText(value)}}\">${{escapeText(label)}}</button>
      `).join('');
      controls.innerHTML = `
        <div class=\"control-group\"><span class=\"control-label\">时间</span>${{windowButtons}}</div>
        <div class=\"control-group\"><span class=\"control-label\">排序</span>${{sortButtons}}</div>
      `;
      controls.querySelectorAll('button').forEach(btn => {{
        btn.onclick = () => {{
          const control = btn.dataset.control;
          const value = btn.dataset.value;
          if (control === 'window') state.window = value;
          if (control === 'sort') state.sort = value;
          render();
        }};
      }});
    }}
    function renderCards() {{
      const cards = document.getElementById('cards');
      const ok = Object.entries(data.source_errors).filter(([, e]) => !e).length;
      const bad = Object.entries(data.source_errors).filter(([, e]) => e).length;
      const channelTotal = channelRows().length;
      const rows = windowedRows();
      const visibleRows = filtered().length;
      const items = [
        {{label: '当前筛选条目', value: rows.length, sub: `频道总数 ${{channelTotal}} · 展示前 ${{visibleRows}} 行`, help: '当前频道在时间筛选后的条目数；表格为了性能只展示前 100 行。'}},
        {{label: '当前频道', value: channels.find(c => c.id === active)?.label || '', sub: '不做跨渠道加权', help: '每个 tab 保持 source 自己的事实和排序。'}},
        {{label: '时间窗分布', value: windowSummary(rows), sub: '24h / 7d / 30d / current', help: '当前频道里各时间窗分别有多少行。'}},
        {{label: '成功数据源', value: ok, sub: '本轮无错误返回', help: 'adapter 没有报错的 source 数。'}},
        {{label: '需注意源', value: bad, sub: 'disabled / 缺文件 / API 错误都算', help: '这里不一定是失败；例如付费 Apify 默认禁用也会显示在这里。'}},
        {{label: '总收集条目', value: data.items.length, sub: '所有频道合计', help: '这次 pipeline 写入并导出的全部行数。'}},
      ];
      cards.innerHTML = items.map(item => `
        <div class=\"card\">
          <div class=\"label\">${{escapeText(item.label)}} ${{tip(item.help)}}</div>
          <div class=\"value\">${{escapeText(item.value)}}</div>
          <div class=\"sub\">${{escapeText(item.sub)}}</div>
        </div>
      `).join('');
    }}
    function renderStatus() {{
      const box = document.getElementById('statusList');
      const entries = Object.entries(data.source_errors || {{}});
      const pills = entries.map(([source, err]) => {{
        const status = err ? '注意' : '正常';
        const cls = err ? 'status-bad' : 'status-ok';
        const problem = err ? ' problem' : '';
        const title = err ? ` title=\"${{escapeText(err)}}\"` : '';
        return `<div class=\"status-pill${{problem}}\"${{title}}><strong>${{escapeText(source)}}</strong> · <span class=\"${{cls}}\">${{status}}</span></div>`;
      }}).join('');
      const errors = entries.filter(([, err]) => err);
      const details = errors.length ? `
        <details class=\"status-details\">
          <summary>查看 ${{errors.length}} 个 source 注意项</summary>
          <div class=\"status-detail-grid\">
            ${{errors.map(([source, err]) => `<div class=\"status-error-line\"><strong>${{escapeText(source)}}</strong><br>${{escapeText(err)}}</div>`).join('')}}
          </div>
        </details>
      ` : '';
      box.innerHTML = pills + details;
    }}
    function projectText(projects) {{
      if (!Array.isArray(projects) || projects.length === 0) return '';
      return projects.map(p => {{
        if (!p || typeof p !== 'object') return '';
        return p.name || p.key || '';
      }}).filter(Boolean).slice(0, 8).join('，');
    }}
    function samplesHtml(samples) {{
      if (!Array.isArray(samples) || samples.length === 0) return '';
      return samples.slice(0, 3).map(sample => {{
        const author = sample.author ? `@${{escapeText(sample.author)}} · ` : '';
        const text = escapeText(sample.text || '');
        const url = escapeUrl(sample.url || '');
        const link = url !== '#' ? ` <a href=\"${{url}}\" target=\"_blank\" rel=\"noreferrer\">原文</a>` : '';
        return `<div class=\"sample\">${{author}}${{text}}${{link}}</div>`;
      }}).join('');
    }}
    function detailHtml(item) {{
      const metadata = item.metadata || {{}};
      const facts = (item.facts || []).map(escapeText).join(' · ');
      const desc = item.description ? `<p>${{escapeText(item.description)}}</p>` : '';
      const projects = projectText(metadata.mentioned_projects);
      const projectLine = projects ? `<p>提及项目/账号：${{escapeText(projects)}}</p>` : '';
      const samples = samplesHtml(metadata.sample_tweets);
      return `<details><summary>查看</summary><div class=\"detail-block\">${{desc}}${{projectLine}}<p class=\"why\">${{facts}}</p>${{samples}}</div></details>`;
    }}
    function renderRows() {{
      const tbody = document.getElementById('rows');
      tbody.innerHTML = '';
      filtered().forEach((item, idx) => {{
        const metric = item.native_metric || {{}};
        const metricText = `${{metric.label || 'metric'}}: ${{fmt(metric.value)}}`;
        const metricTitle = metric.help || '';
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td class=\"num\">${{item.channel_rank || idx + 1}}</td>
          <td><span class=\"pill\">${{rowWindow(item)}}</span></td>
          <td><span class=\"pill\">${{escapeText(item.source)}}</span></td>
          <td><a href=\"${{escapeUrl(item.url)}}\" target=\"_blank\" rel=\"noreferrer\">${{escapeText(item.name)}}</a></td>
          <td title=\"${{escapeText(metricTitle)}}\">${{escapeText(metricText)}}</td>
          <td class=\"num\">${{fmt(item.heat)}}</td>
          <td class=\"num\">${{fmt(item.velocity)}}</td>
          <td class=\"num\">${{fmt(item.acceleration)}}</td>
          <td>${{detailHtml(item)}}</td>
        `;
        tbody.appendChild(tr);
      }});
    }}
    function render() {{ renderTabs(); renderCards(); renderControls(); renderStatus(); renderRows(); }}
    render();
  </script>
</body>
</html>
"""
    (EXPORT_DIR / "dashboard.html").write_text(html_text)


def export_dashboard_v3(scored: list[dict[str, Any]], run_id: str, fetched_at: str, source_errors: dict[str, str | None]) -> None:
    config = read_config()
    dashboard_scored = [
        row
        for row in scored
        if row["channel"] in SETTINGS_CHANNELS or row["channel"] not in SOURCE_DASHBOARD_HIDDEN_CHANNELS
    ]
    display_rows = dashboard_scored + settings_rows_from_config(config, source_errors, fetched_at)
    channel_counts: dict[str, int] = {}
    window_counts: dict[str, int] = {}
    for row in display_rows:
        channel_counts[row["channel"]] = channel_counts.get(row["channel"], 0) + 1
        window = row.get("window") or "current"
        window_counts[window] = window_counts.get(window, 0) + 1

    channels = [
        {"id": channel, "label": channel_label(channel), "count": channel_counts[channel], "description": channel_description(channel)}
        for channel in CHANNEL_ORDER
        if channel in channel_counts and channel not in SOURCE_DASHBOARD_HIDDEN_CHANNELS
    ]
    channels.extend(
        {"id": channel, "label": channel_label(channel), "count": count, "description": channel_description(channel)}
        for channel, count in sorted(channel_counts.items())
        if channel not in CHANNEL_ORDER and channel not in SOURCE_DASHBOARD_HIDDEN_CHANNELS and channel not in SETTINGS_CHANNELS
    )
    settings_channels = [
        {"id": channel, "label": channel_label(channel), "count": channel_counts[channel], "description": channel_description(channel)}
        for channel in SETTINGS_CHANNEL_ORDER
        if channel in channel_counts and channel in SETTINGS_CHANNELS
    ]
    settings_channels.extend(
        {"id": channel, "label": channel_label(channel), "count": count, "description": channel_description(channel)}
        for channel, count in sorted(channel_counts.items())
        if channel not in SETTINGS_CHANNEL_ORDER and channel in SETTINGS_CHANNELS
    )

    payload = {
        "run_id": run_id,
        "fetched_at": fetched_at,
        "source_errors": source_errors,
        "channel_counts": channel_counts,
        "channels": channels,
        "settings_channels": settings_channels,
        "window_counts": window_counts,
        "config": config,
        "config_meta": {
            "default_schedule": "24h",
            "cron_enabled": False,
            "takes_effect": "next pipeline run",
            "api_status": api_status_payload(),
        },
        "items": display_rows,
    }
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html_template = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Hero Radar 数据面板</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #fbfbfa;
      --header: rgba(251,251,250,.94);
      --rail: #f7f7f5;
      --panel: #fbfbfa;
      --panel2: #f1f1ef;
      --head: #f7f7f5;
      --text: #37352f;
      --muted: #787774;
      --faint: #9b9a97;
      --line: #e6e4df;
      --line-strong: #d8d5cf;
      --good: #448361;
      --warn: #9f6b20;
      --accent: #337ea9;
      --accent-soft: #e8f2f8;
      --warn-soft: #fbf3db;
      --good-soft: #edf3ec;
      --shadow: none;
      --tip-bg: #2f3437;
      --tip-text: #ffffff;
    }
    body[data-theme="dark"] {
      color-scheme: dark;
      --bg: #191919;
      --header: rgba(25,25,25,.94);
      --rail: #202020;
      --panel: #191919;
      --panel2: #252525;
      --head: #202020;
      --text: #e6e6e6;
      --muted: #a1a1a1;
      --faint: #777777;
      --line: #303030;
      --line-strong: #424242;
      --good: #7fb996;
      --warn: #d5ad63;
      --accent: #529cca;
      --accent-soft: rgba(82,156,202,.16);
      --warn-soft: rgba(213,173,99,.14);
      --good-soft: rgba(127,185,150,.12);
      --tip-bg: #f7f7f5;
      --tip-text: #202020;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "Helvetica Neue", sans-serif;
      -webkit-font-smoothing: antialiased;
      text-rendering: optimizeLegibility;
    }
    .app-shell {
      display: grid;
      grid-template-columns: 252px minmax(0, 1fr);
      min-height: 100vh;
    }
    body.rail-collapsed .app-shell { grid-template-columns: 50px minmax(0, 1fr); }
    body.settings-mode .app-shell { grid-template-columns: 252px 224px minmax(0, 1fr); }
    body.settings-mode.rail-collapsed .app-shell { grid-template-columns: 50px 224px minmax(0, 1fr); }
    .rail {
      position: sticky;
      top: 0;
      height: 100vh;
      overflow: auto;
      padding: 14px 10px;
      border-right: 1px solid var(--line);
      background: var(--rail);
    }
    .rail-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 14px;
      padding: 2px 2px 10px;
      border-bottom: 1px solid var(--line);
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 9px;
      min-width: 0;
    }
    .brand-mark {
      width: 24px;
      height: 24px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex: 0 0 auto;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
      color: var(--text);
      font-size: 10px;
      font-weight: 650;
      letter-spacing: -.02em;
      font-family: "SF Mono", ui-monospace, Menlo, Consolas, monospace;
    }
    .rail-title { font-weight: 650; letter-spacing: -0.01em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .rail-toggle {
      width: 26px;
      height: 26px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex: 0 0 auto;
      padding: 0;
      border-radius: 6px;
    }
    .rail-toggle::before {
      content: "";
      width: 7px;
      height: 7px;
      border-left: 1.6px solid var(--muted);
      border-bottom: 1.6px solid var(--muted);
      transform: rotate(45deg) translate(1px, -1px);
      transition: transform 160ms ease, border-color 160ms ease;
    }
    .rail-toggle:hover::before { border-color: var(--text); }
    .workspace { min-width: 0; }
    .meta, .muted { color: var(--muted); }
    code { font-family: "SF Mono", ui-monospace, Menlo, Consolas, monospace; font-size: .92em; color: var(--muted); }
    main { padding: 18px 24px 40px; }
    .status-list, .controls, .pager-actions, .channel-tabs { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    .workspace-tabs { display: flex; flex-direction: column; gap: 6px; margin: 0; }
    .workspace-tabs button {
      width: 100%;
      display: flex;
      align-items: center;
      justify-content: flex-start;
      gap: 8px;
      text-align: left;
      padding: 8px 9px;
      overflow: hidden;
      white-space: nowrap;
    }
    .workspace-tabs .nav-icon {
      width: 18px;
      height: 18px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex: 0 0 auto;
      color: var(--muted);
    }
    .workspace-tabs .nav-icon svg {
      width: 16px;
      height: 16px;
      stroke: currentColor;
      stroke-width: 1.7;
      stroke-linecap: round;
      stroke-linejoin: round;
      fill: none;
    }
    .workspace-tabs button.active .nav-icon,
    .workspace-tabs button:hover .nav-icon { color: var(--text); }
    .workspace-tabs .full { overflow: hidden; text-overflow: ellipsis; }
    .nav-label {
      margin: 12px 4px 6px;
      color: var(--muted);
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: .08em;
    }
    .channel-tabs {
      margin: 0 0 10px;
      padding: 0 0 10px;
      background: transparent;
      border-bottom: 1px solid var(--line);
      border-radius: 0;
      box-shadow: var(--shadow);
    }
    .channel-tabs button { padding: 5px 8px; font-size: 13px; position: relative; }
    .channel-tabs button[data-tip]::after {
      content: attr(data-tip);
      visibility: hidden;
      opacity: 0;
      position: absolute;
      left: 0;
      top: calc(100% + 8px);
      width: min(420px, calc(100vw - 48px));
      padding: 9px 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--tip-bg);
      color: var(--tip-text);
      box-shadow: var(--shadow);
      white-space: normal;
      text-align: left;
      line-height: 1.45;
      font-size: 12px;
      font-weight: 400;
      z-index: 50;
      pointer-events: none;
      transform: translateY(-2px);
      transition: opacity .08s ease, transform .08s ease, visibility 0s linear .08s;
    }
    .channel-tabs button[data-tip]::before {
      content: "";
      visibility: hidden;
      opacity: 0;
      position: absolute;
      left: 14px;
      top: calc(100% + 3px);
      width: 9px;
      height: 9px;
      background: var(--tip-bg);
      border-left: 1px solid var(--line);
      border-top: 1px solid var(--line);
      transform: rotate(45deg);
      z-index: 51;
      pointer-events: none;
      transition: opacity .08s ease, visibility 0s linear .08s;
    }
    .channel-tabs button[data-tip]:hover::after,
    .channel-tabs button[data-tip]:focus-visible::after,
    .channel-tabs button[data-tip]:hover::before,
    .channel-tabs button[data-tip]:focus-visible::before {
      visibility: visible;
      opacity: 1;
      transition-delay: .45s, .45s, 0s;
    }
    .channel-tabs button[data-tip]:hover::after,
    .channel-tabs button[data-tip]:focus-visible::after { transform: translateY(0); }
    body.rail-collapsed .rail { padding: 12px 6px; overflow-x: hidden; }
    body.rail-collapsed .rail-head {
      flex-direction: column;
      justify-content: flex-start;
      gap: 8px;
      margin-bottom: 14px;
      padding: 0 0 12px;
    }
    body.rail-collapsed .rail-title,
    body.rail-collapsed .nav-label { display: none; }
    body.rail-collapsed .brand { justify-content: center; }
    body.rail-collapsed .brand-mark { width: 30px; height: 30px; border-radius: 8px; }
    body.rail-collapsed .rail-toggle::before { transform: rotate(225deg) translate(1px, -1px); }
    body.rail-collapsed .workspace-tabs { align-items: center; gap: 7px; }
    body.rail-collapsed .workspace-tabs button {
      width: 34px;
      height: 34px;
      justify-content: center;
      padding: 0;
      border-radius: 8px;
    }
    body.rail-collapsed .workspace-tabs .full { display: none; }
    body.rail-collapsed .workspace-tabs .nav-icon { width: 18px; height: 18px; }
    .settings-subrail {
      position: sticky;
      top: 0;
      height: 100vh;
      overflow: auto;
      padding: 18px 10px;
      border-right: 1px solid var(--line);
      background: var(--panel);
    }
    .settings-subrail[hidden] { display: none; }
    .subrail-eyebrow {
      margin: 0 4px 10px;
      color: var(--muted);
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: .08em;
    }
    .subrail-title {
      margin: 0 4px 14px;
      font-size: 15px;
      line-height: 1.2;
      font-weight: 650;
      letter-spacing: -0.015em;
    }
    .settings-subnav {
      display: grid;
      gap: 2px;
    }
    .settings-subnav button {
      width: 100%;
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 8px;
      align-items: center;
      text-align: left;
      padding: 7px 8px;
      border-radius: 6px;
      color: var(--muted);
    }
    .settings-subnav button:hover {
      color: var(--text);
      background: var(--panel2);
    }
    .settings-subnav button.active {
      color: var(--text);
      background: var(--panel2);
      border-color: transparent;
    }
    .settings-subnav .subnav-label {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    button {
      border: 1px solid transparent;
      color: var(--text);
      background: transparent;
      border-radius: 6px;
      padding: 7px 10px;
      cursor: pointer;
      font: inherit;
      transition: background-color 160ms ease, border-color 160ms ease, color 160ms ease, transform 120ms ease;
    }
    button:hover { background: var(--panel2); }
    button:active { transform: translateY(1px); }
    button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
    button.active { background: var(--panel2); border-color: var(--line-strong); }
    button:disabled { cursor: not-allowed; opacity: .45; border-color: var(--line); }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(176px, 1fr)); gap: 0; margin: 0 0 10px; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; background: var(--panel); }
    .card {
      min-height: 70px;
      background: var(--panel);
      border: 0;
      border-right: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
      border-radius: 0;
      padding: 10px 12px;
      box-shadow: var(--shadow);
    }
    .card .label { color: var(--muted); font-size: 11px; display: flex; gap: 5px; align-items: center; }
    .card .value { font-size: 20px; line-height: 1.15; margin-top: 5px; font-weight: 520; letter-spacing: -0.015em; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
    .card .sub { color: var(--faint); font-size: 11px; margin-top: 4px; }
    .controls {
      margin: 0 0 10px;
      padding: 7px 8px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 6px;
      box-shadow: var(--shadow);
    }
    .control-group { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
    .control-label { color: var(--muted); font-size: 12px; margin-right: 2px; }
    .control-button { padding: 4px 7px; font-size: 12px; }
    .status-list { margin: 2px 0 12px; }
    .settings-note {
      width: 100%;
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      padding: 10px 12px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .settings-note strong { color: var(--text); font-weight: 560; }
    .settings-panel {
      display: grid;
      gap: 16px;
      margin-top: 0;
      max-width: 1180px;
    }
    .settings-toolbar,
    .settings-section {
      box-shadow: var(--shadow);
    }
    .settings-toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 0 0 14px;
      border: 0;
      border-bottom: 1px solid var(--line);
      background: transparent;
      border-radius: 0;
      flex-wrap: wrap;
    }
    .settings-toolbar .title { font-weight: 600; letter-spacing: -0.01em; }
    .settings-toolbar .copy { color: var(--muted); font-size: 12px; margin-top: 2px; }
    .settings-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    .primary-button {
      background: var(--text);
      color: var(--bg);
      border-color: var(--text);
    }
    .primary-button:hover { background: color-mix(in srgb, var(--text) 88%, var(--panel)); color: var(--bg); }
    .settings-section {
      padding: 0 0 18px;
      border: 0;
      border-bottom: 1px solid var(--line);
      background: transparent;
      border-radius: 0;
    }
    .settings-section:last-child { border-bottom: 0; }
    .settings-section + .settings-section { margin-top: 0; }
    .settings-section h2 {
      margin: 0 0 4px;
      font-size: 16px;
      line-height: 1.25;
      font-weight: 620;
      letter-spacing: -0.015em;
    }
    .settings-section .section-copy {
      margin: 0 0 12px;
      color: var(--muted);
      font-size: 12px;
      max-width: 760px;
    }
    .settings-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 10px;
    }
    .settings-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 12px;
      min-width: 0;
    }
    .settings-card.compact { padding: 9px 10px; }
    .settings-card-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 8px;
    }
    .settings-card-title { font-weight: 590; letter-spacing: -0.01em; overflow-wrap: anywhere; }
    .settings-card-note { color: var(--muted); font-size: 12px; margin-top: 2px; overflow-wrap: anywhere; }
    .setting-list { display: grid; gap: 8px; }
    .setting-row {
      display: grid;
      grid-template-columns: minmax(120px, 180px) minmax(0, 1fr) auto;
      gap: 8px;
      align-items: center;
      border-top: 1px solid var(--line);
      padding-top: 8px;
    }
    .setting-row:first-child { border-top: 0; padding-top: 0; }
    .setting-row.two { grid-template-columns: minmax(120px, 180px) minmax(0, 1fr); }
    .setting-row.stack { grid-template-columns: 1fr; }
    .setting-label { color: var(--muted); font-size: 12px; }
    .setting-help { color: var(--faint); font-size: 11px; margin-top: 2px; }
    .field,
    .textarea,
    .select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
      color: var(--text);
      padding: 6px 7px;
      font: inherit;
      font-size: 13px;
    }
    .field:focus,
    .textarea:focus,
    .select:focus {
      outline: 2px solid color-mix(in srgb, var(--accent) 45%, transparent);
      border-color: var(--accent);
    }
    .textarea {
      min-height: 54px;
      resize: vertical;
      line-height: 1.35;
    }
    .toggle-row {
      display: flex;
      gap: 8px;
      align-items: center;
      color: var(--text);
      font-size: 13px;
    }
    .toggle-row input { margin: 0; }
    .settings-table {
      display: grid;
      gap: 7px;
    }
    .query-row,
    .account-row,
    .status-row {
      display: grid;
      grid-template-columns: minmax(120px, 190px) minmax(0, 1fr) auto;
      gap: 8px;
      align-items: start;
      padding: 8px;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: var(--panel);
    }
    .account-row { grid-template-columns: minmax(0, 1fr) auto; align-items: center; }
    .status-row { grid-template-columns: minmax(160px, 220px) minmax(80px, 100px) minmax(0, 1fr); }
    .x-person {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      min-width: 0;
      max-width: 100%;
    }
    .x-avatar {
      width: 22px;
      height: 22px;
      border-radius: 999px;
      object-fit: cover;
      flex: 0 0 auto;
      background: var(--panel2);
      border: 1px solid var(--line);
    }
    .x-avatar-fallback {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      color: var(--muted);
      font-size: 10px;
      font-weight: 600;
      line-height: 1;
    }
    .x-person-name {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .query-row .field { margin-bottom: 6px; }
    .small-button { padding: 5px 7px; font-size: 12px; }
    .danger-button { color: var(--warn); }
    .status-dot {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 58px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 7px;
      font-size: 12px;
      color: var(--muted);
      background: var(--panel2);
    }
    .status-dot.ok { color: var(--good); background: var(--good-soft); }
    .status-dot.warn { color: var(--warn); background: var(--warn-soft); }
    .message-line {
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .message-line.good { color: var(--good); }
    .message-line.warn { color: var(--warn); }
    .settings-panel[hidden],
    .table-wrap[hidden],
    .pager[hidden],
    .controls[hidden],
    .grid[hidden] { display: none; }
    .status-pill {
      border: 1px solid var(--line);
      background: var(--panel2);
      border-radius: 999px;
      padding: 3px 7px;
      color: var(--muted);
      font-size: 11px;
      line-height: 1.2;
      max-width: 240px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .status-pill strong { color: var(--text); }
    .status-pill.problem { background: var(--warn-soft); border-color: color-mix(in srgb, var(--warn) 30%, var(--line)); }
    .status-details { flex-basis: 100%; color: var(--muted); font-size: 12px; margin-top: 2px; }
    .status-details summary { color: var(--accent); cursor: pointer; width: fit-content; }
    .status-detail-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 6px; margin-top: 8px; }
    .status-error-line { border: 1px solid var(--line); background: var(--panel); border-radius: 6px; padding: 8px 10px; overflow-wrap: anywhere; }
    .table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; box-shadow: var(--shadow); background: var(--panel); }
    table { width: 100%; min-width: 1120px; border-collapse: collapse; background: var(--panel); border: 0; }
    th, td { border-bottom: 1px solid var(--line); padding: 7px 9px; vertical-align: top; }
    th { color: var(--muted); text-align: left; font-size: 12px; font-weight: 560; background: var(--head); white-space: nowrap; position: relative; }
    .th-inner { display: inline-flex; align-items: center; gap: 4px; min-width: 0; max-width: 100%; }
    th.sortable .th-inner { cursor: pointer; }
    th.sortable:hover .th-label { color: var(--text); }
    .sort-indicator {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 12px;
      color: var(--accent);
      font-size: 11px;
      line-height: 1;
      opacity: 0;
    }
    th.sort-active .sort-indicator,
    th.sortable:hover .sort-indicator { opacity: 1; }
    .col-resizer {
      position: absolute;
      right: -3px;
      top: 0;
      width: 7px;
      height: 100%;
      cursor: col-resize;
      user-select: none;
      touch-action: none;
      z-index: 2;
    }
    .col-resizer::after {
      content: "";
      position: absolute;
      top: 7px;
      bottom: 7px;
      left: 3px;
      width: 1px;
      background: transparent;
      transition: background-color 120ms ease;
    }
    .col-resizer:hover::after,
    th.is-resizing .col-resizer::after { background: var(--accent); }
    body.resizing-columns { cursor: col-resize; user-select: none; }
    td { max-width: 360px; }
    tr:hover td { background: color-mix(in srgb, var(--panel2) 55%, transparent); }
    td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
    td.tight { max-width: 140px; }
    td.wide { min-width: 220px; }
    td.desc { min-width: 280px; color: var(--muted); }
    td.spark, th.spark { min-width: 116px; max-width: 150px; }
    .sparkline {
      display: inline-grid;
      grid-template-columns: 92px auto;
      align-items: center;
      gap: 7px;
      color: var(--accent);
      font-variant-numeric: tabular-nums;
    }
    .sparkline svg {
      width: 92px;
      height: 28px;
      display: block;
      overflow: visible;
    }
    .sparkline .spark-axis { stroke: var(--line-strong); stroke-width: 1; }
    .sparkline .spark-area { fill: color-mix(in srgb, var(--accent) 12%, transparent); }
    .sparkline .spark-line { fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }
    .sparkline .spark-dot { fill: var(--panel); stroke: currentColor; stroke-width: 1.5; }
    .sparkline-last { color: var(--muted); font-size: 11px; min-width: 24px; text-align: right; }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; text-underline-offset: 2px; }
    .pill { display: inline-block; border: 1px solid var(--line); background: var(--panel2); border-radius: 999px; padding: 1px 6px; color: var(--muted); font-size: 12px; white-space: nowrap; }
    details { color: var(--muted); }
    summary { color: var(--accent); cursor: pointer; white-space: nowrap; }
    .detail-block { margin-top: 8px; max-width: 760px; }
    .detail-block p { margin: 0 0 8px; }
    .why { color: var(--muted); font-size: 12px; }
    .sample { margin: 6px 0 0; padding: 7px 8px; border-left: 2px solid var(--line-strong); background: var(--panel2); border-radius: 4px; }
    .raw-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 5px 10px; margin-top: 8px; }
    .raw-cell { color: var(--muted); overflow-wrap: anywhere; }
    .error { display: none; margin: 12px 0; padding: 12px; border: 1px solid var(--warn); background: var(--panel); color: var(--warn); border-radius: 8px; }
    .status-ok { color: var(--good); }
    .status-bad { color: var(--warn); }
    .pager { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-top: 10px; color: var(--muted); flex-wrap: wrap; }
    .pager-left,
    .pager-size { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
    .empty { padding: 28px; color: var(--muted); text-align: center; border: 1px solid var(--line); border-radius: 6px; background: var(--panel); }
    .hint {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 14px;
      height: 14px;
      border: 1px solid transparent;
      border-radius: 50%;
      color: var(--muted);
      font-size: 10px;
      line-height: 1;
      position: relative;
      cursor: help;
      flex: 0 0 auto;
    }
    .hint::after {
      content: attr(data-tip);
      display: none;
      position: absolute;
      left: 0;
      top: 22px;
      transform: none;
      width: min(430px, 76vw);
      padding: 8px 9px;
      border-radius: 6px;
      background: var(--tip-bg);
      color: var(--tip-text);
      box-shadow: var(--shadow);
      white-space: normal;
      z-index: 30;
      text-align: left;
      font-weight: 400;
      line-height: 1.35;
    }
    th:nth-last-child(-n+2) .hint::after { left: auto; right: 0; }
    .hint:hover::after { display: block; }
    @media (max-width: 760px) {
      .app-shell { grid-template-columns: 1fr; }
      body.settings-mode .app-shell,
      body.settings-mode.rail-collapsed .app-shell { grid-template-columns: 1fr; }
      body.rail-collapsed .app-shell { grid-template-columns: 1fr; }
      .rail { position: static; height: auto; border-right: 0; border-bottom: 1px solid var(--line); }
      body.rail-collapsed .rail { display: none; }
      .settings-subrail { position: static; height: auto; border-right: 0; border-bottom: 1px solid var(--line); }
      .settings-subnav { grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
      .workspace-tabs { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
      main { padding: 12px 12px 32px; }
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
  </style>
</head>
<body>
  <div class="app-shell">
    <aside class="rail" id="rail">
      <div class="rail-head">
        <div class="brand">
          <div class="brand-mark" aria-hidden="true">HR</div>
          <div class="rail-title">Hero Radar</div>
        </div>
        <button class="rail-toggle" id="railToggle" type="button" aria-label="收起侧边栏" title="收起侧边栏"></button>
      </div>
      <div class="nav-label">Workspace</div>
      <nav class="workspace-tabs" id="workspaceTabs" aria-label="Workspace"></nav>
    </aside>
    <aside class="settings-subrail" id="settingsSubrail" hidden>
      <div class="subrail-eyebrow">Settings</div>
      <div class="subrail-title">Controls</div>
      <nav class="settings-subnav" id="settingsSubnav" aria-label="Settings"></nav>
    </aside>
    <div class="workspace">
      <main>
        <div class="error" id="error"></div>
        <section class="channel-tabs" id="channelTabs" aria-label="Channels"></section>
        <section class="grid" id="cards"></section>
        <section class="controls" id="controls"></section>
        <section class="status-list" id="statusList"></section>
        <section class="settings-panel" id="settingsPanel" hidden></section>
        <div class="table-wrap" id="tableWrap">
          <table>
            <thead id="tableHead"></thead>
            <tbody id="rows"></tbody>
          </table>
        </div>
        <div class="pager" id="pager"></div>
      </main>
    </div>
  </div>
  <script id="data" type="application/json">__DATA_JSON__</script>
  <script>
    const errorBox = document.getElementById('error');
    function showError(message) {
      errorBox.style.display = 'block';
      errorBox.textContent = message;
    }
    let data;
    try {
      data = JSON.parse(document.getElementById('data').textContent);
    } catch (error) {
      showError(`Dashboard data failed to parse: ${error.message}`);
      throw error;
    }

    const channels = data.channels || [];
    const settingsChannels = data.settings_channels || [];
    const sourceChannelIds = new Set(channels.map(channel => channel.id));
    let runtimeConfig = cloneJson(data.config || {});
    let savedConfigText = JSON.stringify(runtimeConfig);
    let configMessage = '';
    let configMessageKind = '';
    let configBusy = false;
    const apiWritable = location.protocol === 'http:' || location.protocol === 'https:';
    let activeSection = localStorage.getItem('heroRadarSection') === 'settings' ? 'settings' : 'source';
    let activeSource = localStorage.getItem('heroRadarSourceTab') || visibleSourceChannels()[0]?.id || '';
    let activeSettings = localStorage.getItem('heroRadarSettingsTab') || 'settings_run_sources';
    let active = activeSection === 'settings' ? activeSettings : activeSource;
    const channelState = {};
    const rangeRankCache = {};
    const pageSizes = [50, 100, 200, 500];
    document.body.dataset.theme = localStorage.getItem('heroRadarTheme') || 'light';
    if (localStorage.getItem('heroRadarRail') === 'collapsed') document.body.classList.add('rail-collapsed');
    updateRailButton();
    railToggle.onclick = () => {
      document.body.classList.toggle('rail-collapsed');
      localStorage.setItem('heroRadarRail', document.body.classList.contains('rail-collapsed') ? 'collapsed' : 'expanded');
      updateRailButton();
    };

    function setTheme(theme) {
      const next = theme === 'dark' ? 'dark' : 'light';
      document.body.dataset.theme = next;
      localStorage.setItem('heroRadarTheme', next);
    }
    function updateRailButton() {
      const collapsed = document.body.classList.contains('rail-collapsed');
      const label = collapsed ? '展开侧边栏' : '收起侧边栏';
      railToggle.setAttribute('aria-label', label);
      railToggle.setAttribute('title', label);
    }
    function fmt(v) {
      if (v === null || v === undefined || Number.isNaN(Number(v))) return '';
      const n = Number(v);
      if (Math.abs(n) >= 1000) return Math.round(n).toLocaleString();
      if (Number.isInteger(n)) return String(n);
      return n.toFixed(2);
    }
    function fmtDate(value) {
      if (!value) return '';
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return String(value);
      return date.toISOString().replace('T', ' ').replace(/\\.\\d{3}Z$/, 'Z');
    }
    function escapeText(value) {
      return String(value ?? '').replace(/[&<>"']/g, ch => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
      }[ch]));
    }
    function escapeUrl(value) {
      const text = String(value || '#');
      return text.startsWith('http://') || text.startsWith('https://') ? text.replace(/"/g, '%22') : '#';
    }
    let xAvatarCache = null;
    function xAvatarMap() {
      if (xAvatarCache) return xAvatarCache;
      const map = {};
      for (const item of data.items || []) {
        const metaObj = item.metadata || {};
        const author = String(metaObj.author || metaObj.username || '').replace(/^@/, '').toLowerCase();
        if (author && metaObj.author_avatar && !map[author]) map[author] = metaObj.author_avatar;
        const rawAuthor = item.raw?.author || {};
        const rawHandle = String(rawAuthor.userName || rawAuthor.username || '').replace(/^@/, '').toLowerCase();
        if (rawHandle && rawAuthor.profilePicture && !map[rawHandle]) map[rawHandle] = rawAuthor.profilePicture;
      }
      xAvatarCache = map;
      return xAvatarCache;
    }
    function xAvatarForHandle(handle) {
      const wanted = String(handle || '').replace(/^@/, '').toLowerCase();
      if (!wanted) return '';
      return xAvatarMap()[wanted] || '';
    }
    function avatarHtml(handle, url = '') {
      const clean = String(handle || '').replace(/^@/, '');
      const label = clean ? `@${clean}` : '';
      const safeUrl = escapeUrl(url || xAvatarForHandle(clean));
      const initials = escapeText((clean.slice(0, 2) || '?').toUpperCase());
      if (safeUrl !== '#') return `<img class="x-avatar" src="${safeUrl}" alt="${escapeText(label)}" loading="lazy" referrerpolicy="no-referrer">`;
      return `<span class="x-avatar x-avatar-fallback" aria-hidden="true">${initials}</span>`;
    }
    function xPersonHtml(handle, avatarUrl = '') {
      const clean = String(handle || '').replace(/^@/, '');
      if (!clean) return '';
      return `<span class="x-person">${avatarHtml(clean, avatarUrl)}<span class="x-person-name">@${escapeText(clean)}</span></span>`;
    }
    function cloneJson(value) {
      return JSON.parse(JSON.stringify(value ?? {}));
    }
    function getConfig(path, fallback = undefined) {
      const parts = String(path).split('.').filter(Boolean);
      let cur = runtimeConfig;
      for (const part of parts) {
        if (cur === null || cur === undefined || typeof cur !== 'object') return fallback;
        cur = cur[part];
      }
      return cur === undefined ? fallback : cur;
    }
    function setConfig(path, value) {
      const parts = String(path).split('.').filter(Boolean);
      if (!parts.length) return;
      let cur = runtimeConfig;
      parts.slice(0, -1).forEach(part => {
        if (!cur[part] || typeof cur[part] !== 'object') cur[part] = {};
        cur = cur[part];
      });
      cur[parts[parts.length - 1]] = value;
      markConfigDirty();
    }
    function markConfigDirty(message = '') {
      if (message) {
        configMessage = message;
        configMessageKind = 'warn';
      }
    }
    function configDirty() {
      return JSON.stringify(runtimeConfig) !== savedConfigText;
    }
    function localJson(key, fallback) {
      try {
        const parsed = JSON.parse(localStorage.getItem(key) || 'null');
        return parsed ?? fallback;
      } catch (_) {
        return fallback;
      }
    }
    function setLocalJson(key, value) {
      localStorage.setItem(key, JSON.stringify(value));
    }
    function hiddenSourceSet() {
      return new Set(localJson('heroRadarHiddenSources', []));
    }
    function visibleSourceChannels() {
      const hidden = hiddenSourceSet();
      return channels.filter(channel => !hidden.has(channel.id));
    }
    function settingsPanelDefs() {
      const searchCount =
        (getConfig('github_search.queries', []) || []).length +
        (getConfig('hn.algolia_queries', []) || []).length +
        (getConfig('npm.queries', []) || []).length +
        (getConfig('apify.x_keyword_queries', []) || []).length;
      const xAccountCount = (getConfig('apify.x_seed_accounts', []) || []).length;
      const sourceCount = Object.keys(data.source_errors || {}).length;
      const apiCount = Object.values(data.config_meta?.api_status || {}).length;
      return [
        {id: 'settings_run_sources', label: 'Run & Sources', count: sourceCount},
        {id: 'settings_search_terms', label: 'Search Terms', count: searchCount},
        {id: 'settings_x_monitoring', label: 'X Monitoring', count: xAccountCount},
        {id: 'settings_display', label: 'Display', count: visibleSourceChannels().length},
        {id: 'settings_api_status', label: 'API Status', count: apiCount},
      ];
    }
    function channelLabel(id) {
      const found = [...channels, ...settingsPanelDefs(), ...settingsChannels].find(channel => channel.id === id);
      return found?.label || id;
    }
    function meta(item, key) { return (item.metadata || {})[key]; }
    function raw(item, key) { return (item.raw || {})[key]; }
    function val(item, path) {
      if (path === '$rank') return rangeRankValue(item);
      if (path === '$window') return item.window || 'current';
      if (path === '$source') return item.source;
      if (path === '$name') return item.name;
      if (path === '$description') return item.description;
      if (path === '$url') return item.url;
      if (path.startsWith('m.')) return meta(item, path.slice(2));
      if (path.startsWith('r.')) return raw(item, path.slice(2));
      return item[path];
    }
    function arr(value, limit = 6) {
      if (!Array.isArray(value)) return '';
      return value.filter(Boolean).slice(0, limit).join('，');
    }
    function objPairs(value, limit = 6) {
      if (!value || typeof value !== 'object' || Array.isArray(value)) return '';
      return Object.entries(value).slice(0, limit).map(([k, v]) => `${k}: ${v}`).join('；');
    }
    function pill(value) { return `<span class="pill">${escapeText(value)}</span>`; }
    function link(url, label) { return `<a href="${escapeUrl(url)}" target="_blank" rel="noreferrer">${escapeText(label)}</a>`; }
    function tip(text) { return `<span class="hint" data-tip="${escapeText(text)}">?</span>`; }
    function c(label, help, path, cls = '', kind = 'text') { return {label, help, path, cls, kind}; }
    function rank(label = '排名', help = '这个数字是当前 source + 当前时间范围内的原生顺序。切换 24h / 7d / 30d 后会重新从 1 开始；它不是跨 source 总分，也不是我们额外加权。点其他列排序后，这个数字仍保留 source 原本的窗口内顺序。') { return c(label, help, '$rank', 'num tight', 'num'); }
    function win() { return c('时间窗', '这行数据对应的取数窗口。24h / 7d / 30d 表示对应时间范围；30d+ 表示超过 30 天的历史 X tweet；current 表示只有当前快照。看新增、速度、榜单时必须先看窗口，否则数值不可比。', '$window', 'tight', 'pill'); }
    function src() { return c('来源', '底层 adapter/source 名称，例如 github_search 或 hn_algolia。主 tab 已经按 source 分组，所以这个字段通常只在详情或 fallback 表里出现。', '$source', 'tight', 'pill'); }
    function name(label = '条目') { return c(label, '条目的原始名称和链接。点击会打开 source 给出的原始 URL；这里不做跨平台实体合并，所以同一个项目在不同 source 可能出现多次。', '$name', 'wide', 'link'); }
    function desc() { return c('描述', 'source 返回的原文描述字段：GitHub description、Product Hunt tagline、HN story_text 摘要、tweet text 等。它是阅读线索，不是模型生成摘要。', '$description', 'desc'); }
    function detail() { return c('详情', '展开后看该 source 的保留字段、样例 tweet、原始 facts 和补充链接。主表只放最常用字段，长文本和调试字段都放这里。', '$detail', 'wide', 'detail'); }

    const columnsByChannel = {
      github_trending: [rank('GitHub 排名', 'GitHub Trending 页面在当前时间范围里的原始顺序。切换 24h / 7d / 30d 会进入不同榜单，并从 1 重新开始；它不是我们打分，也不是全 GitHub 项目的全量排名。'), name('Repo'), desc(), c('GitHub 原文窗口', 'GitHub Trending 的 since 参数：daily 显示 stars today，weekly 显示 stars this week，monthly 显示 stars this month。它说明下一列新增 star 对应哪个窗口。', 'm.period', '', 'githubPeriod'), c('原生新增 star', '从 GitHub Trending 页面解析出的窗口新增 star，例如 X stars today / this week / this month。这是 GitHub 页面给出的原生数，不是我们用历史快照算的 velocity；越高表示该窗口内新增关注越多。', 'm.period_stars', 'num', 'num'), c('总 star', 'GitHub Trending 页面显示的当前仓库总 stargazer 数。它是绝对体量，不代表最近增长；老项目通常会更高。', 'm.stars_total', 'num', 'num'), detail()],
      github_movers_trending_repos: [rank('TR 排名', 'Trending Repos 自己的当前时间范围 momentum 榜原始顺序。切换 24h / 7d / 30d 会进入不同榜单，并从 1 重新开始；它不是我们二次排序的结果。'), name('Repo'), desc(), c('增量曲线', 'Trending Repos 原生 sparkline 数组，页面用它画近期新增 star 曲线。我们按当前筛选行里的最大增量统一缩放，右侧数字是最后一个点；看末段抬升、连续上升、单日尖峰或回落。', 'm.sparkline', 'spark', 'sparkline'), c('总 star', 'Trending Repos 原生 starsCount，表示当前仓库总 star。它说明体量，不等于增长速度。', 'm.stars_count', 'num', 'num'), c('总 fork', 'Trending Repos 原生 forksCount，表示当前仓库总 fork。fork 往往比 star 更偏开发者实际尝试，但也可能来自模板或镜像。', 'm.forks_count', 'num', 'num'), c('TR score', 'Trending Repos 原生 score。官方说明大致由 star delta 的 EMA、fork delta 的 EMA、freshness_bonus 组合而成，用来排它自己的 momentum 榜；越高表示它认为近期动量越强，不是我们的最终评分。', 'm.source_score', 'num', 'num'), c('star velocity', 'Trending Repos 原生 scoreComponents.starsVelocity，表示近期 star 增量的平滑动量分量。它不是简单 stars/hour；越高表示近期 star 增长越快。', 'm.stars_velocity', 'num', 'num'), c('fork velocity', 'Trending Repos 原生 scoreComponents.forksVelocity，表示近期 fork 增量的平滑动量分量。fork 增长更像开发者试用信号，越高越值得看。', 'm.forks_velocity', 'num', 'num'), c('freshness_bonus', 'Trending Repos 原生 scoreComponents.freshnessBonus，新 repo 的 recency 加成，会随 repo age 衰减。高值说明项目较新或被 source 判定更 fresh；它不是质量分，只是新鲜度修正。', 'm.freshness_bonus', 'num', 'num'), c('topics', 'Trending Repos 返回的 GitHub topics。用于快速判断领域；topic 是仓库维护者/平台标签，不是我们分类。', 'm.topics', '', 'list'), detail()],
      github_movers_repofomo: [rank('范围排名', 'RepoFOMO 当前原生范围内的排名。切换 7d / 30d / 60d 后，分别按 7d_new / 30d_new / 60d_new 从高到低重新编号；这不是我们加权，只是把 RepoFOMO 同一张表里的原生窗口字段作为 lens。'), name('Repo'), desc(), c('Info', 'RepoFOMO 原生 info/pitch 文案，通常是对仓库用途的短描述。用于快速理解项目，不参与本地计算。', 'm.info', 'desc'), c('总 star', 'RepoFOMO 原生 tot_stars，表示当前总 star。体量越大说明已有关注越多，但不代表它现在仍在加速。', 'm.stars_total', 'num', 'num'), c('7d 新增', 'RepoFOMO 原生 7d_new，表示过去 7 天新增 star 数。它是周级 mover 信号；越高说明最近一周增长越强。', 'm.stars_7d', 'num', 'num'), c('30d 新增', 'RepoFOMO 原生 30d_new，表示过去 30 天新增 star 数。它比 7d 更平滑，适合看月级持续增长。', 'm.stars_30d', 'num', 'num'), c('60d 新增', 'RepoFOMO 原生 60d_new，表示过去 60 天新增 star 数。它更偏中期趋势，适合区分短期尖峰和持续升温。', 'm.stars_60d', 'num', 'num'), c('7d %', 'RepoFOMO 原生 7d% 增长率，表示过去 7 天 star 的相对增长。小 repo 容易很高，所以要和 7d 新增一起看。', 'm.growth_7d_percent', 'num', 'num'), c('30d %', 'RepoFOMO 原生 30d% 增长率，表示过去 30 天 star 的相对增长。用于看月级相对加速，小体量项目可能被放大。', 'm.growth_30d_percent', 'num', 'num'), c('forks', 'RepoFOMO 原生 forks，表示当前 fork 总数。它是开发者复制/尝试的粗信号，不等同于活跃用户。', 'm.forks', 'num', 'num'), c('new forks', 'RepoFOMO 原生 new_forks，表示近期新增 fork。和新增 star 一起看，可以判断是否只是围观，还是有人开始动手试。', 'm.new_forks', 'num', 'num'), c('star age', 'RepoFOMO 原生 star_age，单位天，用作新旧程度参考。数值越小越新；新项目增长快更值得关注，但也更容易是假尖峰。', 'm.star_age_days', 'num', 'num'), detail()],
      github_search: [rank('搜索排名', 'GitHub Search API 在当前 query 里的返回顺序。我们请求 sort=stars、order=desc，所以它基本按总 star 排；这不是 trending，也不是新增速度。'), name('Repo'), desc(), c('搜索词', '命中这行的 GitHub Search 配置 query，例如 agent stars:>20。这个字段说明它是被哪个主动搜索入口抓进来的；改 query 后下一次 run 生效。', 'm.query_label'), c('stars', 'GitHub REST API 的 stargazers_count，当前总 star 数。它是体量指标，不是最近新增。', 'm.stars', 'num', 'num'), c('forks', 'GitHub REST API 的 forks_count，当前总 fork 数。fork 更偏开发者尝试/复用信号，但不代表近期增速。', 'm.forks', 'num', 'num'), c('watchers', 'GitHub API 原生 watchers_count。GitHub 这个字段常和 star 口径接近，不建议当成独立趋势指标；主要用于完整保留 API 事实。', 'r.watchers_count', 'num', 'num'), c('issues', 'GitHub API open_issues_count，通常包含 open issues 和 pull requests。高值可能表示活跃，也可能表示维护压力，需要结合 repo 内容看。', 'r.open_issues_count', 'num', 'num'), c('license', 'GitHub API license 字段，通常是 SPDX id 或 license 名称。用于判断能否复用/研究，不是热度信号。', 'r.license'), c('created', 'GitHub created_at，仓库创建时间。新仓库高增长更接近早期机会；老仓库高 star 更多是存量体量。', 'm.created_at', '', 'date'), c('updated', 'GitHub updated_at，仓库元数据或内容最近更新时间。只能说明近期有变化，不一定是代码 push。', 'r.updated_at', '', 'date'), c('pushed', 'GitHub pushed_at，最近一次代码 push 时间。比 updated 更接近开发活跃度；很久没 push 的项目即使 star 高也要谨慎。', 'm.pushed_at', '', 'date'), c('topics', 'GitHub topics，仓库维护者/平台标签。用于快速判断领域和关键词，不是我们自动分类。', 'm.topics', '', 'list'), detail()],
      hn_search: [rank('搜索排名', 'HN Algolia search_by_date 在当前 query 和时间范围里的返回顺序。切换 24h / 7d / 30d 会进入不同搜索窗口，并从 1 重新开始；这个 endpoint 偏按时间返回搜索命中，不是按 points 排名。'), name('HN/URL'), desc(), c('搜索词', '命中这行的 HN Algolia query，例如 agent。它说明这个 HN story 是由哪个主动搜索词抓到的；改 query 后下一次 run 生效。', 'm.query_label'), c('points', 'HN Algolia 原生 points，也就是 HN story 当前得分。分数越高说明 HN 社区投票越强，但它会受时间、标题和社区偏好影响。', 'm.points', 'num', 'num'), c('comments', 'HN Algolia 原生 num_comments，表示评论数。评论多说明讨论强，但可能是争议、质疑或负面反馈；需要读评论。', 'm.comments', 'num', 'num'), c('作者', 'HN author，提交这个 story 的 HN 用户名。可用于判断是否是项目作者、熟悉账号或普通转发者。', 'm.author'), c('created', 'HN Algolia created_at，story 创建时间。结合 points/comments 和时间窗判断热度是否刚发生。', 'm.created_at', '', 'date'), c('HN 链接', 'Hacker News item 页面链接。打开后可以看完整评论区；后续 LLM 深挖也应从这里拉 comments。', 'm.hn_url', '', 'url'), detail()],
      hn_top: [rank('榜单排名', 'HN Firebase list 内的原始位置。topstories/newstories/beststories 各自都有自己的顺序；数值越小表示在该 list 里越靠前。'), name('HN/URL'), desc(), c('榜单', 'HN Firebase 原生列表来源：topstories、newstories 或 beststories。top 偏当前热度，new 偏最新，best 偏长期质量。', 'm.list'), c('score', 'HN Firebase item.score，HN story 当前分数。越高表示投票越强；要结合发布时间看，老帖天然更容易累积分。', 'm.score', 'num', 'num'), c('comments', 'HN Firebase item.descendants，表示该 story 的评论总数。它不是情绪分；高评论可能是兴奋，也可能是争议。', 'm.comments', 'num', 'num'), c('作者', 'HN Firebase item.by，提交这个 story 的 HN 用户名。可辅助判断是不是项目作者或高信号账号。', 'm.author'), c('created', 'HN Firebase item.time，Unix 秒时间戳；这里转换成 UTC 时间。用于判断上榜速度和是否新发生。', 'm.created_at_unix', '', 'unix'), c('HN 链接', 'Hacker News item 页面链接。评论树入口在 raw.kids，主表不展示；需要深挖时再拉评论。', 'm.hn_url', '', 'url'), detail()],
      product_hunt: [rank('PH 排名', 'Product Hunt tab 的固定顺序。采集请求使用 order=VOTES，并优先使用 dailyRank；数值越小越靠前。另看 daily rank / weekly rank 判断日榜和周榜位置。'), name('产品'), desc(), c('votes', 'Product Hunt GraphQL votesCount，当前投票数。它是 PH 社区的启动当天/近期关注信号，不等同于真实使用。', 'm.votes', 'num', 'num'), c('comments', 'Product Hunt GraphQL commentsCount，当前评论数。评论多说明讨论更多，但需要看内容判断是赞同、提问还是质疑。', 'm.comments', 'num', 'num'), c('daily rank', 'Product Hunt dailyRank，产品在 PH 日榜的原生排名。数值越小越靠前；为空时说明 API 没返回该字段。', 'm.daily_rank', 'num', 'num'), c('weekly rank', 'Product Hunt weeklyRank，产品在 PH 周榜的原生排名。比 daily 更平滑，但更滞后。', 'm.weekly_rank', 'num', 'num'), c('created', 'Product Hunt createdAt，PH post 创建时间。用于判断它是不是刚发布。', 'm.created_at', '', 'date'), c('featured', 'Product Hunt featuredAt，PH featured 时间。featured 后票数可能突然增加，所以看增长时要注意这个事件。', 'm.featured_at', '', 'date'), c('website', 'Product Hunt 返回的产品官网链接。用于看真实产品，不是 PH 页面。', 'm.website', '', 'url'), detail()],
      huggingface_models: hfCols('Model'),
      huggingface_datasets: hfCols('Dataset'),
      huggingface_spaces: hfCols('Space'),
      npm_search: [rank('搜索排名', 'npm registry search API 在当前 query 里的返回顺序。npm 的排序由文本匹配和 score 共同决定，不是下载量榜，也不是增长榜；看 weekly/monthly downloads 才是使用量。'), name('Package'), desc(), c('搜索词', '命中这行的 npm registry search query，例如 mcp 或 ai agent。它决定这个包为什么被抓进来；改 query 后下一次 run 生效。', 'm.query_label'), c('版本', 'npm package.version，当前包版本号。版本高低本身不代表质量，要结合更新时间和下载看。', 'm.version'), c('周下载', 'npm search API downloads.weekly，过去一周下载量。它是使用/依赖热度的粗信号；包管理器自动安装会放大成熟包。', 'm.weekly_downloads', 'num', 'num'), c('月下载', 'npm search API downloads.monthly，过去一月下载量。比周下载更平滑，但对刚冒头项目反应更慢。', 'm.monthly_downloads', 'num', 'num'), c('dependents', 'npm search API dependents，依赖这个包的包数量。高值表示生态嵌入深，但新项目通常还很低。', 'm.dependents', 'num', 'num'), c('score', 'npm search API 的 score.final 或 searchScore，是 npm 自己的搜索排序分。它混合文本匹配、质量、流行度、维护等因素；不是下载速度。', 'm.score_final', 'num', 'num'), c('quality', 'npm search API score.detail.quality，npm 自己的质量子分。通常和包元数据、测试/文档等健康度相关；不是我们计算。', 'm.score_quality', 'num', 'num'), c('popularity', 'npm search API score.detail.popularity，npm 自己的流行度子分。通常受下载量、dependents 等影响；成熟包会更占优。', 'm.score_popularity', 'num', 'num'), c('maintenance', 'npm search API score.detail.maintenance，npm 自己的维护子分。通常反映最近发布和维护活跃；低值可能表示不活跃。', 'm.score_maintenance', 'num', 'num'), c('license', 'npm package.license。用于判断复用风险，不是热度或质量分。', 'm.license'), c('keywords', 'npm package.keywords，包作者设置的关键词。用于快速判断领域；可能缺失或营销化。', 'm.keywords', '', 'list'), c('date', 'npm package.date，npm 返回的包更新时间。新近更新配合下载增长更值得看。', 'm.package_date', '', 'date'), detail()],
      pypi_newest: pypiCols('Newest'),
      pypi_updates: pypiCols('Updates'),
      x_seed_accounts: [rank('粉丝排名', 'Settings 里的 X seed account 顺序。当前按粉丝数和 AI 相关初筛得到，用来决定监控哪些个人账号；这是账号池管理信息，不是项目榜。'), name('账号'), desc(), c('username', 'X username，不带 @ 的账号名。头像优先来自已抓到的 X tweet author.profilePicture；没有本轮 tweet 的账号会显示 initials fallback。', 'm.username', '', 'handle'), c('followers', 'X 账号 followers_count，粉丝数。它只用于 seed account 选择，不代表某条 tweet 的项目价值。', 'm.followers_count', 'num', 'num'), c('following', 'X 账号 following_count，关注数。用于了解账号规模和行为，不作为项目打分。', 'm.following_count', 'num', 'num'), c('AI 关键词分', '本地轻量 AI 相关度分：从账号 bio/name 等文本里匹配 AI/agent/coding 等关键词。它只用于初筛 seed accounts，不是 LLM 判断。', 'm.keyword_score', 'num', 'num'), detail()],
      x_tweets: [rank('tweet 排名', 'X Tweets 当前时间范围里的展示顺序。切换 24h / 7d / 30d / 30d+ 会重新从 1 开始；它不是 engagement 排名。读这个 tab 重点看作者、原文和提及对象。'), name('Tweet'), desc(), c('作者', 'tweet author username。因为这些作者是 seed accounts，本身就是信号；我们暂时不把 engagement 当主指标。', 'm.author', '', 'handle'), c('created', 'tweet created_at，tweet 发布时间。配合当前时间范围看它是 24h、7d 还是 30d 内的信号。', 'm.created_at', '', 'date'), c('提及对象', '本地规则从 tweet 文本抽出的对象：@handle、hashtag、非 X 域名、GitHub repo URL、已知项目词。不是 LLM 实体识别，只是帮助快速扫原文。', 'm.mentioned_projects', '', 'projects'), detail()],
      settings_source_health: [rank('序号', 'Settings Source Health 的行号，只用于浏览运行状态。它不是 source 优先级，也不会影响采集顺序或 dashboard 排名。'), c('Source', 'pipeline adapter 名称，例如 github_search、hn_firebase、product_hunt。每个 adapter 对应一个外部数据源或一个数据抓取逻辑。', '$name'), desc(), c('状态', '最近一次该 adapter 的运行状态。正常表示无错误；注意可能是 disabled、API 错误、缺文件或可忽略的 optional source 失败。', 'm.status'), c('说明', '错误、禁用或状态备注。用于判断为什么某个 source 没数据；这不是项目信号。', 'm.note', 'desc'), c('默认节奏', '当前产品假设是每 24 小时跑一次完整 pipeline；现在还没有启用 cron，所以需要手动 run。', 'm.default_schedule'), c('生效规则', 'Settings 改动会写入 pipeline/config.json；下一次 pipeline run 才会使用新配置，当前已导出的 dashboard 不会自动刷新。', 'm.takes_effect'), detail()],
      settings_search_terms: [rank('序号', 'Settings Search Terms 的行号，只用于浏览配置项。它不是搜索词权重；是否抓取由“启用”和配置文件决定。'), c('设置项', '这个配置项的名字。Search Terms 里的行会真实影响抓取 query，不是宽泛的关注关键词。', '$name'), desc(), c('组', '这个 search term 属于哪个入口：GitHub Search、HN Algolia、npm Search 或 X keyword queries。不同组会调用不同 API。', 'm.group'), c('启用', '这个配置项当前是否启用。启用后下一次 pipeline run 会使用；关闭后不会抓对应 query。', 'm.enabled'), c('默认节奏', '当前产品假设是每 24 小时跑一次完整 pipeline；cron 还没启用。', 'm.default_schedule'), c('生效规则', '修改、增加、删除 search term 后，需要保存到 pipeline/config.json，并在下一次 pipeline run 才生效。', 'm.takes_effect'), detail()],
    };
    function hfCols(label) {
      return [rank('HF 排名', 'Hugging Face trending API 的原始返回顺序。HF 按自己的 trendingScore 排资源；我们只保留它的排序和字段，不把它和 GitHub、HN 做加权。'), name(label), desc(), c('trendingScore', 'Hugging Face API 原生 trendingScore。HF 用它做 trending 排序，具体公式不公开；把它当 HF 平台自己的趋势信号，不要和 GitHub star 直接比较。', 'r.trendingScore', 'num', 'num'), c('likes', 'Hugging Face likes，当前点赞数。它是平台内关注度，不等于下载或真实使用。', 'm.likes', 'num', 'num'), c('downloads', 'Hugging Face downloads，当前下载量。对模型/数据集更有意义；Spaces 有时为空或口径不同。', 'm.downloads', 'num', 'num'), c('pipeline', 'Hugging Face pipeline_tag，表示模型/资源类型或任务，例如 text-generation。用于判断领域，不是热度指标。', 'm.pipeline_tag'), c('library/sdk', 'Hugging Face 原生 library_name 或 Spaces sdk。它说明技术栈，例如 transformers、gradio、docker。', 'r.library_name'), c('created', 'Hugging Face createdAt，资源创建时间。新资源上 trending 更接近早期信号。', 'm.created_at', '', 'date'), c('modified', 'Hugging Face lastModified，最近修改时间。近期修改配合 trendingScore 更值得看。', 'm.last_modified', '', 'date'), c('tags', 'Hugging Face tags，平台标签。用于快速判断任务、框架和领域。', 'm.tags', '', 'list'), detail()];
    }
    function pypiCols(label) {
      return [rank('RSS 排名', 'PyPI RSS feed 的原始条目顺序。newest feed 越靠前表示越新发布，updates feed 越靠前表示越新更新；它不是下载量、质量或增长排名。'), name('Package'), desc(), c('feed', 'PyPI RSS feed 来源：newest packages 或 latest updates。newest 看新包，updates 看最近发布。', 'm.feed'), c('版本', 'RSS title 里解析出的 release version。newest feed 可能没有版本，updates feed 通常有。', 'm.version'), c('latest', '从 PyPI JSON API info.version enrich 得到的当前最新版本。为了速度只 enrich 前 N 条；空值通常表示超出 enrich 上限或 API 未返回。', 'm.latest_version'), c('发布时间', 'PyPI RSS pubDate，表示该 package 或 release 出现在 feed 的时间。用来判断是否刚发布/刚更新。', 'm.pub_date', '', 'date'), c('requires_python', 'PyPI JSON info.requires_python，包要求的 Python 版本范围。用于判断可用性，不是热度指标。', 'm.requires_python'), c('license', 'PyPI JSON info.license，包声明的 license。用于复用风险判断，可能为空或非标准。', 'm.license'), c('keywords', 'PyPI JSON info.keywords，包作者填写的关键词。用于快速扫领域，可能不规范。', 'm.keywords'), c('classifiers', 'PyPI JSON classifiers 前几项。它是包作者选择的标准分类，适合判断框架、Python 版本、主题。', 'm.classifiers', 'desc', 'list'), c('project urls', 'PyPI JSON project_urls，通常包含 Homepage、Repository、Docs、Issues。后续 LLM/agent 深挖应优先打开这些链接。', 'm.project_urls', 'desc', 'object'), detail()];
    }

    const sortOptionsByChannel = {
      github_trending: [['native', '原生顺序', '$rank', 'asc'], ['period_stars', '窗口新增 star', 'm.period_stars', 'desc'], ['total_stars', '总 star', 'm.stars_total', 'desc'], ['name', '名称', '$name', 'asc']],
      github_movers_trending_repos: [['native', '原生顺序', '$rank', 'asc'], ['source_score', 'TR score', 'm.source_score', 'desc'], ['stars_velocity', 'star velocity', 'm.stars_velocity', 'desc'], ['forks_velocity', 'fork velocity', 'm.forks_velocity', 'desc'], ['freshness', 'freshness_bonus', 'm.freshness_bonus', 'desc'], ['stars_count', '总 star', 'm.stars_count', 'desc']],
      github_movers_repofomo: [['native', '范围排名', '$rank', 'asc'], ['stars_7d', '7d 新增', 'm.stars_7d', 'desc'], ['stars_30d', '30d 新增', 'm.stars_30d', 'desc'], ['stars_60d', '60d 新增', 'm.stars_60d', 'desc'], ['stars_total', '总 star', 'm.stars_total', 'desc']],
      github_search: [['native', '搜索顺序', '$rank', 'asc'], ['stars', 'stars', 'm.stars', 'desc'], ['forks', 'forks', 'm.forks', 'desc'], ['open_issues', 'issues', 'r.open_issues_count', 'desc'], ['updated', 'updated', 'r.updated_at', 'desc']],
      hn_search: [['native', '搜索顺序', '$rank', 'asc'], ['points', 'points', 'm.points', 'desc'], ['comments', 'comments', 'm.comments', 'desc'], ['created', 'created', 'm.created_at', 'desc']],
      hn_top: [['native', '榜单顺序', '$rank', 'asc'], ['score', 'score', 'm.score', 'desc'], ['comments', 'comments', 'm.comments', 'desc'], ['created', 'created', 'm.created_at_unix', 'desc']],
      product_hunt: [['native', 'PH 顺序', '$rank', 'asc'], ['votes', 'votes', 'm.votes', 'desc'], ['comments', 'comments', 'm.comments', 'desc'], ['daily_rank', 'daily rank', 'm.daily_rank', 'asc'], ['weekly_rank', 'weekly rank', 'm.weekly_rank', 'asc']],
      huggingface_models: [['native', 'HF 顺序', '$rank', 'asc'], ['trendingScore', 'trendingScore', 'r.trendingScore', 'desc'], ['downloads', 'downloads', 'm.downloads', 'desc'], ['likes', 'likes', 'm.likes', 'desc']],
      huggingface_datasets: [['native', 'HF 顺序', '$rank', 'asc'], ['trendingScore', 'trendingScore', 'r.trendingScore', 'desc'], ['downloads', 'downloads', 'm.downloads', 'desc'], ['likes', 'likes', 'm.likes', 'desc']],
      huggingface_spaces: [['native', 'HF 顺序', '$rank', 'asc'], ['trendingScore', 'trendingScore', 'r.trendingScore', 'desc'], ['likes', 'likes', 'm.likes', 'desc']],
      npm_search: [['native', '搜索顺序', '$rank', 'asc'], ['weekly', '周下载', 'm.weekly_downloads', 'desc'], ['monthly', '月下载', 'm.monthly_downloads', 'desc'], ['score', 'score', 'm.score_final', 'desc'], ['dependents', 'dependents', 'm.dependents', 'desc']],
      pypi_newest: [['native', 'RSS 顺序', '$rank', 'asc'], ['pub', '发布时间', 'm.pub_date', 'desc'], ['name', '名称', '$name', 'asc']],
      pypi_updates: [['native', 'RSS 顺序', '$rank', 'asc'], ['pub', '发布时间', 'm.pub_date', 'desc'], ['name', '名称', '$name', 'asc']],
      x_seed_accounts: [['native', '粉丝顺序', '$rank', 'asc'], ['followers', '粉丝', 'm.followers_count', 'desc'], ['following', '关注', 'm.following_count', 'desc'], ['keyword', 'AI 关键词分', 'm.keyword_score', 'desc']],
      x_tweets: [['native', 'tweet 顺序', '$rank', 'asc'], ['created', '发布时间', 'm.created_at', 'desc']],
      settings_source_health: [['native', '配置顺序', '$rank', 'asc'], ['status', '状态', 'm.status', 'asc']],
      settings_search_terms: [['native', '配置顺序', '$rank', 'asc'], ['group', '组', 'm.group', 'asc'], ['name', '名称', '$name', 'asc']],
    };

    const nativeRangeOptionsByChannel = {
      github_movers_repofomo: [
        {id: '7d', label: '7d', kind: 'metric', path: 'm.stars_7d', dir: 'desc', onlyPositive: true},
        {id: '30d', label: '30d', kind: 'metric', path: 'm.stars_30d', dir: 'desc', onlyPositive: true},
        {id: '60d', label: '60d', kind: 'metric', path: 'm.stars_60d', dir: 'desc', onlyPositive: true},
      ],
    };

    function activeChannelGroup() { return activeSection === 'settings' ? settingsPanelDefs() : visibleSourceChannels(); }
    function ensureActiveChannel() {
      const group = activeChannelGroup();
      if (!group.some(channel => channel.id === active)) active = group[0]?.id || '';
      if (activeSection === 'settings') {
        activeSettings = active || 'settings_run_sources';
        localStorage.setItem('heroRadarSettingsTab', activeSettings);
      } else {
        activeSource = active || visibleSourceChannels()[0]?.id || '';
        if (activeSource) localStorage.setItem('heroRadarSourceTab', activeSource);
      }
    }
    function columns() { return columnsByChannel[active] || [rank(), win(), src(), name(), desc(), detail()]; }
    function sortOptions() { return sortOptionsByChannel[active] || [['native', '原生顺序', '$rank', 'asc'], ['name', '名称', '$name', 'asc']]; }
    function sortOptionForColumn(column) {
      return sortOptions().find(option => option[2] === column.path) || null;
    }
    function currentSortDir() {
      const state = currentState();
      const opt = sortOptions().find(row => row[0] === state.sort) || sortOptions()[0];
      return state.sortDir || opt?.[3] || 'asc';
    }
    function currentState() {
      const defaultPageSize = Number(localStorage.getItem('heroRadarDefaultPageSize') || 100);
      if (!channelState[active]) channelState[active] = {window: undefined, sort: 'native', sortDir: undefined, page: 1, pageSize: pageSizes.includes(defaultPageSize) ? defaultPageSize : 100};
      const state = channelState[active];
      const ranges = availableRanges();
      const rangeIds = ranges.map(range => range.id);
      const defaultWindow = rangeIds.includes('24h') ? '24h' : rangeIds[0] || 'all';
      if (!state.window || state.window === 'all' || (rangeIds.length && !rangeIds.includes(state.window))) state.window = defaultWindow;
      return state;
    }
    function columnWidthKey() { return `heroRadarColumnWidths:${active}`; }
    function readColumnWidths() {
      try {
        const parsed = JSON.parse(localStorage.getItem(columnWidthKey()) || '{}');
        return parsed && typeof parsed === 'object' ? parsed : {};
      } catch (_) {
        return {};
      }
    }
    function writeColumnWidths(widths) {
      localStorage.setItem(columnWidthKey(), JSON.stringify(widths));
    }
    function columnWidthStyle(index) {
      const width = Number(readColumnWidths()[index]);
      return width ? ` style="width:${width}px; min-width:${width}px;"` : '';
    }
    function setColumnWidth(index, width) {
      const nextWidth = Math.max(56, Math.round(width));
      const widths = readColumnWidths();
      widths[index] = nextWidth;
      writeColumnWidths(widths);
      document.querySelectorAll(`#tableHead th[data-col-index="${index}"], #rows tr td:nth-child(${index + 1})`).forEach(cell => {
        cell.style.width = `${nextWidth}px`;
        cell.style.minWidth = `${nextWidth}px`;
      });
    }
    function attachColumnResizers() {
      document.querySelectorAll('.col-resizer').forEach(handle => {
        handle.onpointerdown = event => {
          event.preventDefault();
          event.stopPropagation();
          const index = Number(handle.dataset.colIndex);
          const th = handle.closest('th');
          if (!th || Number.isNaN(index)) return;
          const startX = event.clientX;
          const startWidth = th.getBoundingClientRect().width;
          th.classList.add('is-resizing');
          document.body.classList.add('resizing-columns');
          const onMove = moveEvent => setColumnWidth(index, startWidth + moveEvent.clientX - startX);
          const onUp = () => {
            window.removeEventListener('pointermove', onMove);
            th.classList.remove('is-resizing');
            document.body.classList.remove('resizing-columns');
          };
          window.addEventListener('pointermove', onMove);
          window.addEventListener('pointerup', onUp, {once: true});
        };
      });
    }
    function attachHeaderSorters() {
      document.querySelectorAll('#tableHead th[data-sort-id]').forEach(th => {
        th.onclick = event => {
          if (event.target.closest('.col-resizer') || event.target.closest('.hint')) return;
          const state = currentState();
          const sortId = th.dataset.sortId;
          const opt = sortOptions().find(row => row[0] === sortId);
          if (!opt) return;
          if (state.sort === sortId) {
            state.sortDir = currentSortDir() === 'asc' ? 'desc' : 'asc';
          } else {
            state.sort = sortId;
            state.sortDir = opt[3];
          }
          state.page = 1;
          render();
        };
      });
    }
    function channelRows() { return data.items.filter(item => item.channel === active); }
    function availableWindows() {
      const seen = new Set(channelRows().map(item => item.window || 'current'));
      const order = ['24h', '7d', '30d', '30d+', '7d+30d+60d', 'current'];
      return [...seen].sort((a, b) => (order.indexOf(a) === -1 ? 99 : order.indexOf(a)) - (order.indexOf(b) === -1 ? 99 : order.indexOf(b)) || String(a).localeCompare(String(b)));
    }
    function availableRanges() {
      if (nativeRangeOptionsByChannel[active]) return nativeRangeOptionsByChannel[active];
      return availableWindows().map(value => ({id: value, label: value, kind: 'window', value}));
    }
    function selectedRangeOption() {
      const state = currentState();
      return availableRanges().find(range => range.id === state.window) || availableRanges()[0] || null;
    }
    function numericAt(item, path) {
      const value = path?.startsWith('m.') ? meta(item, path.slice(2)) : val(item, path);
      const num = Number(value);
      return Number.isFinite(num) ? num : 0;
    }
    function rowMatchesRange(item) {
      const range = selectedRangeOption();
      if (!range) return true;
      if (range.kind === 'metric') {
        const num = numericAt(item, range.path);
        return range.onlyPositive ? num > 0 : Number.isFinite(num);
      }
      return (item.window || 'current') === range.id;
    }
    function windowedRows() {
      return channelRows().filter(rowMatchesRange);
    }
    function rangeRankValue(item) {
      const range = selectedRangeOption();
      if (!range || range.kind !== 'metric') return item.window_rank || item.source_rank || item.channel_rank;
      const key = `${active}:${range.id}`;
      if (!rangeRankCache[key]) {
        const dir = range.dir || 'desc';
        const rows = channelRows().filter(rowMatchesRange).sort((a, b) => {
          const av = numericAt(a, range.path);
          const bv = numericAt(b, range.path);
          const diff = dir === 'asc' ? av - bv : bv - av;
          return diff || Number(a.source_rank || a.channel_rank || 999999) - Number(b.source_rank || b.channel_rank || 999999);
        });
        rangeRankCache[key] = new Map(rows.map((row, index) => [String(row.item_id), index + 1]));
      }
      return rangeRankCache[key].get(String(item.item_id)) || item.source_rank || item.channel_rank;
    }
    function sortValue(item, path) {
      const value = val(item, path);
      if (path === '$name') return String(value || '').toLowerCase();
      if (path.includes('_at') || path.includes('date') || path === 'm.pub_date') {
        const parsed = Date.parse(value || '');
        return Number.isNaN(parsed) ? 0 : parsed;
      }
      return Number(value ?? -Infinity);
    }
    function sortedRows() {
      const state = currentState();
      const opt = sortOptions().find(row => row[0] === state.sort) || sortOptions()[0];
      const [, , path] = opt;
      const dir = currentSortDir();
      const rows = [...windowedRows()];
      rows.sort((a, b) => {
        const av = sortValue(a, path);
        const bv = sortValue(b, path);
        if (typeof av === 'string' || typeof bv === 'string') return String(av).localeCompare(String(bv));
        const diff = dir === 'asc' ? av - bv : bv - av;
        return diff || Number(a.channel_rank || 999999) - Number(b.channel_rank || 999999);
      });
      return rows;
    }
    function pagedRows() {
      const state = currentState();
      const rows = sortedRows();
      const totalPages = Math.max(1, Math.ceil(rows.length / state.pageSize));
      if (state.page > totalPages) state.page = totalPages;
      const start = (state.page - 1) * state.pageSize;
      return {rows: rows.slice(start, start + state.pageSize), total: rows.length, totalPages, start};
    }
    function windowSummary(rows) {
      const counts = {};
      rows.forEach(item => counts[item.window || 'current'] = (counts[item.window || 'current'] || 0) + 1);
      return Object.entries(counts).map(([k, v]) => `${k}:${v}`).join(' / ') || '-';
    }
    function renderCell(item, column, index) {
      const value = column.path === '$detail' ? '' : val(item, column.path);
      if (column.kind === 'num') return fmt(value);
      if (column.kind === 'date') return fmtDate(value);
      if (column.kind === 'unix') return value ? fmtDate(Number(value) * 1000) : '';
      if (column.kind === 'sparkline') return sparklineHtml(value);
      if (column.kind === 'githubPeriod') return escapeText(({daily: 'stars today', weekly: 'stars this week', monthly: 'stars this month'}[value] || value || ''));
      if (column.kind === 'pill') return pill(value || '');
      if (column.kind === 'link') return link(item.url, value || '');
      if (column.kind === 'url') return value ? link(value, '打开') : '';
      if (column.kind === 'list') return escapeText(arr(value));
      if (column.kind === 'object') return escapeText(objPairs(value));
      if (column.kind === 'accounts') return escapeText(arr((value || []).map(x => `@${x}`), 8));
      if (column.kind === 'handle') return xPersonHtml(value, meta(item, 'author_avatar'));
      if (column.kind === 'projects') return escapeText(projectText(value));
      if (column.kind === 'detail') return detailHtml(item);
      return escapeText(value ?? '');
    }
    function currentSparklineMax() {
      const values = [];
      windowedRows().forEach(item => {
        const row = meta(item, 'sparkline');
        if (Array.isArray(row)) row.forEach(value => {
          const num = Number(value);
          if (Number.isFinite(num)) values.push(num);
        });
      });
      return Math.max(1, ...values);
    }
    function sparklineHtml(value) {
      if (!Array.isArray(value) || !value.length) return '';
      const nums = value.map(Number).filter(Number.isFinite);
      if (!nums.length) return '';
      const width = 92;
      const height = 28;
      const pad = 3;
      const max = currentSparklineMax();
      const xStep = nums.length > 1 ? (width - pad * 2) / (nums.length - 1) : 0;
      const points = nums.map((num, index) => {
        const x = pad + index * xStep;
        const y = height - pad - (num / max) * (height - pad * 2);
        return `${x.toFixed(1)},${Math.max(pad, Math.min(height - pad, y)).toFixed(1)}`;
      });
      const area = [`${pad},${height - pad}`, ...points, `${width - pad},${height - pad}`].join(' ');
      const last = nums[nums.length - 1];
      const lastPoint = points[points.length - 1].split(',');
      const title = `sparkline: ${nums.map(num => fmt(num)).join(' / ')}；统一缩放 max=${fmt(max)}`;
      return `<span class="sparkline" title="${escapeText(title)}"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeText(title)}"><line class="spark-axis" x1="${pad}" y1="${height - pad}" x2="${width - pad}" y2="${height - pad}"></line><polygon class="spark-area" points="${area}"></polygon><polyline class="spark-line" points="${points.join(' ')}"></polyline><circle class="spark-dot" cx="${lastPoint[0]}" cy="${lastPoint[1]}" r="2.2"></circle></svg><span class="sparkline-last">${escapeText(fmt(last))}</span></span>`;
    }
    function projectText(projects) {
      if (!Array.isArray(projects)) return '';
      return projects.map(p => p && typeof p === 'object' ? (p.name || p.key || '') : '').filter(Boolean).slice(0, 8).join('，');
    }
    function samplesHtml(samples) {
      if (!Array.isArray(samples) || !samples.length) return '';
      return samples.slice(0, 3).map(sample => {
        const author = sample.author ? `@${escapeText(sample.author)} · ` : '';
        const text = escapeText(sample.text || '');
        const url = escapeUrl(sample.url || '');
        const sampleLink = url !== '#' ? ` <a href="${url}" target="_blank" rel="noreferrer">原文</a>` : '';
        return `<div class="sample">${author}${text}${sampleLink}</div>`;
      }).join('');
    }
    function compactRawHtml(item) {
      const metadata = item.metadata || {};
      const rawData = item.raw || {};
      const keys = ['homepage', 'repository', 'bugs', 'default_branch', 'archived', 'fork', 'is_template', 'has_discussions', 'has_pages', 'private', 'gated', 'disabled', 'sdk', 'library_name', 'latest_upload_time', 'home_page'];
      const pairs = [];
      keys.forEach(key => {
        const value = rawData[key] ?? metadata[key];
        if (value !== null && value !== undefined && value !== '') pairs.push([key, value]);
      });
      if (!pairs.length) return '';
      return `<div class="raw-grid">${pairs.map(([k, v]) => `<div class="raw-cell"><strong>${escapeText(k)}</strong>: ${escapeText(Array.isArray(v) ? v.join(', ') : String(v))}</div>`).join('')}</div>`;
    }
    function detailHtml(item) {
      const facts = (item.facts || []).map(escapeText).join(' · ');
      const descText = item.description ? `<p>${escapeText(item.description)}</p>` : '';
      const projects = projectText(meta(item, 'mentioned_projects'));
      const projectLine = projects ? `<p>提及项目/账号：${escapeText(projects)}</p>` : '';
      return `<details><summary>查看</summary><div class="detail-block">${descText}${projectLine}<p class="why">${facts}</p>${samplesHtml(meta(item, 'sample_tweets'))}${compactRawHtml(item)}</div></details>`;
    }
    const workspaceIcons = {
      source: '<svg viewBox="0 0 24 24" aria-hidden="true"><ellipse cx="12" cy="5" rx="7" ry="3"></ellipse><path d="M5 5v6c0 1.7 3.1 3 7 3s7-1.3 7-3V5"></path><path d="M5 11v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"></path></svg>',
      settings: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15.5A3.5 3.5 0 1 0 12 8a3.5 3.5 0 0 0 0 7.5Z"></path><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 0 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1A2 2 0 0 1 4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.6-1H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.3 7A2 2 0 0 1 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3h.1a1.7 1.7 0 0 0 1-1.6V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.6h.1a1.7 1.7 0 0 0 1.9-.3l.1-.1A2 2 0 0 1 19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9v.1a1.7 1.7 0 0 0 1.6 1h.1a2 2 0 0 1 0 4H21a1.7 1.7 0 0 0-1.6 1Z"></path></svg>',
    };
    function renderWorkspaceTabs() {
      const tabs = document.getElementById('workspaceTabs');
      const entries = [
        ['source', 'Source'],
        ['settings', 'Setting'],
      ];
      tabs.innerHTML = entries.map(([id, label]) => `
        <button type="button" class="${activeSection === id ? 'active' : ''}" data-section="${id}" title="${label}">
          <span class="nav-icon">${workspaceIcons[id] || ''}</span><span class="full">${label}</span>
        </button>
      `).join('');
      tabs.querySelectorAll('button').forEach(btn => {
        btn.onclick = () => {
          activeSection = btn.dataset.section || 'source';
          localStorage.setItem('heroRadarSection', activeSection);
          active = activeSection === 'settings' ? activeSettings : activeSource;
          ensureActiveChannel();
          render();
        };
      });
    }
    function renderSettingsSubnav() {
      const rail = document.getElementById('settingsSubrail');
      const nav = document.getElementById('settingsSubnav');
      if (activeSection !== 'settings') {
        rail.hidden = true;
        nav.innerHTML = '';
        return;
      }
      rail.hidden = false;
      nav.innerHTML = settingsPanelDefs().map(panel => `
        <button type="button" class="${panel.id === active ? 'active' : ''}" data-settings-panel="${panel.id}">
          <span class="subnav-label">${escapeText(panel.label)}</span>
        </button>
      `).join('');
      nav.querySelectorAll('button').forEach(btn => {
        btn.onclick = () => {
          activeSettings = btn.dataset.settingsPanel || 'settings_run_sources';
          active = activeSettings;
          localStorage.setItem('heroRadarSettingsTab', activeSettings);
          render();
        };
      });
    }
    function renderChannelTabs() {
      const tabs = document.getElementById('channelTabs');
      if (activeSection === 'settings') {
        tabs.hidden = true;
        tabs.innerHTML = '';
        return;
      }
      tabs.hidden = false;
      const group = activeChannelGroup();
      tabs.innerHTML = '';
      if (!group.length) {
        tabs.innerHTML = '<div class="muted">这个空间还没有可展示的频道。</div>';
        return;
      }
      group.forEach(channel => {
        const btn = document.createElement('button');
        btn.className = channel.id === active ? 'active' : '';
        btn.textContent = channel.label;
        if (channel.description) {
          btn.dataset.tip = channel.description;
          btn.setAttribute('aria-label', `${channel.label}: ${channel.description}`);
        }
        btn.onclick = () => {
          activeSource = channel.id;
          active = activeSource;
          localStorage.setItem('heroRadarSourceTab', activeSource);
          render();
        };
        tabs.appendChild(btn);
      });
    }
    function renderControls() {
      const controls = document.getElementById('controls');
      if (activeSection === 'settings') {
        controls.hidden = true;
        controls.innerHTML = '';
        return;
      }
      controls.hidden = false;
      const state = currentState();
      const ranges = availableRanges();
      if (ranges.length <= 1) {
        controls.hidden = true;
        controls.innerHTML = '';
        return;
      }
      const label = nativeRangeOptionsByChannel[active] ? '原生范围' : '时间范围';
      const windowButtons = ranges.map(range => `<button type="button" class="control-button ${state.window === range.id ? 'active' : ''}" data-control="window" data-value="${escapeText(range.id)}">${escapeText(range.label)}</button>`).join('');
      controls.innerHTML = `<div class="control-group"><span class="control-label">${label}</span>${windowButtons}</div>`;
      controls.querySelectorAll('button').forEach(btn => {
        btn.onclick = () => {
          const control = btn.dataset.control;
          const value = btn.dataset.value;
          if (control === 'window') { state.window = value; state.page = 1; }
          render();
        };
      });
    }
    function renderCards() {
      const cards = document.getElementById('cards');
      cards.hidden = true;
      cards.innerHTML = '';
    }
    function renderStatus() {
      const box = document.getElementById('statusList');
      if (activeSection !== 'settings') {
        box.innerHTML = '';
        return;
      }
      const mode = apiWritable
        ? '当前通过本地 server 打开，可以保存配置和手动运行 pipeline。'
        : '当前是 file:// 静态预览；可以查看和本地试改表单，但不能写入 pipeline/config.json。';
      const message = configMessage ? `<div class="message-line ${configMessageKind || ''}">${escapeText(configMessage)}</div>` : '';
      box.innerHTML = `<div class="settings-note"><strong>Settings 是控制面板，不是项目榜。</strong> ${mode} 配置改动的目标文件是 <code>pipeline/config.json</code>；确认后下一次 pipeline run 生效。默认节奏先按每 24 小时一轮设计，cron 暂不启用。${message}</div>`;
    }
    function settingField(path, label, help, type = 'text', attrs = '') {
      const value = getConfig(path, '');
      return `<div class="setting-row two"><div><div class="setting-label">${escapeText(label)}</div><div class="setting-help">${escapeText(help)}</div></div><input class="field" type="${escapeText(type)}" value="${escapeText(value ?? '')}" data-config-path="${escapeText(path)}" data-type="${type === 'number' ? 'number' : 'text'}" ${attrs}></div>`;
    }
    function settingCheckbox(path, label, help) {
      const checked = getConfig(path, false) ? 'checked' : '';
      return `<label class="toggle-row"><input type="checkbox" ${checked} data-config-path="${escapeText(path)}" data-type="boolean"><span><strong>${escapeText(label)}</strong><div class="setting-help">${escapeText(help)}</div></span></label>`;
    }
    function settingSelect(path, label, help, options) {
      const value = String(getConfig(path, ''));
      return `<div class="setting-row two"><div><div class="setting-label">${escapeText(label)}</div><div class="setting-help">${escapeText(help)}</div></div><select class="select" data-config-path="${escapeText(path)}" data-type="text">${options.map(option => `<option value="${escapeText(option)}" ${String(option) === value ? 'selected' : ''}>${escapeText(option)}</option>`).join('')}</select></div>`;
    }
    function sourceHealthBadge(key) {
      const error = data.source_errors?.[key];
      if (error === undefined) return '<span class="status-dot">n/a</span>';
      return `<span class="status-dot ${error ? 'warn' : 'ok'}">${error ? '注意' : '正常'}</span>`;
    }
    function sourceCard(title, key, note, controlsHtml) {
      const error = data.source_errors?.[key];
      return `<div class="settings-card">
        <div class="settings-card-head"><div><div class="settings-card-title">${escapeText(title)}</div><div class="settings-card-note">${escapeText(note)}</div></div>${sourceHealthBadge(key)}</div>
        <div class="setting-list">${controlsHtml}${error ? `<div class="message-line warn">${escapeText(error)}</div>` : ''}</div>
      </div>`;
    }
    function settingsToolbar() {
      const dirty = configDirty();
      const saveDisabled = !apiWritable || !dirty || configBusy ? 'disabled' : '';
      const runDisabled = !apiWritable || dirty || configBusy ? 'disabled' : '';
      const reloadDisabled = !apiWritable || configBusy ? 'disabled' : '';
      const mode = apiWritable ? 'server mode' : 'file preview';
      const dirtyText = dirty ? '有未保存修改' : '配置已同步';
      return `<section class="settings-toolbar">
        <div><div class="title">${escapeText(channelLabel(active))}</div><div class="copy">${escapeText(mode)} · ${escapeText(dirtyText)} · 保存后下一次 pipeline run 生效</div></div>
        <div class="settings-actions">
          <button type="button" class="primary-button" data-action="save-config" ${saveDisabled}>保存配置</button>
          <button type="button" data-action="reload-config" ${reloadDisabled}>从 API 重载</button>
          <button type="button" data-action="run-now" ${runDisabled}>Run now</button>
        </div>
      </section>`;
    }
    function renderRunSourcesSettings() {
      const cards = [
        sourceCard('GitHub Trending', 'github_trending', '抓 GitHub Trending daily / weekly / monthly；语言 scope 仍由 config 控制但不在 UI 主动调。', '<div class="setting-help">Always on。当前不在 Settings 暴露 language/scope filter。</div>'),
        sourceCard('Trending Repos', 'github_movers', '第三方 GitHub momentum source；抓 daily / weekly / monthly。', settingCheckbox('github_movers.trending_repos.enabled', '启用 Trending Repos', '关闭后下一次 run 不抓这个 mover source。') + settingField('github_movers.trending_repos.limit_per_period', '每窗口上限', '配置请求/解析后每个 period 最多保留多少条；source 可能实际只给更少。', 'number', 'min="1" step="1"')),
        sourceCard('RepoFOMO', 'github_movers', '周/月级 repo movers 补充源。', settingCheckbox('github_movers.repofomo.enabled', '启用 RepoFOMO', '关闭后下一次 run 不抓 RepoFOMO leaderboard。') + settingField('github_movers.repofomo.limit', '保留上限', 'leaderboard 最多保留多少条。', 'number', 'min="1" step="1"')),
        sourceCard('GitHub Search', 'github_search', '按 Search Terms 里的 GitHub query 主动搜索 repo。', settingField('github_search.max_results_per_query', '每个 query 最大结果', '每个 GitHub Search query 最多抓多少条；受 GitHub API 分页和 rate limit 影响。', 'number', 'min="1" step="1"') + settingField('github_search.per_page', '每页大小', 'GitHub Search API per_page，最大通常是 100。', 'number', 'min="1" max="100" step="1"')),
        sourceCard('HN Search', 'hn_algolia', 'HN Algolia search_by_date，按 Search Terms 和窗口抓讨论。', settingField('hn.algolia_hits_per_page', '每 query/window 上限', 'HN Algolia 每个 query 和时间窗最多返回多少条。', 'number', 'min="1" step="1"')),
        sourceCard('HN Top', 'hn_firebase', 'HN Firebase top/new/best 榜单。', settingField('hn.firebase_limit', '每个榜单上限', 'topstories/newstories/beststories 每个 list 取多少条；你已决定 HN Top 可以 100。', 'number', 'min="1" step="1"') + settingField('hn.firebase_workers', '并发 worker', '拉 HN item detail 时的并发数；过高可能不稳。', 'number', 'min="1" step="1"')),
        sourceCard('Product Hunt', 'product_hunt', 'PH GraphQL launches/posts。', settingCheckbox('product_hunt.enabled', '启用 Product Hunt', '需要 PRODUCTHUNT_TOKEN；关闭后下一次 run 跳过。') + settingField('product_hunt.first', '请求 first', 'GraphQL 请求的 first 参数；PH 可能仍只返回实际可用数量。', 'number', 'min="1" step="1"')),
        sourceCard('HF Spaces', 'huggingface_trending', 'Hugging Face trending。Models/Datasets 仍采集但 dashboard 主 source 隐藏。', settingField('huggingface.limit', '每类上限', 'models / datasets / spaces 每类最多请求多少条。', 'number', 'min="1" step="1"')),
        sourceCard('npm Search', 'npm_search', '按 Search Terms 里的 npm query 搜包。', settingCheckbox('npm.enabled', '启用 npm Search', '关闭后下一次 run 跳过 npm。') + settingField('npm.size', '每个 query size', 'npm registry search 每个 query 请求数量。', 'number', 'min="1" step="1"')),
        sourceCard('PyPI Feeds', 'pypi_feeds', 'PyPI newest / updates RSS，并对前 N 条做 JSON enrich。', settingCheckbox('pypi.enabled', '启用 PyPI', '关闭后下一次 run 跳过 PyPI feeds。') + settingField('pypi.limit_per_feed', 'RSS 每 feed 上限', 'newest 和 updates 各保留多少条。', 'number', 'min="1" step="1"') + settingField('pypi.json_enrich_limit_per_feed', 'JSON enrich 上限', '每个 feed 对前多少条请求 PyPI JSON 补 project_urls/classifiers。', 'number', 'min="0" step="1"')),
        sourceCard('Apify configured', 'apify_configured', '付费 actor 防误跑 gate；真正执行还需要 APIFY_ENABLE_RUNS=true。', settingCheckbox('apify.enabled', '启用 Apify configured adapter', '只打开 config 还不够；没有 APIFY_ENABLE_RUNS=true 时仍会拒绝付费 actor。') + settingField('apify.max_results_per_run', 'run 最大结果', '通用 Apify adapter 的每轮最大结果。', 'number', 'min="1" step="1"')),
        sourceCard('OSSInsight optional', 'ossinsight_trending_optional', '可选 source，目前 endpoint 不稳定，失败不阻塞。', settingCheckbox('ossinsight.enabled', '启用 OSSInsight optional', '关闭后下一次 run 不请求 OSSInsight。')),
      ];
      return `<section class="settings-section"><h2>Run & Sources</h2><p class="section-copy">控制每个 source 是否启用、抓取上限和最近一次运行状态。这里不做打分，只改变下一次 pipeline 如何采集。</p><div class="settings-grid">${cards.join('')}</div></section>
        <section class="settings-section"><h2>Source health</h2><p class="section-copy">最近一次 snapshot 的 adapter 状态。正常只表示请求/解析没有报错，不代表 source 数据一定完整。</p><div class="settings-table">${Object.entries(data.source_errors || {}).map(([source, error]) => `<div class="status-row"><strong>${escapeText(source)}</strong>${sourceHealthBadge(source)}<div class="message-line ${error ? 'warn' : 'good'}">${escapeText(error || '正常')}</div></div>`).join('')}</div></section>`;
    }
    function queryEditor(title, path, kind, copy) {
      const list = getConfig(path, []) || [];
      const rows = list.map((entry, index) => {
        const label = kind === 'object' ? (entry.label || '') : `X keyword ${index + 1}`;
        const query = kind === 'object' ? (entry.query || '') : String(entry || '');
        const labelInput = kind === 'object' ? `<input class="field" value="${escapeText(label)}" data-config-path="${escapeText(path)}.${index}.label" data-type="text" placeholder="label">` : `<div class="setting-label">${escapeText(label)}</div>`;
        const queryPath = kind === 'object' ? `${path}.${index}.query` : `${path}.${index}`;
        return `<div class="query-row">${labelInput}<textarea class="textarea" data-config-path="${escapeText(queryPath)}" data-type="text" placeholder="query">${escapeText(query)}</textarea><button type="button" class="small-button danger-button" data-action="remove-query" data-path="${escapeText(path)}" data-index="${index}">删除</button></div>`;
      }).join('') || '<div class="empty">还没有 query。</div>';
      return `<section class="settings-section"><h2>${escapeText(title)}</h2><p class="section-copy">${escapeText(copy)}</p><div class="settings-table">${rows}</div><div style="margin-top:10px"><button type="button" class="small-button" data-action="add-query" data-path="${escapeText(path)}" data-kind="${escapeText(kind)}">新增 query</button></div></section>`;
    }
    function renderSearchTermsSettings() {
      return `${queryEditor('GitHub Search queries', 'github_search.queries', 'object', '真实传给 GitHub Search API 的 repo query。想关注什么就直接加 query。')}
        ${queryEditor('HN Algolia queries', 'hn.algolia_queries', 'object', '真实传给 HN Algolia search_by_date 的 query。每个 query 会按 24h / 7d / 30d 窗口抓。')}
        ${queryEditor('npm Search queries', 'npm.queries', 'object', '真实传给 npm registry search 的 query。适合补充 package 生态里的工具信号。')}
        ${queryEditor('X keyword queries', 'apify.x_keyword_queries', 'string', '预留给 X keyword/topic 抓取。当前 X 主信号仍是 seed accounts tweets。')}`;
    }
    function renderXMonitoringSettings() {
      const accounts = getConfig('apify.x_seed_accounts', []) || [];
      const accountRows = accounts.map((account, index) => `<div class="account-row"><div>${xPersonHtml(account)}<div class="setting-help">apify.x_seed_accounts[${index}]</div></div><button type="button" class="small-button danger-button" data-action="remove-account" data-index="${index}">移除</button></div>`).join('') || '<div class="empty">还没有 seed account。</div>';
      const windows = ['24h', '7d', '30d', '30d+'];
      const selectedWindows = new Set(getConfig('apify.x_tweets.windows', []) || []);
      const windowToggles = windows.map(win => `<label class="toggle-row"><input type="checkbox" data-action="toggle-x-window" data-window="${win}" ${selectedWindows.has(win) ? 'checked' : ''}><span>${win}</span></label>`).join('');
      return `<section class="settings-section"><h2>X Monitoring</h2><p class="section-copy">X 先按 seed accounts 抓 tweets。我们现在弱化 engagement，重点是这些人提到了什么项目、原文怎么说。</p><div class="settings-grid">
          <div class="settings-card"><div class="settings-card-head"><div><div class="settings-card-title">Tweet scrape</div><div class="settings-card-note">下一次 X tweets run 使用这些参数。</div></div>${sourceHealthBadge('x_tweets')}</div>
            <div class="setting-list">
              ${settingCheckbox('apify.x_tweets.enabled', '启用 X tweets', '关闭后 dashboard 不再从 x_tweets_latest.json 导入 tweets。')}
              ${settingField('apify.x_tweets.accounts_limit', '账号上限', '最多从 seed accounts 里取多少个账号抓 tweets。', 'number', 'min="1" step="1"')}
              ${settingField('apify.x_tweets.max_tweets_per_account', '每账号 tweet 上限', 'Apify 抓取时每个账号最多多少条；这个会影响成本。', 'number', 'min="1" step="1"')}
              ${settingField('apify.x_tweets.dashboard_tweet_limit', 'dashboard tweet 上限', '导入 dashboard 的 tweet 总数上限，不等于实际 actor 抓取上限。', 'number', 'min="1" step="1"')}
              ${settingCheckbox('apify.x_tweets.use_since_date_filter', '使用 actor sinceDate 过滤', '默认关闭。这个 actor 会先按每账号条数截断再做 date filter，容易把不活跃账号过滤成 0；关闭时由本地 tweet store 按窗口过滤。')}
              <div class="setting-row stack"><div class="setting-label">时间窗</div><div class="settings-actions">${windowToggles}</div><div class="setting-help">用于给 tweet 标 24h / 7d / 30d / 30d+ 窗口；下一次 run 生效。</div></div>
              ${settingCheckbox('apify.x_tweets.include_retweets', '包含 retweets', '打开后 retweet 也会进入抓取/导入。')}
              ${settingCheckbox('apify.x_tweets.include_replies', '包含 replies', '打开后 replies 也会进入抓取/导入，噪声通常更高。')}
            </div>
          </div>
          <div class="settings-card"><div class="settings-card-head"><div><div class="settings-card-title">Seed discovery</div><div class="settings-card-note">从 following 候选池筛 AI 相关个人账号。</div></div>${sourceHealthBadge('x_seed_accounts')}</div>
            <div class="setting-list">
              ${settingCheckbox('apify.x_seed_from_following.enabled', '启用 following seed file', '从 x_following_ai_seed_candidates_latest.json 读取候选并筛个人账号。')}
              ${settingField('apify.x_seed_from_following.limit', '候选展示上限', 'Settings 里 X Accounts 的候选数量上限。', 'number', 'min="1" step="1"')}
              ${settingSelect('apify.x_seed_from_following.sort', '候选排序', '当前按 followers_count 筛前 N。', ['followers_count', 'keyword_score', 'following_count'])}
            </div>
          </div>
        </div></section>
        <section class="settings-section"><h2>Seed accounts</h2><p class="section-copy">手动维护的账号池。这里只放个人账号，不放 official accounts。保存后下一次 X tweets run 生效。</p><div class="setting-row two"><div><div class="setting-label">新增账号</div><div class="setting-help">输入 handle，不需要 @。</div></div><div class="settings-actions"><input class="field" id="newXAccount" placeholder="karpathy"><button type="button" class="small-button" data-action="add-account">添加</button></div></div><div class="settings-table" style="margin-top:10px">${accountRows}</div></section>`;
    }
    function renderDisplaySettings() {
      const hidden = hiddenSourceSet();
      const defaultPageSize = Number(localStorage.getItem('heroRadarDefaultPageSize') || 100);
      const currentTheme = document.body.dataset.theme === 'dark' ? 'dark' : 'light';
      const themeButtons = ['light', 'dark'].map(theme => `<button type="button" class="control-button ${theme === currentTheme ? 'active' : ''}" data-action="set-theme" data-theme-value="${theme}">${theme === 'light' ? '浅色' : '深色'}</button>`).join('');
      const sourceRows = channels.map(channel => `<label class="toggle-row settings-card compact"><input type="checkbox" data-action="toggle-source-visibility" data-channel="${escapeText(channel.id)}" ${hidden.has(channel.id) ? '' : 'checked'}><span><strong>${escapeText(channel.label)}</strong><div class="setting-help">${fmt(channel.count)} rows · 只影响本浏览器显示，不改数据库和 pipeline。</div></span></label>`).join('');
      return `<section class="settings-section"><h2>Display</h2><p class="section-copy">这些是浏览器本地显示偏好，存在 localStorage，不写入 pipeline/config.json。</p><div class="settings-grid">
        <div class="settings-card"><div class="settings-card-title">默认分页</div><div class="settings-card-note">新打开 tab 时使用的 page size。</div><select class="select" data-action="default-page-size" style="margin-top:8px">${pageSizes.map(size => `<option value="${size}" ${size === defaultPageSize ? 'selected' : ''}>${size}/页</option>`).join('')}</select></div>
        <div class="settings-card"><div class="settings-card-title">Theme</div><div class="settings-card-note">只影响本浏览器显示，设置存在 localStorage。</div><div class="settings-actions" style="margin-top:8px">${themeButtons}</div></div>
      </div></section><section class="settings-section"><h2>Source tabs visibility</h2><p class="section-copy">隐藏 source tab 只是 UI 偏好；不会删除 dashboard payload、数据库或采集配置。</p><div class="settings-grid">${sourceRows}</div></section>`;
    }
    function renderApiStatusSettings() {
      const apiStatus = data.config_meta?.api_status || {};
      const rows = Object.values(apiStatus).map(row => `<div class="status-row"><strong>${escapeText(row.label)}</strong><span class="status-dot ${row.configured ? 'ok' : 'warn'}">${row.configured ? 'configured' : 'missing/off'}</span><div><code>${escapeText(row.env)}</code><div class="setting-help">${escapeText(row.note)}</div></div></div>`).join('');
      return `<section class="settings-section"><h2>API Status</h2><p class="section-copy">这里只显示环境变量是否配置，不显示 token 明文。Apify paid actor 还额外受 APIFY_ENABLE_RUNS gate 控制。</p><div class="settings-table">${rows}</div></section>`;
    }
    function renderSettingsPanel() {
      const panel = document.getElementById('settingsPanel');
      if (activeSection !== 'settings') {
        panel.hidden = true;
        panel.innerHTML = '';
        return;
      }
      panel.hidden = false;
      const body = active === 'settings_search_terms'
        ? renderSearchTermsSettings()
        : active === 'settings_x_monitoring'
          ? renderXMonitoringSettings()
          : active === 'settings_display'
            ? renderDisplaySettings()
            : active === 'settings_api_status'
              ? renderApiStatusSettings()
              : renderRunSourcesSettings();
      panel.innerHTML = settingsToolbar() + body;
      attachSettingsHandlers();
    }
    function attachSettingsHandlers() {
      const panel = document.getElementById('settingsPanel');
      panel.querySelectorAll('[data-config-path]').forEach(input => {
        input.onchange = () => {
          const path = input.dataset.configPath;
          const type = input.dataset.type;
          const value = type === 'boolean' ? input.checked : type === 'number' ? Number(input.value || 0) : input.value;
          setConfig(path, value);
          configMessage = '配置已修改，尚未保存。';
          configMessageKind = 'warn';
          render();
        };
      });
      panel.querySelectorAll('[data-action]').forEach(el => {
        el.onclick = event => handleSettingsAction(event, el);
        el.onchange = event => handleSettingsAction(event, el);
      });
    }
    async function handleSettingsAction(event, el) {
      const action = el.dataset.action;
      if (!action) return;
      if (['save-config', 'reload-config', 'run-now'].includes(action) && event.type !== 'click') return;
      if (action === 'save-config') return saveConfig();
      if (action === 'reload-config') return reloadConfigFromApi(false);
      if (action === 'run-now') return runPipelineNow();
      if (action === 'add-query') {
        const list = [...(getConfig(el.dataset.path, []) || [])];
        list.push(el.dataset.kind === 'object' ? {label: 'new', query: ''} : '');
        setConfig(el.dataset.path, list);
        configMessage = '已新增 query，保存后下一次 run 生效。';
        configMessageKind = 'warn';
        render();
      }
      if (action === 'remove-query') {
        const list = [...(getConfig(el.dataset.path, []) || [])];
        list.splice(Number(el.dataset.index), 1);
        setConfig(el.dataset.path, list);
        configMessage = '已删除 query，保存后下一次 run 生效。';
        configMessageKind = 'warn';
        render();
      }
      if (action === 'add-account') {
        const input = document.getElementById('newXAccount');
        const handle = String(input?.value || '').trim().replace(/^@/, '');
        if (!handle) return;
        const accounts = [...(getConfig('apify.x_seed_accounts', []) || [])];
        if (!accounts.includes(handle)) accounts.push(handle);
        setConfig('apify.x_seed_accounts', accounts);
        configMessage = `已加入 @${handle}，保存后下一次 X run 生效。`;
        configMessageKind = 'warn';
        render();
      }
      if (action === 'remove-account') {
        const accounts = [...(getConfig('apify.x_seed_accounts', []) || [])];
        accounts.splice(Number(el.dataset.index), 1);
        setConfig('apify.x_seed_accounts', accounts);
        configMessage = '已移除账号，保存后下一次 X run 生效。';
        configMessageKind = 'warn';
        render();
      }
      if (action === 'toggle-x-window' && event.type === 'change') {
        const windows = new Set(getConfig('apify.x_tweets.windows', []) || []);
        if (el.checked) windows.add(el.dataset.window); else windows.delete(el.dataset.window);
        setConfig('apify.x_tweets.windows', [...windows]);
        configMessage = 'X 时间窗已修改，保存后下一次 run 生效。';
        configMessageKind = 'warn';
        render();
      }
      if (action === 'default-page-size' && event.type === 'change') {
        localStorage.setItem('heroRadarDefaultPageSize', String(el.value));
        configMessage = `默认分页已改为 ${el.value}/页。`;
        configMessageKind = 'good';
        render();
      }
      if (action === 'set-theme' && event.type === 'click') {
        setTheme(el.dataset.themeValue);
        configMessage = `显示主题已切换为 ${el.dataset.themeValue === 'dark' ? '深色' : '浅色'}。`;
        configMessageKind = 'good';
        render();
      }
      if (action === 'toggle-source-visibility' && event.type === 'change') {
        const hidden = hiddenSourceSet();
        if (el.checked) hidden.delete(el.dataset.channel); else hidden.add(el.dataset.channel);
        setLocalJson('heroRadarHiddenSources', [...hidden]);
        configMessage = 'Source tab 显示偏好已更新；这不影响采集。';
        configMessageKind = 'good';
        ensureActiveChannel();
        render();
      }
    }
    async function saveConfig() {
      if (!apiWritable || configBusy) return;
      configBusy = true;
      configMessage = '正在保存 pipeline/config.json...';
      configMessageKind = '';
      render();
      try {
        const resp = await fetch('/api/config', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({config: runtimeConfig}),
        });
        const payload = await resp.json();
        if (!resp.ok || !payload.ok) throw new Error(payload.error || `HTTP ${resp.status}`);
        savedConfigText = JSON.stringify(runtimeConfig);
        configMessage = `已保存；backup: ${payload.backup_path || 'created'}。下一次 run 生效。`;
        configMessageKind = 'good';
      } catch (error) {
        configMessage = `保存失败：${error.message}`;
        configMessageKind = 'warn';
      } finally {
        configBusy = false;
        render();
      }
    }
    async function reloadConfigFromApi(silent = false) {
      if (!apiWritable || configBusy) return;
      try {
        const resp = await fetch('/api/config', {cache: 'no-store'});
        const payload = await resp.json();
        if (!resp.ok || !payload.config) throw new Error(payload.error || `HTTP ${resp.status}`);
        runtimeConfig = cloneJson(payload.config);
        savedConfigText = JSON.stringify(runtimeConfig);
        if (!silent) {
          configMessage = '已从 /api/config 重载。';
          configMessageKind = 'good';
        }
        render();
      } catch (error) {
        if (!silent) {
          configMessage = `重载失败：${error.message}`;
          configMessageKind = 'warn';
          render();
        }
      }
    }
    async function runPipelineNow() {
      if (!apiWritable || configBusy || configDirty()) return;
      configBusy = true;
      configMessage = 'Pipeline 正在运行；完成后会刷新 dashboard。';
      configMessageKind = '';
      render();
      try {
        const resp = await fetch('/api/run', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({}),
        });
        const payload = await resp.json();
        if (!resp.ok || !payload.ok) throw new Error(payload.stderr || payload.error || `HTTP ${resp.status}`);
        configMessage = 'Pipeline 完成，正在刷新 dashboard。';
        configMessageKind = 'good';
        location.reload();
      } catch (error) {
        configBusy = false;
        configMessage = `Run failed：${String(error.message || error).slice(0, 800)}`;
        configMessageKind = 'warn';
        render();
      }
    }
    function renderHead() {
      if (activeSection === 'settings') {
        document.getElementById('tableHead').innerHTML = '';
        return;
      }
      const state = currentState();
      document.getElementById('tableHead').innerHTML = `<tr>${columns().map((column, index) => {
        const sortOpt = sortOptionForColumn(column);
        const isActiveSort = sortOpt && state.sort === sortOpt[0];
        const sortArrow = currentSortDir() === 'asc' ? '↑' : '↓';
        const sortAttrs = sortOpt ? ` data-sort-id="${escapeText(sortOpt[0])}" title="点击按 ${escapeText(column.label)} 排序"` : '';
        const sortableClass = sortOpt ? ' sortable' : '';
        const activeClass = isActiveSort ? ' sort-active' : '';
        return `<th class="${column.cls || ''}${sortableClass}${activeClass}" data-col-index="${index}"${sortAttrs}${columnWidthStyle(index)}><span class="th-inner"><span class="th-label">${escapeText(column.label)}</span>${sortOpt ? `<span class="sort-indicator">${isActiveSort ? sortArrow : '↕'}</span>` : ''}${tip(column.help)}</span><span class="col-resizer" data-col-index="${index}" title="拖动调整列宽" aria-hidden="true"></span></th>`;
      }).join('')}</tr>`;
      attachColumnResizers();
      attachHeaderSorters();
    }
    function renderRows() {
      const tbody = document.getElementById('rows');
      tbody.innerHTML = '';
      if (activeSection === 'settings') return;
      const page = pagedRows();
      if (!page.rows.length) {
        tbody.innerHTML = `<tr><td colspan="${columns().length}"><div class="empty">这个筛选条件下没有数据。</div></td></tr>`;
        return;
      }
      page.rows.forEach((item, idx) => {
        const tr = document.createElement('tr');
        tr.innerHTML = columns().map((column, columnIndex) => `<td class="${column.cls || ''}"${columnWidthStyle(columnIndex)}>${renderCell(item, column, page.start + idx)}</td>`).join('');
        tbody.appendChild(tr);
      });
    }
    function renderPager() {
      const pager = document.getElementById('pager');
      if (activeSection === 'settings') {
        pager.hidden = true;
        pager.innerHTML = '';
        return;
      }
      pager.hidden = false;
      const state = currentState();
      const page = pagedRows();
      const from = page.total ? page.start + 1 : 0;
      const to = Math.min(page.start + state.pageSize, page.total);
      const sizeButtons = pageSizes.map(size => `<button type="button" class="control-button ${state.pageSize === size ? 'active' : ''}" data-page-size="${size}">${size}/页</button>`).join('');
      pager.innerHTML = `<div class="pager-left"><span>显示 ${from}-${to} / ${page.total} 行</span><span class="pager-size"><span>每页</span>${sizeButtons}</span></div><div class="pager-actions"><button type="button" class="control-button" data-page="first" ${state.page <= 1 ? 'disabled' : ''}>第一页</button><button type="button" class="control-button" data-page="prev" ${state.page <= 1 ? 'disabled' : ''}>上一页</button><span>第 ${state.page} / ${page.totalPages} 页</span><button type="button" class="control-button" data-page="next" ${state.page >= page.totalPages ? 'disabled' : ''}>下一页</button><button type="button" class="control-button" data-page="last" ${state.page >= page.totalPages ? 'disabled' : ''}>最后页</button></div>`;
      pager.querySelectorAll('button').forEach(btn => {
        btn.onclick = () => {
          if (btn.disabled) return;
          if (btn.dataset.pageSize) {
            state.pageSize = Number(btn.dataset.pageSize);
            state.page = 1;
          }
          if (btn.dataset.page === 'first') state.page = 1;
          if (btn.dataset.page === 'prev') state.page = Math.max(1, state.page - 1);
          if (btn.dataset.page === 'next') state.page = Math.min(page.totalPages, state.page + 1);
          if (btn.dataset.page === 'last') state.page = page.totalPages;
          render();
        };
      });
    }
    function renderMainVisibility() {
      const tableWrap = document.getElementById('tableWrap');
      document.body.classList.toggle('settings-mode', activeSection === 'settings');
      tableWrap.hidden = activeSection === 'settings';
    }
    function render() { ensureActiveChannel(); renderWorkspaceTabs(); renderSettingsSubnav(); renderChannelTabs(); renderCards(); renderControls(); renderStatus(); renderSettingsPanel(); renderMainVisibility(); renderHead(); renderRows(); renderPager(); }
    render();
    if (apiWritable) reloadConfigFromApi(true);
  </script>
</body>
</html>
"""
    html_text = (
        html_template.replace("__RUN_ID__", html.escape(run_id))
        .replace("__FETCHED_AT__", html.escape(fetched_at))
        .replace("__DATA_JSON__", data_json)
    )
    (EXPORT_DIR / "dashboard.html").write_text(html_text)


def fmt(value: Any) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.2f}"
    except Exception:  # noqa: BLE001
        return str(value)


def pipeline_adapters() -> list[tuple[str, Any]]:
    return [
        ("github_trending", collect_github_trending),
        ("github_movers", collect_github_movers),
        ("github_search", collect_github_search),
        ("hn_algolia", collect_hn_algolia),
        ("hn_firebase", collect_hn_firebase),
        ("product_hunt", collect_product_hunt),
        ("huggingface_trending", collect_huggingface),
        ("npm_search", collect_npm_search),
        ("pypi_feeds", collect_pypi_feeds),
        ("x_seed_accounts", collect_x_seed_accounts),
        ("x_tweets", collect_x_tweets),
        ("apify_configured", collect_apify_configured),
        ("ossinsight_trending_optional", collect_ossinsight_optional),
    ]


def run_pipeline(only_sources: set[str] | None = None, *, export_only: bool = False) -> int:
    load_dotenv()
    ensure_dirs()
    config = read_config()
    fetched_at = iso(utc_now())
    run_id = fetched_at.replace(":", "").replace("-", "")
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    if export_only:
        scored = rank_latest_by_item_source(conn, run_id)
        export_latest(scored, run_id, fetched_at, latest_source_errors(conn))
        print(f"Exported {len(scored)} scored rows from latest snapshots.", file=sys.stderr)
        print(EXPORT_DIR / "latest_scores.md")
        return 0

    adapters = pipeline_adapters()
    if only_sources:
        known = {name for name, _ in adapters}
        unknown = sorted(only_sources - known)
        if unknown:
            print(f"Unknown source(s): {', '.join(unknown)}", file=sys.stderr)
            print(f"Known sources: {', '.join(sorted(known))}", file=sys.stderr)
            return 2
        adapters = [(name, adapter) for name, adapter in adapters if name in only_sources]

    source_errors: dict[str, str | None] = {}
    total_inserted = 0
    for source_name, adapter in adapters:
        print(f"[{source_name}] collecting...", file=sys.stderr)
        try:
            items, error = adapter(config, fetched_at)
        except Exception as exc:  # noqa: BLE001
            items = []
            error = f"{type(exc).__name__}: {exc}"
        source_errors[source_name] = error
        ids = insert_source_items(conn, run_id=run_id, source=source_name, fetched_at=fetched_at, items=items, error=error)
        total_inserted += len(ids)
        print(f"[{source_name}] inserted {len(ids)} items" + (f" ({error})" if error else ""), file=sys.stderr)

    if only_sources:
        scored = rank_latest_by_item_source(conn, run_id)
        export_errors = latest_source_errors(conn)
    else:
        scored = rank_score(conn, run_id)
        export_errors = source_errors
    export_latest(scored, run_id, fetched_at, export_errors)
    print(f"Inserted {total_inserted} items. Exported {len(scored)} scored rows.", file=sys.stderr)
    print(EXPORT_DIR / "latest_scores.md")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_PATH), help="Reserved for future use; database path is currently fixed.")
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="Collect only named adapter(s), comma-separated or repeatable. Dashboard is exported from latest snapshot per item source.",
    )
    parser.add_argument("--export-only", action="store_true", help="Do not collect; export dashboard from latest snapshot per item source.")
    args = parser.parse_args()
    only: set[str] = set()
    for value in args.only:
        only.update(part.strip() for part in value.split(",") if part.strip())
    return run_pipeline(only or None, export_only=args.export_only)


if __name__ == "__main__":
    raise SystemExit(main())
