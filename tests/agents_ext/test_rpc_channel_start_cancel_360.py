"""#360 at the delegation: a spawn cancelled inside the startup grace leaves no process.

``RpcChannel.run`` publishes the child on its registry row only once
``RpcClient.start`` has returned, so while ``start`` waits out its grace the
row's ``proc`` is ``None`` and ``stop`` / ``stop_all`` / session teardown have
nothing to reach. A cancel landing there — Esc, a batch's cancel, a loop torn
down at exit — used to propagate out of ``run`` with the child running and the
row already popped. Measured on the base, macOS and Linux, with a real
``-m aelix_coding_agent --mode rpc`` child: alive after the ``CancelledError``
with ``runtime.list() == []`` and alive when ``stop_all`` returned. (That real
child then exited 1 on its own, 0.41-0.46 s later on macOS and 0.44-0.63 s on
Linux, because ``run``'s ``finally`` had removed the prompt file it had not
read yet.
The stubs here have no prompt file to lose, and the tree stub never reads its
stdin: that shape outlived the parent with PPID 1.)

The fix is in ``start`` itself (``tests/rpc/test_rpc_client_start_cancel_360.py``
pins it there); these pin that the delegation path inherits it, through the
real runtime.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from pathlib import Path
from typing import Any

import pytest
from aelix_agents.rpc_channel import RpcChannel
from aelix_agents.runtime import SubagentHost, _SubagentRuntimeImpl
from aelix_coding_agent.rpc.rpc_client import RpcClient, RpcClientOptions

from tests.agents_ext.test_rpc_channel import _grant, _resolved, _rpc_stub
from tests.event_waits import wait_until, within
from tests.process_probe import STATE_GONE, STATE_ZOMBIE, await_dead_or_zombie
from tests.rpc.test_rpc_client_start_cancel_360 import _TREE_CHILD, _pids

_DEAD = (STATE_GONE, STATE_ZOMBIE)


def _really_kill(pid: int) -> None:
    with contextlib.suppress(OSError):
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))


async def test_a_delegation_cancelled_inside_the_grace_leaves_neither_the_child_nor_its_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The grace made long, so the cancel can only land in it. Red on the base."""

    class _LongGrace(RpcClient):
        STARTUP_GRACE_MS = 600_000

    monkeypatch.setattr("aelix_agents.rpc_channel.RpcClient", _LongGrace)
    pid_file = tmp_path / "tree.pids"

    def _argv(*_args: Any, **_kwargs: Any) -> list[str]:
        return [sys.executable, "-c", _TREE_CHILD, str(pid_file)]

    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path)),
        channel=RpcChannel(grace=0.3, argv_builder=_argv),
    )
    task = asyncio.ensure_future(
        runtime.spawn_granted(_grant(), _resolved(), "be cancelled in the grace")
    )
    pids: list[int] = []
    try:
        await wait_until(lambda: _pids(pid_file) is not None, what="the child's tree pids")
        pids = _pids(pid_file) or []
        (row,) = runtime._children.values()
        # The precondition that made it an orphan: the row has no process yet.
        assert row.proc is None
        assert not task.done()

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await within(task, bound=20.0, what="the delegation cancelled in the grace")
        assert runtime.list() == []
        await runtime.stop_all()

        states = [await await_dead_or_zombie(pid, timeout=5.0) for pid in pids]
        assert all(state in _DEAD for state in states), (
            f"the cancelled delegation's child and grandchild {pids} outlived it: {states}"
        )
    finally:
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
        for pid in pids:
            _really_kill(pid)


async def test_a_delegation_cancelled_in_the_products_own_grace_leaves_no_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 100 ms grace as shipped, the cancel sent from the exit watcher's first step.

    The stub serves on stdin until EOF, and on the base the abandoned client
    still held that pipe open, so the child idled on for as long as the loop
    ran.
    """

    spawn: dict[str, asyncio.Task[Any]] = {}
    pids: list[int] = []

    class _CancelInTheGrace(RpcClient):
        def __init__(self, options: RpcClientOptions) -> None:
            super().__init__(options)
            self._fired = False

        async def _watch_for_exit(self) -> None:
            if not self._fired:
                self._fired = True
                assert self.process is not None
                pids.append(self.process.pid)
                spawn["task"].cancel()
            await super()._watch_for_exit()

    monkeypatch.setattr("aelix_agents.rpc_channel.RpcClient", _CancelInTheGrace)
    script = _rpc_stub()

    def _argv(*_args: Any, **_kwargs: Any) -> list[str]:
        return [sys.executable, "-c", script]

    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path)),
        channel=RpcChannel(grace=0.3, argv_builder=_argv),
    )
    task = asyncio.ensure_future(
        runtime.spawn_granted(_grant(), _resolved(), "be cancelled in the grace")
    )
    spawn["task"] = task
    try:
        with pytest.raises(asyncio.CancelledError):
            await within(task, bound=20.0, what="the delegation cancelled in the grace")
        assert runtime.list() == []
        (pid,) = pids
        state = await await_dead_or_zombie(pid, timeout=5.0)
        assert state in _DEAD, f"the cancelled delegation's child {pid} outlived it: {state}"
    finally:
        for pid in pids:
            _really_kill(pid)
