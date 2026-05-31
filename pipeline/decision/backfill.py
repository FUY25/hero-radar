from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from pipeline.decision.cache import (
    api_cache_key,
    get_api_cache,
    put_api_cache,
    stable_hash,
)
from pipeline.decision.rules import iso_time, parse_time
from pipeline.decision.schema import utc_now


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT / "data" / "hero_radar.sqlite"
MAX_STARGAZER_PAGES = 5
GITHUB_PAGE_LIMIT = 400


class GitHubClient:
    def __init__(self, *, token: str | None = None, timeout: int = 20) -> None:
        self.token = token
        self.timeout = timeout
        self.last_lower_bound = False

    def request_json(self, url: str, *, accept: str = "application/vnd.github+json") -> Any:
        headers = {
            "Accept": accept,
            "User-Agent": "hero-radar-decision-backfill",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def repo_metadata(self, full_name: str) -> dict[str, Any]:
        owner_repo = urllib.parse.quote(full_name, safe="/")
        return self.request_json(f"https://api.github.com/repos/{owner_repo}")

    def stargazers_since(self, full_name: str, since_iso: str) -> list[dict[str, Any]]:
        metadata = self.repo_metadata(full_name)
        total = int(metadata.get("stargazers_count") or 0)
        per_page = 100
        last_page = max(1, math.ceil(total / per_page))
        self.last_lower_bound = last_page > GITHUB_PAGE_LIMIT
        start_page = min(last_page, GITHUB_PAGE_LIMIT)
        stop_page = max(1, start_page - MAX_STARGAZER_PAGES + 1)
        since_dt = parse_time(since_iso)
        owner_repo = urllib.parse.quote(full_name, safe="/")
        stars: list[dict[str, Any]] = []
        for page in range(start_page, stop_page - 1, -1):
            url = (
                f"https://api.github.com/repos/{owner_repo}/stargazers"
                f"?per_page={per_page}&page={page}"
            )
            page_rows = self.request_json(
                url,
                accept="application/vnd.github.star+json",
            )
            if not isinstance(page_rows, list):
                continue
            stars.extend(page_rows)
            if since_dt and any(
                (starred := parse_time(row.get("starred_at"))) and starred < since_dt
                for row in page_rows
            ):
                break
        return stars


def repo_from_canonical_key(canonical_key: str) -> str | None:
    if not canonical_key.startswith("github:"):
        return None
    return canonical_key.split(":", 1)[1]


def count_stars_between(stargazers: list[dict[str, Any]], start: dt.datetime, end: dt.datetime) -> int:
    count = 0
    for row in stargazers:
        starred = parse_time(row.get("starred_at"))
        if starred and start <= starred <= end:
            count += 1
    return count


def insert_backfill_evidence(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    canonical_entity: str,
    metric_name: str,
    metric_value: float | int,
    historical_safety: str,
    note: str,
    run_id: str,
    now: str,
    raw_url_or_ref: str,
) -> None:
    conn.execute(
        """
        insert into evidence_rows(entity_id, canonical_entity, alias, source, event_at, relative_to_reference, metric_name, metric_value, family, rule_id, rule_version, signal_label, historical_safety, note, raw_url_or_ref, run_id)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity_id,
            canonical_entity,
            canonical_entity,
            "github_backfill",
            now,
            None,
            metric_name,
            str(int(metric_value)) if float(metric_value).is_integer() else str(metric_value),
            "github",
            f"github_backfill_{metric_name}",
            "rules-v1",
            "backfill",
            historical_safety,
            note,
            raw_url_or_ref,
            run_id,
        ),
    )


def run_backfill_jobs(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    github_client: Any,
    now: str,
) -> dict[str, Any]:
    now_dt = parse_time(now) or dt.datetime.now(dt.timezone.utc)
    since_7d = iso_time(now_dt - dt.timedelta(days=7))
    since_24h = now_dt - dt.timedelta(hours=24)
    jobs = conn.execute(
        """
        select bj.id, bj.entity_id, e.canonical_entity, e.canonical_key, bj.source, bj.reason
        from backfill_jobs bj
        join entities e on e.entity_id = bj.entity_id
        where bj.run_id = ? and bj.status = 'pending'
        order by bj.id
        """,
        (run_id,),
    ).fetchall()
    completed = 0
    failed = 0
    signals: dict[str, dict[str, float]] = {}

    for job_id, entity_id, canonical_entity, canonical_key, source, reason in jobs:
        if source != "github_stargazers":
            continue
        full_name = repo_from_canonical_key(canonical_key)
        if not full_name:
            continue
        try:
            input_hash = stable_hash(
                {
                    "full_name": full_name,
                    "since": since_7d,
                    "reason": reason,
                }
            )
            cache_key = api_cache_key(
                source="github_stargazers",
                external_id=full_name,
                window="7d",
                input_hash=input_hash,
            )
            cached = get_api_cache(conn, cache_key)
            if cached is None:
                metadata = github_client.repo_metadata(full_name)
                stargazers = github_client.stargazers_since(full_name, since_7d)
                cached = {
                    "metadata": metadata,
                    "stargazers": stargazers,
                    "lower_bound": bool(getattr(github_client, "last_lower_bound", False)),
                }
                put_api_cache(
                    conn,
                    cache_key=cache_key,
                    source="github_stargazers",
                    external_id=full_name,
                    window="7d",
                    input_hash=input_hash,
                    response=cached,
                )

            stargazers = cached.get("stargazers") or []
            metadata = cached.get("metadata") or {}
            stars_24h = count_stars_between(stargazers, since_24h, now_dt)
            stars_7d = count_stars_between(stargazers, now_dt - dt.timedelta(days=7), now_dt)
            forks_total = int(metadata.get("forks_count") or 0)
            bound_note = "lower_bound_due_to_page_cap" if cached.get("lower_bound") else "complete_recent_pages"
            insert_backfill_evidence(
                conn,
                entity_id=entity_id,
                canonical_entity=canonical_entity,
                metric_name="github_stars_24h",
                metric_value=stars_24h,
                historical_safety="as_of_safe",
                note=bound_note,
                run_id=run_id,
                now=now,
                raw_url_or_ref=cache_key,
            )
            insert_backfill_evidence(
                conn,
                entity_id=entity_id,
                canonical_entity=canonical_entity,
                metric_name="github_stars_7d",
                metric_value=stars_7d,
                historical_safety="as_of_safe",
                note=bound_note,
                run_id=run_id,
                now=now,
                raw_url_or_ref=cache_key,
            )
            insert_backfill_evidence(
                conn,
                entity_id=entity_id,
                canonical_entity=canonical_entity,
                metric_name="github_forks_total",
                metric_value=forks_total,
                historical_safety="partial_as_of",
                note="repo metadata snapshot",
                run_id=run_id,
                now=now,
                raw_url_or_ref=cache_key,
            )
            conn.execute(
                "update backfill_jobs set status = 'completed', completed_at = ?, result_ref = ? where id = ?",
                (now, cache_key, job_id),
            )
            signals[entity_id] = {
                "stars_24h": float(stars_24h),
                "stars_7d": float(stars_7d),
            }
            completed += 1
            conn.commit()
        except Exception as exc:
            failed += 1
            conn.execute(
                "update backfill_jobs set status = 'failed', completed_at = ?, result_ref = ? where id = ?",
                (now, str(exc)[:500], job_id),
            )
            conn.commit()

    return {"completed": completed, "failed": failed, "signals": signals}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run pending decision backfill jobs")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--now", default=utc_now())
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        summary = run_backfill_jobs(
            conn,
            run_id=args.run_id,
            github_client=GitHubClient(token=os.environ.get("GITHUB_TOKEN")),
            now=args.now,
        )
    finally:
        conn.close()
    print(f"completed: {summary['completed']}")
    print(f"failed: {summary['failed']}")


if __name__ == "__main__":
    main()
