from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any


def utc_timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class JsonlRunLogger:
    def __init__(self, path: Path | str | None, *, run_id: str):
        self.path = Path(path) if path else None
        self.run_id = run_id
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def event(self, event: str, **fields: Any) -> None:
        if not self.path:
            return
        payload = {
            "ts": utc_timestamp(),
            "run_id": self.run_id,
            "event": event,
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
