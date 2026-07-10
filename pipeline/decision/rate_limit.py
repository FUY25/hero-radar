from __future__ import annotations

import threading
import time
from typing import Any


class StartRateLimiter:
    """Thread-safe spacing for outbound call starts.

    Concurrency controls in-flight work; this limiter separately prevents a worker
    pool from releasing all of its requests in one burst.
    """

    def __init__(self, starts_per_second: float) -> None:
        rate = max(0.0, float(starts_per_second or 0.0))
        self._interval = 1.0 / rate if rate > 0 else 0.0
        self._next_start = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        if self._interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            scheduled = max(now, self._next_start)
            self._next_start = scheduled + self._interval
        delay = scheduled - now
        if delay > 0:
            time.sleep(delay)


class RateLimitedClient:
    """Apply one shared start-rate gate to every callable on a client."""

    def __init__(self, client: Any, *, starts_per_second: float) -> None:
        self._client = client
        self._limiter = StartRateLimiter(starts_per_second)

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._client, name)
        if not callable(attribute):
            return attribute

        def limited(*args: Any, **kwargs: Any) -> Any:
            self._limiter.wait()
            return attribute(*args, **kwargs)

        return limited
