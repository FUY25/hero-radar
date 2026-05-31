from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar


InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def bounded_parallel_map(
    values: Iterable[InputT],
    worker: Callable[[InputT], OutputT],
    *,
    concurrency: int,
) -> list[OutputT]:
    if concurrency <= 0:
        raise ValueError("concurrency must be positive")
    items = list(values)
    if concurrency == 1 or len(items) <= 1:
        return [worker(item) for item in items]
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(worker, item) for item in items]
        return [future.result() for future in futures]
