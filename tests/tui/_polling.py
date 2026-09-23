"""#315 — the one poll helper the TUI tests share.

A TUI test that asserts something the event loop has to DO first (a key reaching
the key processor, the output pump flushing into a pyte grid, a modal mounting)
can wait for it in two ways. It can sleep a fixed wall-clock interval and bet
that the loop got there — or it can poll for the thing itself. The bet is what
kept going red on the windows-latest leg: #206 (``assert 'A' in []`` after a
0.1 s sleep), #303 (a 2-3 s bound), #315 (a 50 ms flush wait in
``test_render_width_e2e.py``). A 15-minute windows leg starves the loop, and no
number is safe against that.

So the rule the sweep applied, file by file:

* **Wait for the event, not for time.** Poll the observable the assertion is
  about. The bound is an ANTI-HANG bound: the regression it guards against ("it
  never happens") is unbounded, so the number decides nothing and a generous
  one can only remove false failures (#303's argument, ADR-0130's amendment).
* **A bound hit must be diagnosable from a ``-q`` log.** It says what it waited
  for, the bound, the wall clock it actually spent and the poll count — the
  #315 failure ("the streamed paragraph never reached the grid") said none of
  those, so a loaded runner and a rendering regression read the same.
* **A wait that burns more than half its bound and then succeeds warns**, with
  the number, so a leg that is about to flake says so a run before it does.

This was ``test_run_tui_smoke._wait`` (#303). It moved here because five files
had grown private copies — 3 s and 5 s bounds, "condition not met within
timeout", "modal not mounted" — and #303 had already recorded one of them
drifting. ``test_run_tui_smoke`` re-exports it under its old name, so every
``from tests.tui.test_run_tui_smoke import _wait`` keeps working.
"""

from __future__ import annotations

import asyncio
import inspect
import warnings
from collections.abc import Callable

WAIT_CEILING = 10.0
"""Anti-hang bound for :func:`wait_until` — not a gate (see the module note)."""

QUIT_CEILING = 20.0
"""Anti-hang bound for awaiting a ``run_tui`` task after /quit or Ctrl+D (#303)."""

POLL_INTERVAL = 0.005
"""The poll tick. Small enough that the happy path pays a tick or two, where the
fixed sleeps it replaces always paid their full interval."""


def describe(predicate: Callable[[], object]) -> str:
    """Best-effort source text of *predicate*, for the failure line.

    A lambda's source is what makes a windows flake readable in the ``-q`` log.
    """

    try:
        text = " ".join(inspect.getsource(predicate).split())
    except (OSError, TypeError):  # pragma: no cover — no source (exec'd, C, …)
        return repr(predicate)
    return text[:160]


async def wait_until(
    predicate: Callable[[], object],
    *,
    timeout: float = WAIT_CEILING,
    what: str | None = None,
    detail: Callable[[], str] | None = None,
) -> float:
    """Poll until ``predicate()`` is truthy; return the seconds it took.

    ``timeout`` is an anti-hang bound, not a gate. On a bound hit the
    ``AssertionError`` carries *what* (or the predicate's source), the bound,
    the measured wall clock and the poll count, plus ``detail()`` when given —
    the pyte harness passes the grid it last saw, so a missing row and a slow
    runner can be told apart from the log alone.
    """

    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + timeout
    polls = 0
    while True:
        polls += 1
        if predicate():
            waited = loop.time() - started
            if waited > timeout / 2:
                warnings.warn(
                    f"waited {waited:.2f}s of a {timeout:.1f}s bound "
                    f"({polls} polls) for: {what or describe(predicate)}",
                    stacklevel=2,
                )
            return waited
        if loop.time() >= deadline:
            break
        await asyncio.sleep(POLL_INTERVAL)
    message = (
        f"waited {loop.time() - started:.2f}s (bound {timeout:.1f}s, {polls} polls) "
        f"for: {what or describe(predicate)}"
    )
    if detail is not None:
        message += "\n" + detail()
    raise AssertionError(message)


async def quit_within(task: asyncio.Task[int], *, ceiling: float = QUIT_CEILING) -> int:
    """Await a ``run_tui`` task after /quit (or Ctrl+D) under an anti-hang bound.

    #303 wrote this as ``test_run_tui_smoke._quit_within``; #315 moved it here
    with :func:`wait_until` and put it behind the ``asyncio.wait_for(task,
    timeout=5)`` calls the other run_tui files still carried. What it adds:

    * the bound is named and documented as an anti-hang bound, so nobody reads
      5 s as "the shutdown is asserted to be fast" — it never was;
    * a bound hit reports the measured wall clock instead of a bare
      ``TimeoutError``, which in a ``-q`` log is indistinguishable from any
      other timeout in the file.
    """

    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        code = await asyncio.wait_for(task, timeout=ceiling)
    except TimeoutError as exc:  # asyncio.TimeoutError is this, since 3.11
        raise AssertionError(
            f"run_tui did not return within {ceiling:.0f}s of /quit "
            f"(waited {loop.time() - started:.2f}s)"
        ) from exc
    waited = loop.time() - started
    if waited > ceiling / 2:
        warnings.warn(
            f"run_tui took {waited:.2f}s of its {ceiling:.0f}s bound to return after /quit",
            stacklevel=2,
        )
    return code


__all__ = ["POLL_INTERVAL", "QUIT_CEILING", "WAIT_CEILING", "describe", "quit_within", "wait_until"]
