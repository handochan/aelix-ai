"""#719 on Windows: the delegation's Job is what stops a delegated child's detached helper (r2 §3.6, ADR-0296).

No launch ledger is created on win32: the print channel attaches the child to a
Job with ``kill_on_close=True`` and no ``BREAKAWAY_OK``, so every process the
child starts — a detached helper included — is in it, and closing the Job at
the run's end (its cleanup task, ``print_channel._close_tree``) ends them.
#220's comment is the only basis for that; this is the first measurement, and
it is windows-latest's to make (I until CI runs it).

A stub child, not a real aelix: it starts ``python -c "sleep"`` with
``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`` — ``start /b``'s shape without
a shell — announces its pid, then either ends (door ``end``) or waits to be
stopped (door ``stop``).
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path

import aelix_agents.print_channel as pc
import pytest
from aelix_agents.print_channel import RunningChild

from tests.agents_ext.test_print_channel_spawn import (
    _alive,
    _announced_pid,
    _plan,
    _stub,
    _stub_channel,
)
from tests.event_waits import wait_until

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the delegation's Job is win32's")


def _detaching_stub(marker: Path, *, stay: bool) -> str:
    wait = "time.sleep(60)" if stay else "pass"
    return _stub(
        f"""
        start()
        helper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(300)"],
            creationflags=0x00000008 | 0x00000200,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        announce({str(marker)!r}, helper.pid)
        say("left a helper")
        {wait}
        done()
        """
    )


@pytest.mark.parametrize("door", ["end", "stop"])
async def test_a_delegated_childs_detached_helper_is_gone_after_the_run(tmp_path: Path, door: str) -> None:
    """I (windows-latest): the helper is gone after a normal end and after a stop; no ledger file exists."""

    marker = tmp_path / "helper.pid"
    channel = _stub_channel(_detaching_stub(marker, stay=door == "stop"), grace=1.0)
    row = RunningChild(id=f"sub-win-{door}", profile="scout")
    run = asyncio.ensure_future(channel.run(_plan(tmp_path), child=row))
    helper: int | None = None
    try:
        await wait_until(lambda: _announced_pid(marker) is not None, timeout=60, what="the stub's helper")
        helper = _announced_pid(marker)
        assert helper is not None and _alive(helper)
        assert row.tracking is None, "no launch ledger on win32"
        if door == "stop":
            await asyncio.wait_for(pc.abort_child(row, grace=1.0), 30)
        result = await asyncio.wait_for(run, 60)
        assert result.status in ("ok", "aborted"), result
        await pc.join_cleanup(row.cleanup)
        await wait_until(lambda: not _alive(helper), timeout=10, what=f"the detached helper {helper} to end")
    finally:
        run.cancel()
        with contextlib.suppress(BaseException):
            await run
        if helper is not None and _alive(helper):
            import subprocess

            subprocess.run(["taskkill", "/F", "/PID", str(helper)], capture_output=True, check=False)  # noqa: S603, S607
