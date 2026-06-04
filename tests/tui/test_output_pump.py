"""Sprint 6h₂₄ — flicker fix for ``_output_pump``.

The pump must coalesce CONSECUTIVE ``commit`` items into a single
``print_above_many`` call (one ``in_terminal`` suspend per batch) — that's
the whole flicker fix. A ``tail`` interleaved between commits flushes the
pending commits BEFORE the tail (visible ordering must not change).

We exercise the pump function directly with a fake chrome that records calls,
since the in_terminal suspend is the (untestable) side-effect we're optimizing
away — counting print_above_many invocations is the proxy.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence

import pytest
from aelix_coding_agent.tui.shell import _output_pump


class _FakeChrome:
    """Record print_above[_many] and set_widget calls in order."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def print_above(self, renderable: object) -> None:
        self.calls.append(("print_above", renderable))

    async def print_above_many(self, renderables: Sequence[object]) -> None:
        self.calls.append(("print_above_many", list(renderables)))

    def set_widget(
        self, key: str, lines: list[str] | None, *, above: bool = True  # noqa: ARG002
    ) -> None:
        self.calls.append(("set_widget", (key, lines)))


async def _drain(chrome: _FakeChrome, queue: asyncio.Queue[tuple[str, object]]) -> None:
    # Run the pump until the queue empties + a quiet period elapses; then cancel.
    task = asyncio.create_task(_output_pump(queue, chrome))  # type: ignore[arg-type]
    # Yield enough times for the pump to drain a put_nowait burst. Two settle
    # ticks: the first drains; the second confirms no more arrived. This is
    # finite and deterministic in the asyncio event loop.
    for _ in range(5):
        await asyncio.sleep(0)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_pump_batches_consecutive_commits() -> None:
    chrome = _FakeChrome()
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
    queue.put_nowait(("commit", "A"))
    queue.put_nowait(("commit", "B"))
    queue.put_nowait(("commit", "C"))

    await _drain(chrome, queue)

    # ONE print_above_many for the whole burst (the flicker fix).
    batches = [c for c in chrome.calls if c[0] == "print_above_many"]
    assert len(batches) == 1
    assert batches[0][1] == ["A", "B", "C"]
    # And NO per-item print_above fell through.
    assert not any(c[0] == "print_above" for c in chrome.calls)


@pytest.mark.asyncio
async def test_pump_tail_flushes_pending_commits_first() -> None:
    chrome = _FakeChrome()
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
    queue.put_nowait(("commit", "A"))
    queue.put_nowait(("commit", "B"))
    queue.put_nowait(("tail", "stream-tail\n"))
    queue.put_nowait(("commit", "C"))

    await _drain(chrome, queue)

    # Expected order: batch[A,B] → set_widget(stream-tail) → batch[C].
    kinds = [c[0] for c in chrome.calls]
    assert kinds == ["print_above_many", "set_widget", "print_above_many"]
    assert chrome.calls[0][1] == ["A", "B"]
    assert chrome.calls[1][1] == ("__stream__", ["stream-tail", ""])
    assert chrome.calls[2][1] == ["C"]


@pytest.mark.asyncio
async def test_pump_empty_tail_clears_widget() -> None:
    chrome = _FakeChrome()
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
    queue.put_nowait(("tail", ""))

    await _drain(chrome, queue)

    assert chrome.calls == [("set_widget", ("__stream__", None))]


@pytest.mark.asyncio
async def test_pump_single_commit_uses_batch_path() -> None:
    # Even a single commit goes through print_above_many — consistency keeps
    # the pump's "one suspend per drain" guarantee uniform.
    chrome = _FakeChrome()
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
    queue.put_nowait(("commit", "solo"))

    await _drain(chrome, queue)

    assert chrome.calls == [("print_above_many", ["solo"])]
