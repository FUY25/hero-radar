from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any


MAX_PREFLIGHT_SCAN = 10000


def classifier_preflight_summary(
    conn: sqlite3.Connection,
    *,
    now: str,
    x_limit: int,
    hn_limit: int,
    max_scan: int = MAX_PREFLIGHT_SCAN,
) -> dict[str, Any]:
    x_stats = _x_preflight(conn, now=now, limit=x_limit, max_scan=max_scan)
    hn_stats = _hn_preflight(conn, limit=hn_limit, max_scan=max_scan)
    return {**x_stats, **hn_stats}


def _x_preflight(
    conn: sqlite3.Connection,
    *,
    now: str,
    limit: int,
    max_scan: int,
) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "x_time_basis": "x_tweets_store.created_at",
        "x_items_rows": _count_or_zero(conn, "select count(*) from items where source = 'x_tweets'"),
        "x_items_distinct_tweets": _count_or_zero(
            conn,
            """
            select count(distinct json_extract(metadata_json, '$.tweet_id'))
            from items
            where source = 'x_tweets'
            """,
        ),
        "x_store_rows": _count_or_zero(conn, "select count(*) from x_tweets_store"),
        "x_store_distinct_tweets": _count_or_zero(
            conn,
            "select count(distinct tweet_id) from x_tweets_store",
        ),
        "x_classifier_limit": max(0, int(limit or 0)),
    }
    try:
        from pipeline.decision.x_classifier import candidate_tweets

        candidate_count = len(candidate_tweets(conn, now=now, limit=max_scan))
    except (sqlite3.OperationalError, ValueError):
        candidate_count = 0
    stats["x_classifier_candidates_7d"] = candidate_count
    stats["x_classifier_will_process"] = min(stats["x_classifier_limit"], candidate_count)
    return stats


def _hn_preflight(
    conn: sqlite3.Connection,
    *,
    limit: int,
    max_scan: int,
) -> dict[str, Any]:
    stats = {
        "hn_classifier_limit": max(0, int(limit or 0)),
    }
    try:
        from pipeline.decision.hn_classifier import candidate_hn_units

        unit_count = len(candidate_hn_units(conn, limit=max_scan))
    except sqlite3.OperationalError:
        unit_count = 0
    stats["hn_classifier_units"] = unit_count
    stats["hn_classifier_will_process"] = min(stats["hn_classifier_limit"], unit_count)
    return stats


def _count_or_zero(conn: sqlite3.Connection, sql: str) -> int:
    try:
        row = conn.execute(sql).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0] or 0) if row else 0


def today_utc_iso() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
