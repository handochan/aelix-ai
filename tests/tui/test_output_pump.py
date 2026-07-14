"""Sprint 6h₂₄ + flicker fix round 3 — ``_output_pump`` batching contract.

The pump must coalesce a drain's ``commit`` items into a single
``print_above_many`` call (one ``in_terminal`` suspend per drain) — that's
the original 6h₂₄ flicker fix. Round 3 tightens the contract:

- commits coalesce ACROSS interleaved tails (a tail is a full-window
  replacement; nothing paints mid-drain, so intermediate tails are dead
  states — only the LAST tail of the drain may apply);
- the last tail is applied INSIDE the batch via ``apply_before_redraw`` so
  the exit repaint shows scrollback + new window in ONE frame;
- a failed flush must still apply the tail (a stale live window must never
  strand), via the pump's fallback path.

We exercise the pump function directly with a fake chrome that records calls,
since the in_terminal suspend is the (untestable) side-effect we're optimizing
away — counting print_above_many invocations is the proxy.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Sequence

import pytest
from aelix_coding_agent.tui.shell import _output_pump


class _FakeChrome:
    """Record print_above[_many] and set_widget calls in order.

    ``print_above_many`` mimics the real chrome: it runs
    ``apply_before_redraw`` AFTER recording the prints (i.e. "inside the
    suspend"), and — when ``raise_on_print`` — raises BEFORE the hook runs,
    like a real mid-batch console failure would.
    """

    def __init__(self, *, raise_on_print: bool = False) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.raise_on_print = raise_on_print

    async def print_above(self, renderable: object) -> None:
        self.calls.append(("print_above", renderable))

    async def print_above_many(
        self,
        renderables: Sequence[object],
        *,
        apply_before_redraw: Callable[[], None] | None = None,
    ) -> None:
        # The 3rd element records whether the fold-in hook was PASSED — that is
        # what pins the round-3 contract. Recording set_widget alone is not
        # enough: a mutant that applies the tail AFTER this call returns
        # produces the identical flat call order and only differs here
        # (hook=False), as mutation testing demonstrated.
        self.calls.append(
            ("print_above_many", list(renderables), apply_before_redraw is not None)
        )
        if self.raise_on_print:
            raise RuntimeError("console failure mid-batch")
        if apply_before_redraw is not None:
            apply_before_redraw()

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
    assert batches[0][2] is False  # no tail in the drain → no fold-in hook
    # And NO per-item print_above fell through.
    assert not any(c[0] == "print_above" for c in chrome.calls)


@pytest.mark.asyncio
async def test_pump_coalesces_commits_across_tails_last_tail_wins() -> None:
    # Round 3: interleaved tails no longer split the commit batch. All commits
    # of the drain flush in ONE suspend (order preserved) and only the LAST
    # tail applies — inside that same suspend (the fake runs the hook after
    # recording the prints, so call order proves the fold-in).
    chrome = _FakeChrome()
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
    queue.put_nowait(("commit", "A"))
    queue.put_nowait(("commit", "B"))
    queue.put_nowait(("tail", "stale-tail\n"))
    queue.put_nowait(("commit", "C"))
    queue.put_nowait(("tail", "live-tail\n"))

    await _drain(chrome, queue)

    assert chrome.calls == [
        ("print_above_many", ["A", "B", "C"], True),
        ("set_widget", ("__stream__", ["live-tail", ""])),
    ]


@pytest.mark.asyncio
async def test_pump_empty_tail_clears_widget() -> None:
    chrome = _FakeChrome()
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
    queue.put_nowait(("tail", ""))

    await _drain(chrome, queue)

    assert chrome.calls == [("set_widget", ("__stream__", None))]


@pytest.mark.asyncio
async def test_pump_tail_only_batch_skips_suspend() -> None:
    # A drain with no commits must not touch print_above_many at all — the
    # per-delta tail repaint is a plain diff render, never a suspend.
    chrome = _FakeChrome()
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
    queue.put_nowait(("tail", "window-line\n"))

    await _drain(chrome, queue)

    assert chrome.calls == [("set_widget", ("__stream__", ["window-line", ""]))]


@pytest.mark.asyncio
async def test_pump_failed_flush_still_applies_tail() -> None:
    # Round 3 fallback: if the batch flush raises before the fold-in hook ran,
    # the pump must still apply the tail (a lost final ``""`` would strand the
    # stale window on screen forever) — and must survive to serve the next
    # drain (the pump never dies).
    chrome = _FakeChrome(raise_on_print=True)
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
    queue.put_nowait(("commit", "A"))
    queue.put_nowait(("tail", ""))

    await _drain(chrome, queue)

    assert chrome.calls == [
        ("print_above_many", ["A"], True),
        ("set_widget", ("__stream__", None)),
    ]

    # Pump survived: a subsequent commit-only drain still flushes.
    chrome.raise_on_print = False
    queue.put_nowait(("commit", "B"))
    await _drain(chrome, queue)
    assert chrome.calls[-1] == ("print_above_many", ["B"], False)


@pytest.mark.asyncio
async def test_pump_single_commit_uses_batch_path() -> None:
    # Even a single commit goes through print_above_many — consistency keeps
    # the pump's "one suspend per drain" guarantee uniform.
    chrome = _FakeChrome()
    queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
    queue.put_nowait(("commit", "solo"))

    await _drain(chrome, queue)

    assert chrome.calls == [("print_above_many", ["solo"], False)]
