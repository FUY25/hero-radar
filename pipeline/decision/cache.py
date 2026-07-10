from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from pipeline.decision.schema import utc_now


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def api_cache_key(*, source: str, external_id: str, window: str, input_hash: str) -> str:
    return f"api:{source}:{external_id}:{window}:{input_hash}"


def get_api_cache(conn: sqlite3.Connection, cache_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "select response_json, status from api_cache where cache_key = ?",
        (cache_key,),
    ).fetchone()
    if not row or row[1] != "ok":
        return None
    return json.loads(row[0])


def put_api_cache(
    conn: sqlite3.Connection,
    *,
    cache_key: str,
    source: str,
    external_id: str,
    window: str,
    input_hash: str,
    response: dict[str, Any],
    status: str = "ok",
    error: str | None = None,
    commit: bool = True,
) -> None:
    conn.execute(
        """
        insert into api_cache(cache_key, source, external_id, window, input_hash, response_json, status, fetched_at, expires_at, error)
        values (?, ?, ?, ?, ?, ?, ?, ?, null, ?)
        on conflict(cache_key) do update set
            response_json = excluded.response_json,
            status = excluded.status,
            fetched_at = excluded.fetched_at,
            error = excluded.error
        """,
        (
            cache_key,
            source,
            external_id,
            window,
            input_hash,
            json.dumps(response, ensure_ascii=False, sort_keys=True),
            status,
            utc_now(),
            error,
        ),
    )
    if commit:
        conn.commit()
