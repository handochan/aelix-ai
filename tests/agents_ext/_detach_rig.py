"""Shared rig for the #775 Ctrl+B tests: the gated delegation rig plus the TUI's own guard.

``test_agent_background_611``'s rig (the real extension, job service, harness
and print channel child), told it is on the TUI (``detach_host``), with the
REAL ``JobsUi`` installed as the host — its ``detach_blocked`` is the guard
every commit asks — over a stand-in chrome and runtime host whose harness is
the rig's real one. The key is ``JobsUi.request_detach``, what Ctrl+B calls.

NOTHING HANGS AND NOTHING IS LEFT (#775 fix round 1, verify F9). A stub child
waits for its gate file at most :data:`GATE_SECONDS`, every wait on a call is
bounded (:func:`within`), and :func:`reap_children` — use it with
``pytestmark`` — finishes every rig a test built and kills every child it
spawned that is still alive, whether the test passed or not.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aelix_coding_agent.tui.jobs_ui import JobsUi

from tests.agents_ext.test_agent_background_611 import _rig
from tests.agents_ext.test_print_channel_spawn import _stub
from tests.jobs.conftest import wait_for

GATE_SECONDS = 30
"""How long a stub child waits for its gate before it ends on its own."""

CALL_SECONDS = 15.0
"""How long a test waits for a call to return before it fails."""

_RIGS: list[Any] = []


def script(answer: str = "the background answer", *, gate: Path | None = None, partial: str | None = None) -> str:
    """``test_agent_background_611._script`` with a bounded gate: the child gives up after :data:`GATE_SECONDS`."""

    wait = (
        f"\nend = time.time() + {GATE_SECONDS}\n"
        f"while not os.path.exists({str(gate)!r}) and time.time() < end:\n    time.sleep(0.02)\n"
        if gate is not None
        else ""
    )
    said = f"say({partial!r})\n" if partial is not None else ""
    return _stub(
        "start()\n"
        'emit({"type": "tool_execution_start", "tool_name": "read"})\n'
        + said
        + wait
        + f"say({answer!r})\n"
        "done()\n"
        'if os.environ.get("AELIX_JOBS_PROTOCOL") == "1":\n'
        '    emit({"type": "aelix_print_done"})\n'
    )


async def within(future: asyncio.Future[Any], seconds: float = CALL_SECONDS, what: str = "the call") -> Any:
    """``future``'s result, or an assertion when it has not ended within ``seconds`` (cancelled then)."""

    done, _ = await asyncio.wait({future}, timeout=seconds)
    if not done:
        future.cancel()
        raise AssertionError(f"{what} did not end within {seconds} s")
    return future.result()


async def call(rig: Any, args: dict[str, Any], **kw: Any) -> Any:
    """``rig.call``, bounded by :func:`within`."""

    return await within(asyncio.ensure_future(rig.call(args, **kw)))


@pytest.fixture
async def reap_children(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[list[Any]]:
    """Every child the test spawns, killed at its end if still alive; every rig finished."""

    spawned: list[Any] = []
    real = asyncio.create_subprocess_exec

    async def recording(*args: Any, **kwargs: Any) -> Any:
        proc = await real(*args, **kwargs)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", recording)
    _RIGS.clear()
    yield spawned
    for rig in list(_RIGS):
        with contextlib.suppress(Exception):
            await asyncio.wait_for(finish(rig), 20)
    _RIGS.clear()
    for proc in spawned:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), 5)


class StandInChrome:
    """What ``JobsUi`` reads of the chrome: a turn runs, no modal, and the interrupt latch."""

    def __init__(self) -> None:
        self.running = True
        self.interrupt_pending = False
        self.modal = False

    def is_modal_open(self) -> bool:
        return self.modal

    def invalidate(self) -> None:
        return None

    def queue_wake(self, item: Any) -> None:
        return None

    def note_interrupt(self) -> None:
        self.interrupt_pending = True


class StandInHost:
    def __init__(self, harness: Any) -> None:
        self.harness = harness
        self.in_transition = False
        self.stopped = None


async def tui_rig(tmp_path: Path, **kwargs: Any) -> Any:
    rig = await _rig(tmp_path, **kwargs)
    rig.ext.detach_host = lambda: True
    rig.chrome = StandInChrome()
    rig.host = StandInHost(rig.harness)
    rig.notes = []
    rig.jobs_ui = JobsUi(rig.service, chrome=rig.chrome, runtime_host=rig.host, commit=rig.notes.append)
    rig.finished = False
    _RIGS.append(rig)
    return rig


def ready(rig: Any) -> int:
    return len(rig.service.detach.ready())


async def parked_call(rig: Any, args: dict[str, Any] | None = None, **kw: Any) -> asyncio.Future[Any]:
    """Start a call (as the turn task does) and return once its run offers itself to Ctrl+B."""

    turn = asyncio.ensure_future(rig.call(args or {"profile": "scout", "task": "count files"}, **kw))
    await wait_for(lambda: ready(rig) == 1 or turn.done())
    assert not turn.done(), "the call ended before it was offered"
    return turn


def row_of(rig: Any) -> Any:
    runtime = rig.ext.runtime
    assert runtime is not None
    (row,) = runtime._children.values()
    return row


async def settled(rig: Any) -> Any:
    def done() -> bool:
        snaps = rig.jobs()
        return bool(snaps) and snaps[0].state in ("done", "failed", "stopped", "lost") and snaps[0].notice_state != "none"

    await wait_for(done)
    return rig.jobs()[0]


async def finish(rig: Any) -> None:
    """The test's end: the job service shut down, then every run's cleanup task joined.

    A run that ended cancelled, or that a stop ended, leaves its cleanup task
    (the child's exit, its sweep, its pipes) running past the call; joined here,
    so no subprocess transport outlives the test's loop (Linux reports one as an
    unraisable ``Event loop is closed``).
    """

    if getattr(rig, "finished", False):
        return
    rig.finished = True
    await rig.service.shutdown()
    runtime = rig.ext.runtime
    if runtime is not None:
        await runtime.stop_all()
    await rig.ext.drain_cleanups()

