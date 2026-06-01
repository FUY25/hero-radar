from __future__ import annotations

import re
import sqlite3
from typing import Any

from pipeline.decision.schema import to_json, utc_now


SECRET_PATTERNS = [
    re.compile(r"Bearer\s+[A-Za-z0-9._-]+", re.I),
    re.compile(r"sk-[A-Za-z0-9._-]+", re.I),
    re.compile(r"(api[_-]?key\s*[:=]\s*)[A-Za-z0-9._-]+", re.I),
]

SUCCESS_STATUSES = {
    "scheduled",
    "skipped_unchanged",
    "pending_budget",
    "scout_ok",
    "scout_filtered",
    "scoring_ok",
    "deepdive_selected",
    "deepdive_ok",
}


def sanitize_text(value: Any, *, max_chars: int = 800) -> str:
    text = str(value or "")
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(
            lambda match: (
                f"{match.group(1)}[redacted]" if match.lastindex else "[redacted]"
            ),
            text,
        )
    return text[:max_chars]


def sanitized_error(error: BaseException | str | None) -> dict[str, str]:
    if error is None:
        return {"error_type": "", "error": ""}
    if isinstance(error, BaseException):
        return {
            "error_type": type(error).__name__,
            "error": sanitize_text(error),
        }
    return {"error_type": "Error", "error": sanitize_text(error)}


def record_stage_event(
    conn: sqlite3.Connection,
    *,
    feed_run_id: str,
    group_id: str | None,
    stage: str,
    status: str,
    error: BaseException | str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    err = sanitized_error(error)
    conn.execute(
        """
        insert into l2_stage_events(
          feed_run_id, group_id, stage, status, error_type, error, metadata_json, created_at
        )
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            feed_run_id,
            group_id,
            stage,
            status,
            err["error_type"],
            err["error"],
            to_json(metadata or {}),
            utc_now(),
        ),
    )


def stage_summary(conn: sqlite3.Connection, feed_run_id: str) -> dict[str, Any]:
    rows = conn.execute(
        "select stage, status from l2_stage_events where feed_run_id = ?",
        (feed_run_id,),
    ).fetchall()
    stage_counts: dict[str, int] = {}
    error_counts: dict[str, int] = {}
    success_total = 0
    error_total = 0
    for stage, status in rows:
        stage_counts[status] = stage_counts.get(status, 0) + 1
        if str(status).endswith("_error"):
            error_total += 1
            error_counts[stage] = error_counts.get(stage, 0) + 1
        elif status in SUCCESS_STATUSES:
            success_total += 1
    return {
        "stage_counts": stage_counts,
        "error_counts": error_counts,
        "success_total": success_total,
        "error_total": error_total,
    }


def final_run_status(summary: dict[str, Any]) -> str:
    error_total = int(summary.get("error_total") or 0)
    success_total = int(summary.get("success_total") or 0)
    if error_total and success_total:
        return "ok_with_errors"
    if error_total and not success_total:
        return "error"
    return "ok"
