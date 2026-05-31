from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from pipeline.run_pipeline import (
    CHANNEL_ORDER,
    SETTINGS_CHANNEL_ORDER,
    SOURCE_DASHBOARD_HIDDEN_CHANNELS,
    api_status_payload,
    channel_description,
    channel_label,
    latest_source_errors,
    rank_latest_by_item_source,
    settings_rows_from_config,
)


def latest_run_meta(conn: sqlite3.Connection) -> dict[str, str]:
    row = conn.execute(
        """
        select run_id, fetched_at
        from snapshots
        where status = 'ok'
        order by id desc
        limit 1
        """
    ).fetchone()
    if not row:
        return {"run_id": "", "fetched_at": ""}
    return {"run_id": row[0], "fetched_at": row[1]}


def build_dashboard_data(*, db_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        meta = latest_run_meta(conn)
        scored = rank_latest_by_item_source(conn, meta["run_id"]) if meta["run_id"] else []
        source_errors = latest_source_errors(conn) if meta["run_id"] else {}
        display_rows = scored + settings_rows_from_config(config, source_errors, meta["fetched_at"])

        channel_counts: dict[str, int] = {}
        window_counts: dict[str, int] = {}
        for row in display_rows:
            channel = str(row["channel"])
            window = str(row.get("window") or "current")
            channel_counts[channel] = channel_counts.get(channel, 0) + 1
            window_counts[window] = window_counts.get(window, 0) + 1

        channels = [
            {
                "id": channel,
                "label": channel_label(channel),
                "count": channel_counts.get(channel, 0),
                "description": channel_description(channel),
            }
            for channel in CHANNEL_ORDER
            if channel_counts.get(channel, 0) and channel not in SOURCE_DASHBOARD_HIDDEN_CHANNELS
        ]
        settings_channels = [
            {
                "id": channel,
                "label": channel_label(channel),
                "count": channel_counts.get(channel, 0),
                "description": channel_description(channel),
            }
            for channel in SETTINGS_CHANNEL_ORDER
            if channel_counts.get(channel, 0)
        ]
        return {
            "run_id": meta["run_id"],
            "fetched_at": meta["fetched_at"],
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
    finally:
        conn.close()
