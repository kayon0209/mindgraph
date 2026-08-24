from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from concurrent.futures import Executor
from concurrent.futures import TimeoutError as FutureTimeoutError
import threading
from typing import Any


async def iter_sync_events(
    factory: Callable[[], Iterator[dict[str, Any]]],
    *,
    executor: Executor | None = None,
    queue_size: int = 16,
):
    """Expose a blocking event iterator as a back-pressured async iterator."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=queue_size)
    stopped = threading.Event()

    def put(kind: str, value: Any) -> bool:
        future = asyncio.run_coroutine_threadsafe(queue.put((kind, value)), loop)
        while not stopped.is_set():
            try:
                future.result(timeout=0.1)
                return True
            except FutureTimeoutError:
                continue
        future.cancel()
        return False

    def produce() -> None:
        try:
            for item in factory():
                if stopped.is_set() or not put("item", item):
                    return
        except BaseException as exc:
            put("error", exc)
        else:
            put("done", None)

    producer = loop.run_in_executor(executor, produce)
    try:
        while True:
            kind, value = await queue.get()
            if kind == "item":
                yield value
            elif kind == "error":
                raise value
            else:
                return
    finally:
        stopped.set()
        if producer.done():
            producer.result()
