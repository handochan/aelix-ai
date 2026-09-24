"""#330 — the waits ``tests/`` shares: poll for an event, bound a hang, record a bound.

The windows-latest legs kept going red on tests that let the wall clock DECIDE a
verdict: a floor with no margin (``elapsed >= grace`` read 0.187 s for 0.2 s on a
15.625 ms clock, #313), a fixed sleep betting that the loop got somewhere (#315),
a tight ceiling on a loaded runner. The rule the sweeps applied, and that these
helpers make cheap to follow:

* **Assert the event, not the time.** Poll the thing the assertion is about
  (:func:`wait_until`), or ask the product what it did — which bound it armed a
  wait with and how that wait ended (:func:`record_armed_waits`). A number the
  product computed is not a race against the runner.
* **A bound you keep is an ANTI-HANG bound.** The regression it guards against
  ("it never happens") is unbounded, so the number decides nothing and a
  generous one can only remove false failures. On a hit it says what it waited
  for, the bound and the wall clock it actually spent (:func:`within`,
  :func:`check_anti_hang`) — so a ``-q`` CI log alone tells a loaded runner from
  a regression. ADR-0225's rule: a failure explains itself.
* **A wait that burns more than half its bound and then succeeds warns**, with
  the number, because the windows log under ``-q`` carries warnings and little
  else — a leg that is about to flake says so a run before it does.

:func:`wait_until` and :func:`describe` were ``tests/tui/_polling.py``'s (#315),
which re-exports them so no ``tests/tui`` importer changed. They moved here
because the #330 sweep needed them outside ``tests/tui`` and the files it touched
had grown more private copies ("the child never started", "condition not met").
The module is NOT named ``_polling``: ``tests/tui`` imports its sibling as the
top-level module ``_polling`` (pytest's prepend import mode puts that directory
on ``sys.path``), and a second top-level ``_polling`` on the ``tests/`` path
could shadow it.
"""

from __future__ import annotations

import asyncio
import inspect
import warnings
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import ModuleType
from typing import Any, TypeVar

import pytest

T = TypeVar("T")

WAIT_CEILING = 10.0
"""Anti-hang bound for :func:`wait_until` — not a gate (see the module note)."""

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
    interval: float = POLL_INTERVAL,
) -> float:
    """Poll until ``predicate()`` is truthy; return the seconds it took.

    ``timeout`` is an anti-hang bound, not a gate. On a bound hit the
    ``AssertionError`` carries *what* (or the predicate's source), the bound,
    the measured wall clock and the poll count, plus ``detail()`` when given —
    the pyte harness passes the grid it last saw, so a missing row and a slow
    runner can be told apart from the log alone.

    ``interval`` is the poll tick. A predicate that costs something (a process
    probe, a directory walk) passes a coarser one; the default is #315's.
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
        await asyncio.sleep(interval)
    message = (
        f"waited {loop.time() - started:.2f}s (bound {timeout:.1f}s, {polls} polls) "
        f"for: {what or describe(predicate)}"
    )
    if detail is not None:
        message += "\n" + detail()
    raise AssertionError(message)


async def within(awaitable: Awaitable[T], *, bound: float, what: str) -> T:
    """Await *awaitable* under an ANTI-HANG bound that says what it waited for.

    ``asyncio.wait_for(x, 30)`` fails as a bare ``TimeoutError``, which in a
    ``-q`` log reads the same as every other timeout in the file. This fails as
    ``"<what> did not finish within <bound>s (waited <measured>s)"``, and warns
    with the number when a success took more than half the bound. The awaitable
    is cancelled on a hit, as ``wait_for`` would.

    ONLY THE BOUND'S OWN EXPIRY is reported as a bound hit. A ``TimeoutError``
    the awaited call raises itself (a product budget firing — the bare-timeout
    shape several callers exist to guard against) propagates unchanged, because
    rewriting it as "did not finish within 20.0s (waited 0.05s)" would be a
    failure that lies about itself (ADR-0225). ``asyncio.timeout``'s
    ``expired()`` is what tells the two apart; ``wait_for`` cannot.
    """

    loop = asyncio.get_running_loop()
    started = loop.time()
    scope = asyncio.timeout(bound)
    try:
        async with scope:
            result = await awaitable
    except TimeoutError as exc:  # asyncio.TimeoutError is this, since 3.11
        if not scope.expired():
            raise
        raise AssertionError(
            f"{what} did not finish within its {bound:.1f}s anti-hang bound "
            f"(waited {loop.time() - started:.2f}s)"
        ) from exc
    waited = loop.time() - started
    if waited > bound / 2:
        warnings.warn(
            f"{what} took {waited:.2f}s of its {bound:.1f}s anti-hang bound",
            stacklevel=2,
        )
    return result


def check_anti_hang(elapsed: float, *, bound: float, what: str) -> None:
    """``assert elapsed <= bound`` as an anti-hang bound: the message and the twin.

    For a wall clock the case measured itself (a synchronous call, or one whose
    watchdog lives elsewhere). The bound decides nothing about the product's
    correctness — the event assertions do — so the message states the bound and
    the measured time, and the warning puts the number in the ``-q`` log when it
    is more than half the bound.
    """

    assert elapsed <= bound, f"{what} took {elapsed:.3f}s, past its {bound:.1f}s anti-hang bound"
    if elapsed > bound / 2:
        warnings.warn(
            f"{what} took {elapsed:.3f}s of its {bound:.1f}s anti-hang bound",
            stacklevel=2,
        )


@dataclass
class ArmedWait:
    """One ``asyncio.wait_for`` / ``asyncio.wait`` the product ARMED, in order.

    ``timeout`` is recorded on entry, as the product passed it; ``outcome`` is
    filled on exit: ``"returned"`` (the awaited thing finished first),
    ``"timed out"`` (the bound fired), ``"cancelled"``, or ``"raised <Type>"``.
    A pending entry says the wait was still running when the case looked.
    """

    kind: str
    timeout: float | None
    outcome: str = "pending"


def armed_since(log: list[ArmedWait], mark: int, kind: str = "wait_for") -> list[ArmedWait]:
    """The *kind* entries appended to *log* after position *mark*."""

    return [w for w in log[mark:] if w.kind == kind]


class _RecordingAsyncio:
    """``asyncio`` for ONE product module, with its bounded waits recorded.

    Everything but ``wait_for`` and ``wait`` (and ``sleep``, when asked) is the
    real module, looked up at call time, so ``asyncio.CancelledError``,
    ``shield``, ``ensure_future`` and the rest behave exactly as they do in
    production. The recorded calls delegate to the real ones: the recording
    adds one coroutine frame and nothing else.
    """

    def __init__(self, log: list[ArmedWait], *, record_sleeps: bool = False) -> None:
        self._log = log
        self.record_sleeps = record_sleeps

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)

    async def wait_for(self, aw: Awaitable[T], timeout: float | None) -> T:
        entry = ArmedWait("wait_for", timeout)
        self._log.append(entry)
        try:
            result = await asyncio.wait_for(aw, timeout)
        except TimeoutError:
            entry.outcome = "timed out"
            raise
        except asyncio.CancelledError:
            entry.outcome = "cancelled"
            raise
        except BaseException as exc:
            entry.outcome = f"raised {type(exc).__name__}"
            raise
        entry.outcome = "returned"
        return result

    async def wait(self, fs: Any, *, timeout: float | None = None, **kwargs: Any) -> Any:
        entry = ArmedWait("wait", timeout)
        self._log.append(entry)
        try:
            done, pending = await asyncio.wait(fs, timeout=timeout, **kwargs)
        except asyncio.CancelledError:
            entry.outcome = "cancelled"
            raise
        entry.outcome = "returned" if done else "timed out"
        return done, pending

    async def sleep(self, delay: float, result: Any = None) -> Any:
        if not self.record_sleeps:
            return await asyncio.sleep(delay, result)
        entry = ArmedWait("sleep", delay)
        self._log.append(entry)
        try:
            value = await asyncio.sleep(delay, result)
        except asyncio.CancelledError:
            entry.outcome = "cancelled"
            raise
        entry.outcome = "returned"
        return value


def record_armed_waits(
    monkeypatch: pytest.MonkeyPatch, module: ModuleType, *, record_sleeps: bool = False
) -> list[ArmedWait]:
    """Record every bounded wait *module* arms through its ``asyncio`` name.

    The event a timing test usually wants is not "how long did it take" but
    "which bound did the product wait under, and did that bound fire". A
    duration read off the runner's clock answers that only on an idle runner;
    this answers it on any runner, and a regression that changes the bound
    (a unit slip, a skipped grace, a grace paid that should not be) changes
    the record exactly. #313's ``FakePopen.wait`` recorded the same thing for
    ``run_contained``.

    Only the module's OWN ``asyncio.wait_for`` / ``asyncio.wait`` calls are
    seen — it swaps the module-global name, not the ``asyncio`` module — so
    the test's own waits and every other module's stay out of the record.
    ``record_sleeps=True`` adds the module's ``asyncio.sleep`` calls as
    ``kind="sleep"`` entries: a poll loop's PACE, which is the product's own
    number for "how soon is a change noticed".

    Called twice for one module in one case, it hands back the SAME log (a
    helper and the case can both ask), turning sleep recording on if either
    asked for it.
    """

    current = getattr(module, "asyncio", None)
    if isinstance(current, _RecordingAsyncio):
        current.record_sleeps = current.record_sleeps or record_sleeps
        return current._log
    assert current is asyncio, (
        f"{module.__name__} does not reach asyncio as a module-global ``asyncio`` "
        "name, so its waits cannot be recorded this way"
    )
    log: list[ArmedWait] = []
    monkeypatch.setattr(module, "asyncio", _RecordingAsyncio(log, record_sleeps=record_sleeps))
    return log


__all__ = [
    "POLL_INTERVAL",
    "WAIT_CEILING",
    "ArmedWait",
    "armed_since",
    "check_anti_hang",
    "describe",
    "record_armed_waits",
    "wait_until",
    "within",
]
