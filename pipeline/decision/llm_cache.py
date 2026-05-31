from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from pipeline.decision.schema import utc_now


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def input_hash_for(input_payload: Any) -> str:
    return hashlib.sha256(canonical_json(input_payload).encode("utf-8")).hexdigest()


def cache_key_for(
    *,
    provider: str,
    model: str,
    prompt_version: str,
    task: str,
    input_payload: Any,
) -> str:
    input_hash = input_hash_for(input_payload)
    raw = "|".join([provider, model, prompt_version, task, input_hash])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def store_cached_response(
    conn: sqlite3.Connection,
    *,
    provider: str,
    model: str,
    prompt_version: str,
    task: str,
    input_payload: Any,
    request_payload: Any,
    response_payload: Any,
    status: str,
    error: str | None = None,
    expires_at: str | None = None,
) -> str:
    input_hash = input_hash_for(input_payload)
    key = cache_key_for(
        provider=provider,
        model=model,
        prompt_version=prompt_version,
        task=task,
        input_payload=input_payload,
    )
    conn.execute(
        """
        insert into llm_cache(
            cache_key, provider, model, prompt_version, task, input_hash,
            request_json, response_json, status, created_at, expires_at, error
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(cache_key) do update set
            response_json = excluded.response_json,
            status = excluded.status,
            expires_at = excluded.expires_at,
            error = excluded.error
        """,
        (
            key,
            provider,
            model,
            prompt_version,
            task,
            input_hash,
            canonical_json(request_payload),
            canonical_json(response_payload),
            status,
            utc_now(),
            expires_at,
            error,
        ),
    )
    conn.commit()
    return key


def get_cached_response(conn: sqlite3.Connection, cache_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        select cache_key, provider, model, prompt_version, task, input_hash,
               request_json, response_json, status, created_at, expires_at, error
        from llm_cache
        where cache_key = ?
        """,
        (cache_key,),
    ).fetchone()
    if row is None:
        return None
    return {
        "cache_key": row[0],
        "provider": row[1],
        "model": row[2],
        "prompt_version": row[3],
        "task": row[4],
        "input_hash": row[5],
        "request_json": json.loads(row[6]),
        "response_json": json.loads(row[7]),
        "status": row[8],
        "created_at": row[9],
        "expires_at": row[10],
        "error": row[11],
    }
