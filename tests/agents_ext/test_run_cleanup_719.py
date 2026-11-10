"""One cleanup task per delegated run, published at its first end, joined by every remover (#719, #750; r3 §3.4).

The runtime gives each row its registry before the channel runs; the channel
creates the run's ONE task and adds it there in the same synchronous step
(Codex N8). ``run`` joins it on a normal end, ``stop(id)`` after its abort,
``stop_all`` through one shared, bounded drain (Codex N9) — also when the row is
already gone, which the cancel path makes happen (``_run`` pops it first). Esc's
cancel path does not wait for it (D14). A task still running at
``CLEANUP_BOUND`` stays and is reported.

None of it exists on ``478ffd13``: each test there fails at the first touch of
``RunningChild.cleanup`` / ``_SubagentRuntimeImpl._cleanups`` (``AttributeError``)
or ``drain_cleanups``; what it asserts is in its docstring.

A real stub child through the real runtime and ``PrintChannel``; the sweep is
replaced by a slow stand-in (``_slow_sweep``) where a test needs the task to
still be running when a remover arrives.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import aelix_agents.print_channel as pc
import aelix_agents.runtime as rt
import pytest
from aelix_agents.consent import SpawnGrant
from aelix_agents.prompt_file import _pid_is_live
from aelix_agents.runtime import SpawnRecordMeta, SubagentHost, _SubagentRuntimeImpl
from aelix_coding_agent.builtin.permission_mode import PermissionMode

from tests.agents_ext.test_print_channel_spawn import (
    _HAPPY,
    _SLEEPER,
    _resolved,
    _stub_channel,
)
from tests.event_waits import wait_until

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX stub children")


def _runtime(tmp_path: Path, script: str) -> _SubagentRuntimeImpl:
    return _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path), active_tools=lambda: ["read"]),
        channel=_stub_channel(script, grace=1.0),
    )


def _spawn(runtime: _SubagentRuntimeImpl) -> asyncio.Future[Any]:
    resolved = _resolved()
    grant = SpawnGrant(
        profile=resolved.name,
        source_path=resolved.source_path,
        scope=resolved.scope,
        mode=PermissionMode.PLAN,
        widened=False,
        consented=True,
    )
    return asyncio.ensure_future(
        runtime.spawn_granted(
            grant, resolved, "t", record=SpawnRecordMeta(tool_call_id="tc-719", mode="single", index=0)
        )
    )


def _slow_sweep(monkeypatch: pytest.MonkeyPatch, seconds: float) -> list[float]:
    """The sweep, replaced by one that takes ``seconds``; the times it ENDED are returned."""

    ended: list[float] = []

    async def _sweep(_tracking: Any, *, parent_stopped: bool) -> pc.HelperAnswer:
        await asyncio.sleep(seconds)
        ended.append(time.monotonic())
        return pc.HelperAnswer(confirmed=True)

    monkeypatch.setattr(pc, "sweep_helpers", _sweep)
    return ended


async def _row(runtime: _SubagentRuntimeImpl) -> Any:
    await wait_until(
        lambda: any(r.proc is not None for r in runtime._children.values()),
        what="the stub child to start",
    )
    (row,) = runtime._children.values()
    return row


async def test_the_cleanup_is_in_the_registry_before_run_awaits_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N (Codex N8, ``registry_transfer``): when ``run`` first awaits the cleanup, the registry holds it.

    So a ``stop_all`` that runs while ``run`` waits finds it there.
    """

    seen: list[tuple[bool, bool]] = []
    real = pc.join_cleanup

    async def _spy(duty: Any) -> None:
        if duty is not None:
            seen.append((runtime._cleanups.get(duty.key) is duty, duty.task.done()))
        await real(duty)

    monkeypatch.setattr(pc, "join_cleanup", _spy)
    runtime = _runtime(tmp_path, _HAPPY)
    result = await asyncio.wait_for(_spawn(runtime), 60)
    assert result.ok, result
    assert seen == [(True, False)]
    assert runtime._cleanups == {}


async def test_stop_all_joins_a_cleanup_whose_row_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N (Codex #2): Esc ends the run and ``_run`` pops its row; ``stop_all`` still waits for its cleanup."""

    ended = _slow_sweep(monkeypatch, 0.8)
    runtime = _runtime(tmp_path, _SLEEPER)
    delegation = _spawn(runtime)
    row = await _row(runtime)
    delegation.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await delegation
    assert runtime._children == {}
    duty = runtime._cleanups.get(row.id)
    assert duty is not None and not duty.task.done()
    await runtime.stop_all()
    assert duty.task.done() and ended, "stop_all returned before the cleanup"
    assert row.proc.returncode is not None and row.prompt is None


async def test_stop_id_joins_the_same_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """N: ``stop(id)`` returns after the run's one cleanup ended — the task ``run`` itself joins."""

    _slow_sweep(monkeypatch, 0.5)
    joined: list[Any] = []
    real = pc.join_cleanup

    async def _spy(duty: Any) -> None:
        joined.append(duty)
        await real(duty)

    monkeypatch.setattr(pc, "join_cleanup", _spy)
    monkeypatch.setattr(rt, "join_cleanup", _spy)
    runtime = _runtime(tmp_path, _SLEEPER)
    delegation = _spawn(runtime)
    row = await _row(runtime)
    await runtime.stop(row.id)
    assert row.cleanup is not None and row.cleanup.task.done()
    result = await asyncio.wait_for(delegation, 30)
    assert result.status == "aborted", result
    assert joined and all(d is row.cleanup for d in joined), joined


async def test_a_cancelled_awaiter_does_not_cancel_the_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N (Codex N9): a cancel that lands while ``run`` waits for its cleanup leaves the cleanup running."""

    ended = _slow_sweep(monkeypatch, 0.8)
    runtime = _runtime(tmp_path, _HAPPY)
    delegation = _spawn(runtime)
    await wait_until(
        lambda: any(d.ended.is_set() for d in runtime._cleanups.values()),
        what="the run to reach its cleanup",
    )
    (duty,) = runtime._cleanups.values()
    delegation.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await delegation
    await asyncio.wait({duty.task}, timeout=10)
    assert duty.task.done() and not duty.task.cancelled() and ended


async def test_concurrent_disposers_share_one_drain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """N (Codex N9): two disposers at once wait on ONE drain, and neither returns before the cleanup."""

    ended = _slow_sweep(monkeypatch, 0.8)
    runtime = _runtime(tmp_path, _SLEEPER)
    delegation = _spawn(runtime)
    await _row(runtime)
    delegation.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await delegation
    first = runtime.drain_cleanups()
    assert runtime.drain_cleanups() is first
    finished: list[float] = []

    async def _dispose() -> None:
        await runtime.stop_all()
        finished.append(time.monotonic())

    await asyncio.gather(_dispose(), _dispose())
    assert ended and all(t >= ended[0] for t in finished), (ended, finished)
    assert first.done()


async def test_a_cleanup_past_the_bound_is_kept_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """N: past ``CLEANUP_BOUND`` the task stays in the registry, one line says so, a later drain joins it."""

    _slow_sweep(monkeypatch, 1.5)
    monkeypatch.setattr(rt, "CLEANUP_BOUND", 0.3)
    runtime = _runtime(tmp_path, _SLEEPER)
    delegation = _spawn(runtime)
    row = await _row(runtime)
    delegation.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await delegation
    await runtime.stop_all()
    duty = runtime._cleanups.get(row.id)
    assert duty is not None and not duty.task.done(), "kept, not dropped"
    err = capsys.readouterr().err
    assert f"aelix: cleanup of delegated run {row.id} did not finish (sweeping what its bash left)" in err
    monkeypatch.setattr(rt, "CLEANUP_BOUND", 10.0)
    await runtime.drain_cleanups()
    assert duty.task.done() and runtime._cleanups == {}


async def test_a_sweep_that_raises_still_removes_the_directory_and_closes_the_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """N: the cleanup's last two steps run however its sweep ends; the failure is one DEBUG log line."""

    async def _boom(_tracking: Any, *, parent_stopped: bool) -> pc.HelperAnswer:
        raise RuntimeError("sweep exploded")

    monkeypatch.setattr(pc, "sweep_helpers", _boom)
    runtime = _runtime(tmp_path, _HAPPY)
    seen: list[Any] = []
    real = pc.join_cleanup

    async def _spy(duty: Any) -> None:
        await real(duty)
        seen.append(duty)

    monkeypatch.setattr(pc, "join_cleanup", _spy)
    with caplog.at_level("DEBUG", logger="aelix_agents.print_channel"):
        result = await asyncio.wait_for(_spawn(runtime), 60)
    assert result.ok, result
    (duty,) = seen
    assert duty.task.done() and duty.step == "removing its prompt directory"
    assert any("its cleanup failed" in r.getMessage() for r in caplog.records)
    assert runtime._cleanups == {}


async def test_a_row_this_channel_did_not_spawn_gets_no_cleanup_task(tmp_path: Path) -> None:
    """N: an rpc row (a process, no ``channel_cleans``) or a row with no process owes no task."""

    class _Proc:
        pid = 1
        returncode = None

    rpc_row = pc.RunningChild(id="sub-rpc", profile="p", proc=_Proc())
    assert pc.ensure_cleanup(rpc_row, grace=1.0) is None and rpc_row.cleanup is None
    assert pc.ensure_cleanup(pc.RunningChild(id="sub-none", profile="p"), grace=1.0) is None


async def test_the_run_reads_the_agents_start_time_for_its_marker_scan(tmp_path: Path) -> None:
    """N: the token's hits are filtered by the agent's own start time, read right after the spawn."""

    from aelix_ai.utils._process_identity import process_started

    runtime = _runtime(tmp_path, _SLEEPER)
    delegation = _spawn(runtime)
    row = await _row(runtime)
    try:
        assert row.tracking is not None
        assert row.tracking.agent_stamp is not None
        assert row.tracking.agent_stamp == process_started(row.proc.pid)
    finally:
        await runtime.stop(row.id)
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(delegation, 30)


async def test_the_clis_last_drain_joins_an_older_builds_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N: a rebuild's old runtime still holds a cleanup; the CLI's ``finally`` drain joins it.

    ``cli/entry.py`` calls ``_drain_delegation_cleanups(agents_ext)`` after its
    last dispose, because a second dispose returns at once (Codex N9).
    """

    from aelix_agents.extension import AgentsExtension
    from aelix_coding_agent.cli.entry import _drain_delegation_cleanups

    ended = _slow_sweep(monkeypatch, 0.6)
    old = _runtime(tmp_path, _SLEEPER)
    ext = AgentsExtension(cwd=str(tmp_path), agent_dir=str(tmp_path / "agent"))
    ext._built = [old, _runtime(tmp_path, _HAPPY)]
    delegation = _spawn(old)
    await _row(old)
    delegation.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await delegation
    assert old._cleanups
    await _drain_delegation_cleanups(ext)
    assert old._cleanups == {} and ended


# ── fix round 1 ───────────────────────────────────────────────────


def _delayed_spawn(monkeypatch: pytest.MonkeyPatch) -> tuple[list[Any], asyncio.Event, asyncio.Event]:
    """``create_subprocess_exec`` that starts the child, then holds its return until released (Codex 3)."""

    real = asyncio.create_subprocess_exec
    spawned: list[Any] = []
    entered, release = asyncio.Event(), asyncio.Event()

    async def _delayed(*args: Any, **kwargs: Any) -> Any:
        proc = await real(*args, **kwargs)
        spawned.append(proc)
        entered.set()
        await release.wait()
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _delayed)
    return spawned, entered, release


async def test_a_stop_all_during_a_spawn_keeps_its_directory_and_reports_the_duty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """N (Codex 3, its real-child probe with the deadline shortened): a spawn in flight is a duty.

    The OS child exists, ``row.proc`` does not yet. ``stop_all`` keeps the
    prompt directory and its fallback ledger, and at the bound reports the
    run's duty; once the spawn returns, the run kills the child and the duty
    ends. Fails on ``7d298a8d``: ``ledger_exists=False``, ``cleanup=None`` and
    nothing on stderr while the child is alive.
    """

    spawned, entered, release = _delayed_spawn(monkeypatch)
    monkeypatch.setattr(rt, "CLEANUP_BOUND", 0.08)
    runtime = _runtime(tmp_path, "import time; time.sleep(5)\n" + _HAPPY)
    delegation = _spawn(runtime)
    await entered.wait()
    (row,) = runtime._children.values()
    ledger = Path(row.tracking.ledger)
    try:
        await runtime.stop_all()
        err = capsys.readouterr().err
        assert ledger.is_file(), "the in-flight spawn's ledger was deleted"
        assert row.cleanup is not None and not row.cleanup.task.done()
        assert f"aelix: cleanup of delegated run {row.id} did not finish (waiting for the run to end)" in err
    finally:
        release.set()
        result = await asyncio.wait_for(delegation, 30)
        monkeypatch.setattr(rt, "CLEANUP_BOUND", 10.0)
        await runtime.drain_cleanups()
    assert result.status == "aborted", result
    assert spawned[0].returncode is not None and runtime._cleanups == {}
    assert not ledger.exists()


async def test_a_stop_during_a_spawn_returns_once_the_child_is_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N (Codex 3): ``stop(id)`` while the spawn is in flight joins the run's duty — it returns with the child dead.

    Fails on ``7d298a8d``: it returns at once, the run pending, ``cleanup=None``.
    """

    spawned, entered, release = _delayed_spawn(monkeypatch)
    runtime = _runtime(tmp_path, _SLEEPER)
    delegation = _spawn(runtime)
    await entered.wait()
    (row,) = runtime._children.values()
    releaser = asyncio.get_running_loop().call_later(0.3, release.set)
    try:
        await asyncio.wait_for(runtime.stop(row.id), 30)
        assert spawned[0].returncode is not None, "stop returned with the spawned child alive"
        assert row.cleanup is not None and row.cleanup.task.done()
    finally:
        releaser.cancel()
        release.set()
        await asyncio.wait_for(delegation, 30)


async def test_a_reload_keeps_a_runtime_whose_spawn_is_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N (Codex 3; X01): a rebuild keeps the older runtime while a duty of its is open; the last drain joins it."""

    from aelix_agents.extension import AgentsExtension
    from aelix_coding_agent.extensions.api import Extension, ExtensionAPI, _ExtensionRuntime

    spawned, entered, release = _delayed_spawn(monkeypatch)
    ext = AgentsExtension(cwd=str(tmp_path), agent_dir=str(tmp_path / "agent"))
    ext(ExtensionAPI(Extension(name="aelix-agents"), _ExtensionRuntime()))
    first = ext.runtime
    assert first is not None
    first.channel = _stub_channel(_SLEEPER, grace=1.0)
    delegation = _spawn(first)
    await entered.wait()
    ext(ExtensionAPI(Extension(name="aelix-agents"), _ExtensionRuntime()))
    try:
        assert first in ext._built and ext.runtime is not first
    finally:
        release.set()
        await first.stop_all()
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(delegation, 30)
        await ext.drain_cleanups()
    assert spawned[0].returncode is not None and first._cleanups == {}


async def test_a_stop_after_esc_joins_the_cleanup_its_row_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N (Codex 5): Esc pops the row and leaves its sweep running; ``stop(id)`` then waits for that sweep.

    Esc itself still does not wait (D14). Fails on ``7d298a8d``: ``stop``
    returns with the duty pending.
    """

    started, gate = asyncio.Event(), asyncio.Event()

    async def _sweep(_tracking: Any, *, parent_stopped: bool) -> pc.HelperAnswer:
        started.set()
        await gate.wait()
        return pc.HelperAnswer(confirmed=True)

    monkeypatch.setattr(pc, "sweep_helpers", _sweep)
    runtime = _runtime(tmp_path, _SLEEPER)
    delegation = _spawn(runtime)
    row = await _row(runtime)
    delegation.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await delegation
    await started.wait()
    duty = runtime._cleanups[row.id]
    stopping = asyncio.ensure_future(runtime.stop(row.id))
    await asyncio.sleep(0.2)
    assert not stopping.done(), "stop(id) returned past the run's open cleanup"
    gate.set()
    await asyncio.wait_for(stopping, 10)
    assert duty.task.done()


async def test_a_pipe_holder_the_sweep_cannot_name_does_not_keep_the_transport_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N (Codex 6; P20): the child is reaped, a holder nobody may signal keeps its pipes — the parent closes its own ends.

    The marker scan is made to find nothing (the ADR-0296 residual a holder
    out of every reach is). After the cleanup, the child's pipe transports are
    closed and the holder is still alive: nothing signalled it. Fails on
    ``7d298a8d``: ``cleanup_done=True``, the registry empty, the transport open.
    """

    from aelix_coding_agent.tools import launch_ledger

    from tests.agents_ext.test_print_channel_spawn import _announced_pid, _escaped_holder_stub

    monkeypatch.setattr(launch_ledger, "marker_hits", lambda *a, **k: [])
    monkeypatch.setattr(pc, "CLEANUP_CONFIRM_SECONDS", 0.2)
    marker = tmp_path / "holder.pid"
    runtime = _runtime(tmp_path, _escaped_holder_stub(marker))
    delegation = _spawn(runtime)
    await wait_until(lambda: _announced_pid(marker) is not None, what="the stub's holder")
    (row,) = runtime._children.values()
    proc, holder = row.proc, _announced_pid(marker)
    assert holder is not None
    try:
        delegation.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await delegation
        await pc.join_cleanup(row.cleanup)
        pipes = [proc._transport.get_pipe_transport(fd) for fd in (1, 2)]
        assert proc.returncode is not None
        assert all(p is None or p.is_closing() for p in pipes), "the transport was left open"
        assert _pid_is_live(holder), "the residual holder was not to be signalled"
    finally:
        with contextlib.suppress(OSError):
            os.kill(holder, signal.SIGKILL)
        await runtime.drain_cleanups()


async def test_the_sweep_runs_after_the_child_has_exited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The task awaits the stopped child before it sweeps (P19): a stop's sweep never races a live agent."""

    seen: list[int | None] = []
    real = pc.sweep_helpers

    async def _spy(tracking: Any, *, parent_stopped: bool) -> pc.HelperAnswer:
        seen.append(row.proc.returncode)
        return await real(tracking, parent_stopped=parent_stopped)

    monkeypatch.setattr(pc, "sweep_helpers", _spy)
    runtime = _runtime(tmp_path, _SLEEPER)
    delegation = _spawn(runtime)
    row = await _row(runtime)
    await runtime.stop(row.id)
    await asyncio.wait_for(delegation, 30)
    assert seen and seen[0] is not None, seen


async def test_stop_all_leaves_a_runs_directory_to_its_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``stop_all`` does not remove the prompt directory of a run with a cleanup task (R02).

    That directory holds the fallback ledger the task's sweep reads.
    """

    seen: list[bool] = []

    async def _sweep(tracking: Any, *, parent_stopped: bool) -> pc.HelperAnswer:
        await asyncio.sleep(0.3)
        seen.append(Path(tracking.ledger).is_file())
        return pc.HelperAnswer(confirmed=True)

    monkeypatch.setattr(pc, "sweep_helpers", _sweep)
    runtime = _runtime(tmp_path, _SLEEPER)
    delegation = _spawn(runtime)
    await _row(runtime)
    await runtime.stop_all()
    await asyncio.wait_for(delegation, 30)
    assert seen == [True]


_REPORT = textwrap.dedent(
    """
    import asyncio, contextlib, sys
    sys.path.insert(0, sys.argv[1])
    import aelix_agents.print_channel as pc
    import aelix_agents.runtime as rt
    from aelix_agents.consent import SpawnGrant
    from aelix_agents.runtime import SpawnRecordMeta, SubagentHost, _SubagentRuntimeImpl
    from aelix_coding_agent.builtin.permission_mode import PermissionMode
    from tests.agents_ext.test_print_channel_spawn import _SLEEPER, _resolved, _stub_channel

    has_ui = sys.argv[2] == "ui"

    async def _sweep(_t, *, parent_stopped):
        await asyncio.sleep(1.0)
        return pc.HelperAnswer(confirmed=False, reason="the agent ended without its own cleanup")

    pc.sweep_helpers = _sweep
    rt.CLEANUP_BOUND = 0.3

    async def main(tmp):
        runtime = _SubagentRuntimeImpl(
            host=SubagentHost(cwd=lambda: tmp, active_tools=lambda: ["read"], has_ui=lambda: has_ui),
            channel=_stub_channel(_SLEEPER, grace=1.0),
        )
        r = _resolved()
        grant = SpawnGrant(profile=r.name, source_path=r.source_path, scope=r.scope,
                           mode=PermissionMode.PLAN, widened=False, consented=True)
        d = asyncio.ensure_future(runtime.spawn_granted(
            grant, r, "t", record=SpawnRecordMeta(tool_call_id="tc", mode="single", index=0)))
        for _ in range(400):
            if any(c.proc is not None for c in runtime._children.values()):
                break
            await asyncio.sleep(0.05)
        d.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await d
        await runtime.stop_all()
        rt.CLEANUP_BOUND = 10.0
        await runtime.drain_cleanups()

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(main(tmp))
    """
)


@pytest.mark.parametrize("mode", ["headless", "ui"])
def test_the_unfinished_line_is_one_line_headless_and_none_on_a_tui(tmp_path: Path, mode: str) -> None:
    """N (verify F1, F2; its reproducer, with no log handler, as the product runs).

    Headless (``-p``, json): exactly one ``did not finish`` line on stderr.
    Under a TUI: nothing on the terminal — not that line, not the sweep's
    "not confirmed stopped" — so the frame is not painted over; the log has
    both at DEBUG. Fails on ``7d298a8d``: twice headless, once under the TUI.
    """

    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _REPORT, str(root), mode],
        cwd=tmp_path,
        capture_output=True,
        timeout=60,
        check=False,
    )
    err = proc.stderr.decode("utf-8", errors="replace")
    assert proc.returncode == 0, err[-2000:]
    lines = [line for line in err.splitlines() if "did not finish" in line]
    assert len(lines) == (1 if mode == "headless" else 0), err
    assert "not confirmed stopped" not in err, err


async def test_a_spawn_that_fails_ends_the_duty_published_before_it(tmp_path: Path) -> None:
    """The duty published before the spawn ends when the spawn raises; nothing is left to drain (F18)."""

    runtime = _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path), active_tools=lambda: ["read"]),
        channel=_stub_channel("", argv=[str(tmp_path / "no-such-interpreter"), "-c", ""]),
    )
    result = await asyncio.wait_for(_spawn(runtime), 30)
    assert result.status == "error", result
    await wait_until(lambda: runtime._cleanups == {}, timeout=5, what="the spawn's duty to end")
