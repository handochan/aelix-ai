"""Per-file asyncio mutation queue (Pi parity ``file-mutation-queue.ts``)."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")

_LOCKS: dict[str, asyncio.Lock] = {}


def _lock_for(path: str) -> asyncio.Lock:
    """Resolve path to canonical abs form and return its lock (lazy-create)."""

    key = os.path.realpath(path)
    lock = _LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[key] = lock
    return lock


async def with_file_mutation_queue(
    path: str, work: Callable[[], Awaitable[T]]
) -> T:
    """Serialize ``work`` against any other mutation on the same file path.

    Pi parity ``withFileMutationQueue`` — guarantees in-process write/edit
    ordering on the same file. Concurrent edits on different files run in
    parallel.
    """

    lock = _lock_for(path)
    async with lock:
        return await work()


__all__ = ["with_file_mutation_queue"]
