#!/usr/bin/env python3
"""Export a sanitized dashboard snapshot for static demo hosting."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import server


SENSITIVE_KEY_PARTS = ("token", "secret", "password", "authorization", "api_key")
LOCAL_PATH_KEYS = {"raw_file", "local_path", "file_path", "path"}


def main() -> None:
    args = parse_args()
    payload = server.query_dashboard_data_payload()
    demo = sanitize_payload(payload, max_items_per_channel_window=args.max_items_per_channel_window)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(demo, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {args.output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a static Hero Radar demo snapshot")
    parser.add_argument("--output", type=Path, default=Path("docs/demo/dashboard-data.json"))
    parser.add_argument("--max-items-per-channel-window", type=int, default=80)
    return parser.parse_args()


def sanitize_payload(payload: dict[str, Any], *, max_items_per_channel_window: int) -> dict[str, Any]:
    keep_item_ids = referenced_item_ids(payload)
    items = prune_items(
        payload.get("items", []),
        keep_item_ids=keep_item_ids,
        max_items_per_channel_window=max_items_per_channel_window,
    )
    demo = dict(payload)
    demo["items"] = items
    demo["channel_counts"] = channel_counts(items)
    demo["window_counts"] = window_counts(items)
    demo["config"] = sanitize_object(payload.get("config", {}))
    demo["source_errors"] = sanitize_object(payload.get("source_errors", {}))
    demo["config_meta"] = sanitize_object(payload.get("config_meta", {}))
    demo["demo_meta"] = {
        "kind": "static_snapshot",
        "max_items_per_channel_window": max_items_per_channel_window,
        "items_kept": len(items),
        "items_original": len(payload.get("items", [])),
    }
    return demo


def prune_items(
    items: list[dict[str, Any]],
    *,
    keep_item_ids: set[int],
    max_items_per_channel_window: int,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        grouped[(str(item.get("channel") or ""), str(item.get("window") or "current"))].append(item)

    kept: list[dict[str, Any]] = []
    kept_ids: set[int] = set()
    for rows in grouped.values():
        ordered = sorted(rows, key=item_rank)
        for row in ordered:
            item_id = int(row.get("item_id") or 0)
            if item_id in keep_item_ids or len([r for r in kept if same_channel_window(r, row)]) < max_items_per_channel_window:
                if item_id not in kept_ids:
                    kept.append(sanitize_item(row))
                    kept_ids.add(item_id)
    return sorted(kept, key=lambda row: (str(row.get("channel") or ""), str(row.get("window") or ""), item_rank(row)))


def item_rank(item: dict[str, Any]) -> tuple[int, int, int]:
    return (
        int(item.get("window_rank") or item.get("rank") or 999999),
        int(item.get("channel_rank") or item.get("source_rank") or 999999),
        int(item.get("item_id") or 0),
    )


def same_channel_window(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        str(left.get("channel") or "") == str(right.get("channel") or "")
        and str(left.get("window") or "current") == str(right.get("window") or "current")
    )


def sanitize_item(item: dict[str, Any]) -> dict[str, Any]:
    cleaned = sanitize_object(item)
    cleaned.pop("raw", None)
    return cleaned


def sanitize_object(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, child in value.items():
            key_text = str(key)
            key_lower = key_text.lower()
            if any(part in key_lower for part in SENSITIVE_KEY_PARTS):
                cleaned[key_text] = "[redacted]"
            elif key_lower in LOCAL_PATH_KEYS:
                continue
            else:
                cleaned[key_text] = sanitize_object(child)
        return cleaned
    if isinstance(value, list):
        return [sanitize_object(item) for item in value]
    return value


def referenced_item_ids(payload: dict[str, Any]) -> set[int]:
    ids: set[int] = set()
    collect_source_link_ids(payload.get("candidates", {}).get("candidates", []), ids)
    collect_source_link_ids(payload.get("candidates", {}).get("edge_watch", []), ids)
    feed = payload.get("feed", {})
    for section in ("today_focus", "scored_list", "diagnostics"):
        for item in feed.get(section, []) or []:
            context = item.get("context") or {}
            collect_source_link_ids(context.get("members", []), ids)
    return ids


def collect_source_link_ids(rows: Any, ids: set[int]) -> None:
    if not isinstance(rows, list):
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        for link in row.get("source_links", []) or []:
            item_id = link.get("item_id") if isinstance(link, dict) else None
            if item_id is not None:
                ids.add(int(item_id))


def channel_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        channel = str(item.get("channel") or "")
        counts[channel] = counts.get(channel, 0) + 1
    return counts


def window_counts(items: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for item in items:
        channel = str(item.get("channel") or "")
        window = str(item.get("window") or "current")
        counts.setdefault(channel, {})[window] = counts.setdefault(channel, {}).get(window, 0) + 1
    return counts


if __name__ == "__main__":
    main()
