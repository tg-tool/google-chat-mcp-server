"""Run per-space API work in parallel.

API clients are not thread-safe (each holds one HTTP connection), so every worker
thread builds its own client from `factory` the first time it needs one.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

WORKERS = 8


def parallel_map[T, R](
    fn: Callable[[Any, T], R],
    items: Iterable[T],
    *,
    chat: Any,
    factory: Callable[[], Any] | None = None,
    workers: int = WORKERS,
) -> list[R]:
    """`fn(client, item)` for every item, results in input order.

    Without `factory` it runs sequentially on `chat`; with one, items run on up to
    `workers` threads, each using a client of its own."""
    items = list(items)
    if factory is None or len(items) <= 1:
        return [fn(chat, item) for item in items]
    local = threading.local()

    def run(item: T) -> R:
        client = getattr(local, "client", None)
        if client is None:
            client = local.client = factory()
        return fn(client, item)

    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        return list(pool.map(run, items))
