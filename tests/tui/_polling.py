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

This was ``test_run_tui_smoke._wait`` (#303). It moved here (#315) because
five files had grown private copies — 3 s and 5 s bounds, "condition not met
within timeout", "modal not mounted" — and #303 had already recorded one of them
drifting. ``test_run_tui_smoke`` re-exports it under its old name, so every
``from tests.tui.test_run_tui_smoke import _wait`` keeps working. Since #330
:func:`wait_until` and :func:`describe` live in ``tests/event_waits.py``, shared
by all of ``tests/``, and this module re-exports them.
"""

from __future__ import annotations

import asyncio
import warnings

# #330 moved the poll helper to ``tests/event_waits.py`` so the rest of
# ``tests/`` can use it; this module re-exports it under the same names, so no
# ``tests/tui`` importer changed. ``quit_within`` stays here: it is about
# ``run_tui``.
from tests.event_waits import POLL_INTERVAL, WAIT_CEILING, describe, wait_until

QUIT_CEILING = 20.0
"""Anti-hang bound for awaiting a ``run_tui`` task after /quit or Ctrl+D (#303)."""


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
