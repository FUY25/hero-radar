from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import TypeVar


InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def stable_bounded_parallel_map(
    values: Iterable[InputT],
    worker: Callable[[InputT], OutputT],
    *,
    concurrency: int,
) -> list[OutputT]:
    """Run independent work concurrently while preserving input order."""

    if concurrency <= 0:
        raise ValueError("concurrency must be positive")
    items = list(values)
    if concurrency == 1 or len(items) <= 1:
        return [worker(item) for item in items]
    with ThreadPoolExecutor(max_workers=min(concurrency, len(items))) as executor:
        futures = [executor.submit(worker, item) for item in items]
        return [future.result() for future in futures]


class RateGate:
    """Bound in-flight calls and space their start times across worker threads."""

    def __init__(
        self,
        *,
        max_in_flight: int,
        min_interval_seconds: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_in_flight <= 0:
            raise ValueError("max_in_flight must be positive")
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must not be negative")
        self._slots = threading.BoundedSemaphore(max_in_flight)
        self._schedule_lock = threading.Lock()
        self._min_interval_seconds = min_interval_seconds
        self._clock = clock
        self._sleeper = sleeper
        self._next_start = 0.0

    @contextmanager
    def slot(self):
        self._slots.acquire()
        try:
            with self._schedule_lock:
                now = self._clock()
                start_at = max(now, self._next_start)
                self._next_start = start_at + self._min_interval_seconds
            wait_seconds = max(0.0, start_at - self._clock())
            if wait_seconds:
                self._sleeper(wait_seconds)
            yield
        finally:
            self._slots.release()

    def run(self, function: Callable[[], OutputT]) -> OutputT:
        with self.slot():
            return function()
