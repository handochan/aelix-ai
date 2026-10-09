"""Event-gated #428 probe, run in a child so a leaked worker cannot hang pytest."""

from __future__ import annotations

import asyncio
import contextlib
import json
import queue
import sys
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from aelix_coding_agent.tui import completion as completion_mod
from aelix_coding_agent.tui.shell import _build_input_completer
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document


class _GatedExecutor(ThreadPoolExecutor):
    """Hold the producer before or after its Future becomes running.

    The returned Future follows the normal executor cancellation contract. The
    separate worker carrying it waits at an event, replacing a scheduler delay
    with an explicit barrier. A queue consumer, if submitted, runs immediately.
    No prompt-toolkit or CPython worker implementation is patched.
    """

    def __init__(self, stage: str) -> None:
        super().__init__(max_workers=2)
        self.stage = stage
        self.producer_parked = threading.Event()
        self.consumer_started = threading.Event()
        self.release = threading.Event()
        self.producer_futures: list[Future[Any]] = []
        self.jobs: list[str] = []

    def submit(self, fn: Any, /, *args: Any, **kwargs: Any) -> Future[Any]:
        is_consumer = getattr(fn, "__name__", "") == "get" and isinstance(
            getattr(fn, "__self__", None), queue.Queue
        )
        self.jobs.append("consumer" if is_consumer else "producer")
        if is_consumer:

            def consume() -> Any:
                self.consumer_started.set()
                return fn(*args, **kwargs)

            return super().submit(consume)

        future: Future[Any] = Future()
        self.producer_futures.append(future)

        def produce() -> None:
            if self.stage == "running" and not future.set_running_or_notify_cancel():
                return
            self.producer_parked.set()
            assert self.release.wait(5), "completion probe gate was not released"
            if self.stage == "queued" and not future.set_running_or_notify_cancel():
                return
            try:
                future.set_result(fn(*args, **kwargs))
            except BaseException as exc:
                future.set_exception(exc)

        super().submit(produce)
        return future


async def _wait_until(predicate: Any) -> None:
    deadline = asyncio.get_running_loop().time() + 3
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "completion probe missed its gate"
        await asyncio.sleep(0)


async def _run(text: str, cancels: int, stage: str) -> None:
    # The child does not inherit pytest's hermetic tool-bin fixture. Keep its
    # actual FileMentionCompleter on the local walk arm, without reading HOME.
    completion_mod._fd_binary = lambda: None
    executor = _GatedExecutor(stage)
    asyncio.get_running_loop().set_default_executor(executor)
    completer = _build_input_completer(lambda: {}, [], ".")

    async def consume() -> None:
        async for _ in completer.get_completions_async(
            Document(text), CompleteEvent(text_inserted=True)
        ):
            pass

    task = asyncio.create_task(consume())
    try:
        await _wait_until(lambda: executor.producer_parked.is_set() or task.done())
        if "consumer" in executor.jobs:
            await _wait_until(executor.consumer_started.is_set)
        if not task.done():
            task.cancel()
            await asyncio.sleep(0)
            if "consumer" in executor.jobs:
                # The first cancellation entered the generator's finally and
                # is waiting for the gated producer. The next cancel interrupts
                # that await before the producer has started (#428).
                assert not task.done(), "first cancel did not await the producer"
                assert not executor.producer_futures[0].cancelled()
            for _ in range(cancels - 1):
                task.cancel()
                await asyncio.sleep(0)
    finally:
        executor.release.set()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    print(json.dumps({"jobs": executor.jobs}), flush=True)


if __name__ == "__main__":
    asyncio.run(_run(sys.argv[1], int(sys.argv[2]), sys.argv[3]))
    # This marker is reached only after asyncio.run has joined its executor.
    # The parent also requires a natural process exit, covering interpreter join.
    print("executor_shutdown_complete", flush=True)
