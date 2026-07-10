from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any, TypeVar
from urllib.parse import urlsplit


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


class RequestPolicyRegistry:
    """Run-scoped rate gates keyed by network hostname.

    A hostname is the rate-limit bucket: paths on ``api.github.com`` share one
    gate, while ``github.com`` HTML traffic has a separate gate because it is a
    different host and provider surface. Registering the same host more than
    once reuses its gate. Before the first request, conflicting registrations
    are combined conservatively (lowest concurrency, longest interval).
    """

    def __init__(self, *, gate_factory: Callable[..., Any] = RateGate) -> None:
        self._gate_factory = gate_factory
        self._lock = threading.Lock()
        self._gates: dict[str, Any] = {}
        self._specs: dict[str, tuple[int, float]] = {}
        self._used_hosts: set[str] = set()

    @staticmethod
    def _host(url: str) -> str:
        host = (urlsplit(url).hostname or "").lower()
        if not host:
            raise ValueError(f"request policy URL has no hostname: {url!r}")
        return host

    def register_url(
        self,
        url: str,
        *,
        max_in_flight: int,
        min_interval_seconds: float,
    ) -> Any:
        if max_in_flight <= 0:
            raise ValueError("max_in_flight must be positive")
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must not be negative")
        host = self._host(url)
        requested = (max_in_flight, min_interval_seconds)
        with self._lock:
            existing = self._specs.get(host)
            if existing is None:
                effective = requested
            else:
                effective = (min(existing[0], requested[0]), max(existing[1], requested[1]))
            if existing != effective:
                if host in self._used_hosts:
                    raise ValueError(f"cannot tighten request policy after requests started for host {host}")
                self._specs[host] = effective
                self._gates[host] = self._gate_factory(
                    max_in_flight=effective[0],
                    min_interval_seconds=effective[1],
                )
            return self._gates[host]

    def run_url(
        self,
        url: str,
        function: Callable[[], OutputT],
        *,
        max_in_flight: int,
        min_interval_seconds: float,
    ) -> OutputT:
        gate = self.register_url(
            url,
            max_in_flight=max_in_flight,
            min_interval_seconds=min_interval_seconds,
        )
        host = self._host(url)
        with self._lock:
            self._used_hosts.add(host)
        return gate.run(function)

    def spec_for_url(self, url: str) -> tuple[int, float] | None:
        with self._lock:
            return self._specs.get(self._host(url))
