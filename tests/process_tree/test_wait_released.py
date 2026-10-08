"""``wait_released``: the grace a teardown ladder waits out ends when the TREE has
let go of the child's pipes, not when the child exits (#192 review round 5).

CPython gh-119710 (3.13.15, 3.14.7) made ``asyncio.subprocess.Process.wait()``
resolve at the child's exit; up to 3.13.14 it resolved only once every pipe was
disconnected - for a waiter registered while the child ran. ``wait_released``
waits for the exit and the pipes on every interpreter, whenever it is called
(stricter than the old ``wait()``, which returned at once once the exit status
was known), and the hook timeout ladder and the delegation reaper spend their
graces on it.

Every case here runs under ``resolve_wait_at_exit`` - the new ``wait()`` - so
the semantics under test are the same on every leg; the first case's opening
assertion is the vacuity guard that the patch is in force (a pipe holder alive,
``wait()`` already resolved).
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path

import pytest
from aelix_ai.utils._process_tree import wait_released

from tests.asyncio_exit_wait import resolve_wait_at_exit
from tests.process_probe import (
    STATE_ALIVE,
    STATE_GONE,
    STATE_ZOMBIE,
    await_dead_or_zombie,
    probe_state,
)

#: Bound on every wait here: a CI runner is slow, and the bound only stops a hang.
DEADLINE = 30.0

#: The child: start a holder of its stdout and/or stderr (``sys.argv[2]``:
#: ``both``, ``stdout`` or ``stderr``; the other goes to ``DEVNULL``. Explicit
#: handles, so Windows passes them too - with all three ``None`` CPython's win32
#: ``_get_handles`` inherits nothing), print the holder's pid, exit 7. The holder
#: lives until the gate file exists, so whether it holds the pipes is decided by
#: the test alone.
_CHILD = r"""
import subprocess, sys
keep = sys.argv[2]
holder = subprocess.Popen(
    [sys.executable, "-c",
     "import os, sys, time\nwhile not os.path.exists(sys.argv[1]): time.sleep(0.02)",
     sys.argv[1]],
    stdin=subprocess.DEVNULL,
    stdout=sys.stdout if keep in ("both", "stdout") else subprocess.DEVNULL,
    stderr=sys.stderr if keep in ("both", "stderr") else subprocess.DEVNULL,
)
print(holder.pid, flush=True)
sys.exit(7)
"""


@pytest.mark.parametrize("pipes", ["both", "stdout", "stderr"])
async def test_it_resolves_when_the_last_pipe_holder_lets_go_not_at_the_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pipes: str
) -> None:
    """``stdout`` / ``stderr`` (review round 6): a holder of ONE pipe holds the
    grace too - a poll of only the other pipe would resolve at the exit."""

    resolve_wait_at_exit(monkeypatch)
    gate = tmp_path / "gate"
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _CHILD,
        str(gate),
        pipes,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdout is not None
    holder = int((await asyncio.wait_for(proc.stdout.readline(), DEADLINE)).strip())
    released: asyncio.Future[int] | None = None
    try:
        # Vacuity guard: the child is gone and wait() said so while the holder
        # still has the pipes - gh-119710's wait(), whatever this interpreter is.
        assert await asyncio.wait_for(proc.wait(), DEADLINE) == 7
        assert probe_state(holder) == STATE_ALIVE

        released = asyncio.ensure_future(wait_released(proc))
        # The holder cannot exit before the gate exists, so this is a window the
        # test owns, not a race: 0.5 s is many polls of RELEASE_POLL_SECONDS.
        await asyncio.sleep(0.5)
        assert not released.done(), "wait_released resolved while a pipe was held"

        gate.write_text("", encoding="utf-8")
        assert await asyncio.wait_for(asyncio.shield(released), DEADLINE) == 7
        state = await await_dead_or_zombie(holder, timeout=DEADLINE)
        assert state in (STATE_GONE, STATE_ZOMBIE), state
    finally:
        gate.write_text("", encoding="utf-8")
        if released is not None:
            released.cancel()
            with contextlib.suppress(BaseException):
                await released


async def test_a_child_without_pipes_is_released_at_its_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolve_wait_at_exit(monkeypatch)
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "raise SystemExit(5)",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert await asyncio.wait_for(wait_released(proc), DEADLINE) == 5


async def test_a_process_object_without_a_transport_is_answered_by_its_wait() -> None:
    class _Stub:
        async def wait(self) -> int:
            return 3

    assert await wait_released(_Stub()) == 3
