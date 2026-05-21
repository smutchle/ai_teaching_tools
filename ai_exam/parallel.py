"""Run a list of sync callables in parallel via a private asyncio loop.

The agents are sync. The Anthropic SDK is thread-safe and the network wait
releases the GIL, so `asyncio.to_thread` gives real parallelism for I/O-bound
agent calls without us having to add async sibling methods on every agent.

The Moderator stays sync at its public API. Each fan-out point inside it just
calls `gather_sync([...])`, gets results back in input order, and merges them
into shared state on the main thread (no locks needed because no two threads
ever touch shared state — they only execute the agent call and return).
"""

import asyncio
from typing import Callable, TypeVar

R = TypeVar("R")


def gather_sync(calls: list[Callable[[], R]]) -> list[R]:
    """Run the given zero-arg callables concurrently and return results in order.

    Exceptions propagate from the first failing call after all others complete
    (asyncio.gather's default with return_exceptions=False).
    """
    if not calls:
        return []

    async def _run() -> list[R]:
        return await asyncio.gather(*(asyncio.to_thread(c) for c in calls))

    return asyncio.run(_run())
