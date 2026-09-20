"""#303 — how much event-loop starvation do the TUI smoke ceilings survive?

The Windows leg takes ~15 min and starves the loop; a 2-3 s wall-clock ceiling
in a TUI smoke test flakes there while passing on a re-run. We cannot run that
leg, and spawning CPU burners here would interfere with sibling worktrees, so
the starvation is injected INTO THE TEST'S OWN EVENT LOOP instead — the
technique ADR-0225 used to measure the retry handover ("150 ms/frame",
"400 ms/frame").

A hog co-task blocks the loop for ``block_ms`` out of every ``block_ms+gap_ms``,
so every ``await`` in the test under it pays a stretched wall clock while the
test's own logic is untouched. Sweeping ``block_ms`` finds the duty cycle at
which each ceiling gives out.

    uv run --no-sync pytest -q -s .omc/specs/303-wallclock-margin-probe.py

Reports, per test and per block_ms: PASS/FAIL and the wall clock each ``_wait``
and ``asyncio.wait_for`` actually spent.

MEASURED, this box (macOS, 18 cores), before and after the #303 commit — the
block size at which each test first goes RED:

| test                                                   | before | after |
|--------------------------------------------------------|--------|-------|
| statusline ``test_run_tui_threads_settings_manager_…``  | 2000ms | 6000ms |
| smoke ``…auto_retry_shutdown_cancels_ticker_mid_backoff`` | 3000ms | >8000ms |

Before, both died with ``AssertionError: condition not met within timeout``.
After, the statusline one dies with
``waited 12.04s (bound 10.0s, 6 polls) for: await _wait(lambda: not chrome.is_modal_open())``.

">8000ms" is NOT "survives anything", and the row must not be read that way. The
smoke test's causal reading is bounded by the product's own 10 s backoff: a box
slow enough to sit that backoff out before /quit is processed lets the ticker
reach ``_hand_back_retry_interrupt`` on its own, and a correct build goes red.
Measured directly, correct build, /quit DELAYED after the countdown widget is up
(no hog): 9.5 s -> GREEN, 10.5 s -> RED (handler swapped, "now…" painted). Loop
starvation is the weaker shape of the two — a 12000 ms block across the /quit
window made ``run_tui`` take 12.02 s to return and still came back GREEN,
because the expired ticker timer and the queued /quit become runnable in the
same turn and the teardown gets there first. So the honest claim for this row is
a 10 s margin where there was a 2 s one, not an unconditional one.

NOT reproducible here, and stated so the number is never mistaken for one: the
row-1 ``asyncio.wait_for(task, timeout=2)`` could not be made to fire by loop
starvation at ALL. Blocking the loop for 2.5 / 3 / 4 / 6 s across the /quit
window let the shutdown take that long and ``wait_for`` still returned 0 — the
expired timer cancels the waiter, ``run_tui``'s teardown suppresses the
CancelledError and returns normally, and ``asyncio.timeouts.Timeout.__aexit__``
does not raise when the body returned a value. It fires only when run_tui
genuinely never returns (verified: no /quit sent -> TimeoutError at the bound).
So on windows that ceiling fired because the shutdown really did not complete,
not because one slice of starvation ate the budget.
"""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
import time
from pathlib import Path

import pytest

BLOCK_MS = [0, 2000, 3000, 4000, 6000, 8000]  # ~100 s for the full sweep
GAP_MS = 20


class _Hog:
    """Blocks the event loop block_ms out of every block_ms+GAP_MS."""

    def __init__(self, block_ms: int) -> None:
        self.block_ms = block_ms
        self._task: asyncio.Task[None] | None = None

    async def _run(self) -> None:
        while True:
            time.sleep(self.block_ms / 1000.0)
            await asyncio.sleep(GAP_MS / 1000.0)

    def start(self) -> None:
        if self.block_ms:
            self._task = asyncio.ensure_future(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task


def _instrument(monkeypatch, samples: list[tuple[str, float]]) -> None:
    import tests.tui.test_run_tui_smoke as smoke
    import tests.tui.test_run_tui_statusline_settings as sls

    for mod in (smoke, sls):
        real = mod._wait

        def _timed(predicate, *, timeout: float | None = None, _real=real, **kw):
            async def _run():
                t0 = time.monotonic()
                try:
                    if timeout is None:
                        await _real(predicate, **kw)
                    else:
                        await _real(predicate, timeout=timeout, **kw)
                finally:
                    samples.append((f"_wait(ceiling={timeout})", time.monotonic() - t0))

            return _run()

        monkeypatch.setattr(mod, "_wait", _timed)

    real_wait_for = asyncio.wait_for

    async def _timed_wait_for(aw, timeout=None):
        t0 = time.monotonic()
        try:
            return await real_wait_for(aw, timeout)
        finally:
            samples.append((f"wait_for(ceiling={timeout})", time.monotonic() - t0))

    monkeypatch.setattr(asyncio, "wait_for", _timed_wait_for)


async def _drive(name: str, block_ms: int, monkeypatch) -> None:
    samples: list[tuple[str, float]] = []
    _instrument(monkeypatch, samples)
    hog = _Hog(block_ms)
    hog.start()
    verdict = "PASS"
    err = ""
    t0 = time.monotonic()
    try:
        if name == "statusline":
            from tests.tui.test_run_tui_statusline_settings import (
                test_run_tui_threads_settings_manager_and_statusline as t,
            )

            with tempfile.TemporaryDirectory() as d:
                await t(Path(d))
        else:
            from tests.tui.test_run_tui_smoke import (
                test_run_tui_auto_retry_shutdown_cancels_ticker_mid_backoff as t,
            )

            await t()
    except BaseException as exc:  # noqa: BLE001 — this is the measurement
        verdict = "FAIL"
        err = f"{type(exc).__name__}: {exc}"
    finally:
        total = time.monotonic() - t0
        await hog.stop()
    worst = max(samples, key=lambda s: s[1], default=("-", 0.0))
    print(
        f"\n[{name:11s}] block={block_ms:3d}ms  {verdict}  total={total:6.2f}s  "
        f"worst={worst[1]:6.2f}s {worst[0]}  {err}"
    )
    for label, dt in samples:
        print(f"      {dt:7.3f}s  {label}")


@pytest.mark.parametrize("block_ms", BLOCK_MS)
async def test_statusline_margin(block_ms, monkeypatch):
    await _drive("statusline", block_ms, monkeypatch)


@pytest.mark.parametrize("block_ms", BLOCK_MS)
async def test_smoke_shutdown_margin(block_ms, monkeypatch):
    await _drive("smoke", block_ms, monkeypatch)


# --- the row-1 failure, reproduced exactly ---------------------------------
#
# The sweep above never reaches ``asyncio.wait_for(task, timeout=2)`` because an
# earlier ``_wait`` gives out first. This arm inlines the smoke test's body and
# starts the hog only once the countdown widget is up, so the starvation lands
# squarely in the shutdown window the 2 s ceiling bounds.


@pytest.mark.parametrize("block_ms", [0, 1500, 2500])
async def test_shutdown_ceiling_under_starved_quit(block_ms):
    from aelix_agent_core.types import AutoRetryStartEvent

    from tests.tui.test_run_tui_smoke import _harness_chrome, _launch, _RetryHarness, _wait

    async with _harness_chrome(harness=_RetryHarness()) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running)
        await _wait(lambda: bool(runtime.harness.subscribers))
        subscriber = runtime.harness.subscribers[0]
        subscriber(
            AutoRetryStartEvent(
                attempt=1, max_attempts=3, delay_ms=10_000, error_message="overloaded"
            )
        )
        await _wait(lambda: "__auto_retry__" in chrome._widgets_above)

        hog = _Hog(block_ms)
        hog.start()
        verdict, err = "PASS", ""
        t0 = time.monotonic()
        try:
            pipe.send_text("/quit\n")
            await asyncio.wait_for(task, timeout=2)
        except BaseException as exc:  # noqa: BLE001 — this is the measurement
            verdict, err = "FAIL", f"{type(exc).__name__}: {exc}"
        finally:
            elapsed = time.monotonic() - t0
            await hog.stop()
            if not task.done():
                task.cancel()
        print(f"\n[quit-window] block={block_ms:5d}ms  {verdict}  shutdown={elapsed:6.2f}s  {err}")
