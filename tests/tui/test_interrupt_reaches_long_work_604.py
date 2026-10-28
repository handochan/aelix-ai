"""#604 (ADR-0276 D15/D16) — a human can end the work no clock ends any more.

A delegation has no time limit since #604, which turned two holes the
10-minute default used to cover into hangs:

* ``/agents run`` is awaited INSIDE the input loop, outside the turn wrapper,
  so Esc was inert and Ctrl+C only armed the exit window. A child that stalled
  held the TUI until the process was killed from outside. D15 runs the
  command's work as a task the interrupt can cancel (``run_cancellable``).
* SIGTERM / SIGHUP only queued EOF, which the input loop reads after its
  current await — ``harness.prompt``, for as long as a delegation inside the
  turn runs. D16 aborts the running turn (Esc's own ``_safe_abort``), or
  interrupts the running command, before asking for EOF.

Driven through the real ``run_tui`` with a headless chrome, as the smoke tests
are. The ``/agents run`` cases bind a REAL ``_SubagentRuntimeImpl`` with a
stub child, so "the child is reaped" is a process that is gone, not a flag.

No ``__init__.py`` in ``tests/tui`` — local convention.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from aelix_agents.prompt_file import _pid_is_live
from aelix_agents.runtime import SubagentHost, _SubagentRuntimeImpl
from aelix_coding_agent.subagent_contract import ResolvedProfile
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.shell import run_tui

from tests.agents_ext.test_print_channel_spawn import _SLEEPER, _resolved, _stub_channel
from tests.tui.test_run_tui_smoke import (
    FakeHarness,
    FakeRuntime,
    _FakeExtRuntime,
    _harness_chrome,
    _launch,
    _quit_within,
    _spy_commits,
    _wait,
)

_POSIX_SIGNALS = pytest.mark.skipif(
    sys.platform == "win32",
    reason="run_tui installs no SIGTERM/SIGHUP handler on win32 (no loop.add_signal_handler)",
)


class _OneProfileRuntime:
    """The seam ``/agents run`` calls, in front of a REAL runtime.

    ``resolve_profile`` is the only part faked — discovery is not this file's
    subject — and every other member delegates, so the spawn, the registry, the
    cancellation and the reap are the product's own.
    """

    contract_version = 1

    def __init__(self, impl: _SubagentRuntimeImpl) -> None:
        self.impl = impl
        self.spawns = 0

    def resolve_profile(self, name_or_path: str, *, allow_project: bool = False) -> ResolvedProfile:
        return _resolved()

    async def spawn(self, resolved: ResolvedProfile, task: str, **kwargs: Any) -> Any:
        self.spawns += 1
        return await self.impl.spawn(resolved, task, **kwargs)

    def list(self) -> list[Any]:
        return self.impl.list()

    def status(self, id: str) -> Any:  # noqa: A002 — Protocol spelling
        return self.impl.status(id)

    async def stop(self, id: str) -> None:  # noqa: A002 — Protocol spelling
        await self.impl.stop(id)

    async def stop_all(self) -> None:
        await self.impl.stop_all()


class _SubagentsExtRuntime(_FakeExtRuntime):
    def __init__(self, subagents: _OneProfileRuntime) -> None:
        super().__init__()
        self.subagents = subagents


class _DelegatingHarness(FakeHarness):
    """The smoke harness, with a bound subagent runtime and a turn that never ends.

    ``prompt`` parks until ``abort`` — the shape of a turn whose delegation has
    no time limit — so the only way it returns is the abort under test.
    """

    def __init__(self, subagents: _OneProfileRuntime | None = None) -> None:
        super().__init__()
        if subagents is not None:
            self.runtime = _SubagentsExtRuntime(subagents)
        self.in_prompt = False
        self._released = asyncio.Event()

    async def prompt(
        self, text: str, *, source: str = "interactive", images: Any = None
    ) -> list[Any]:
        self.prompts.append((text, source))
        self.in_prompt = True
        try:
            await self._released.wait()
        finally:
            self.in_prompt = False
        return []

    async def abort(self) -> None:
        self.aborts += 1
        self._released.set()


def _real_runtime(tmp_path: Path) -> _SubagentRuntimeImpl:
    """A real runtime whose child is the stub that answers and then sleeps 60 s."""

    return _SubagentRuntimeImpl(
        host=SubagentHost(cwd=lambda: str(tmp_path)),
        channel=_stub_channel(_SLEEPER, grace=0.5),
    )


async def _the_live_child(impl: _SubagentRuntimeImpl) -> int:
    """The pid of the one child ``/agents run`` started, once it exists."""

    def _pid() -> int | None:
        for row in impl._children.values():
            if row.proc is not None:
                return int(row.proc.pid)
        return None

    await _wait(lambda: _pid() is not None, what="the delegated child to spawn")
    pid = _pid()
    assert pid is not None
    return pid


def _capture_signal_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[int, Callable[[], None]]:
    """Take ``run_tui``'s SIGTERM/SIGHUP handlers instead of installing them.

    Installing them for real would put this pytest process's own SIGTERM in the
    TUI's hands; capturing them lets the case call the handler exactly as the
    loop would, with no signal sent anywhere.
    """

    loop = asyncio.get_running_loop()
    handlers: dict[int, Callable[[], None]] = {}

    def _add(sig: int, callback: Callable[..., None], *args: Any) -> None:
        handlers[sig] = lambda: callback(*args)

    def _remove(sig: int) -> bool:
        return handlers.pop(sig, None) is not None

    monkeypatch.setattr(loop, "add_signal_handler", _add)
    monkeypatch.setattr(loop, "remove_signal_handler", _remove)
    return handlers


def _launch_with_signals(runtime: FakeRuntime, chrome: AelixChrome) -> asyncio.Task[int]:
    return asyncio.ensure_future(
        run_tui(
            runtime,  # type: ignore[arg-type]
            cwd=".",
            chrome=chrome,
            install_signal_handlers=True,
        )
    )


# === D15 — /agents run takes the interrupt ===================================


@pytest.mark.parametrize("key", ["\x03", "\x1b"], ids=["ctrl-c", "esc"])
async def test_an_interrupt_ends_a_running_agents_run_and_the_prompt_comes_back(
    tmp_path: Path, key: str
) -> None:
    """Esc or Ctrl+C during ``/agents run``: the work is cancelled, the child is
    reaped, a "stopped" line is printed, and the TUI is back where it was.

    Before D15 neither key reached the command at all — Esc is filtered on
    ``running`` (``chrome.py``), which only the turn wrapper set, and Ctrl+C on
    an empty buffer armed the exit window — so this case hung on its first wait
    with the child alive. ``running`` and ``on_interrupt`` must both come BACK:
    a TUI left "running" would route the next Enter to ``steer()``.
    """

    impl = _real_runtime(tmp_path)
    seam = _OneProfileRuntime(impl)
    harness = _DelegatingHarness(seam)
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        commits = _spy_commits(chrome)
        task = _launch(runtime, chrome)
        try:
            await _wait(lambda: chrome.app.is_running)
            idle_interrupt = chrome.on_interrupt
            pipe.send_text("/agents run scout do the long thing\n")
            pid = await _the_live_child(impl)
            await _wait(lambda: chrome.running, what="the command to mark the TUI running")
            assert chrome.on_interrupt is not idle_interrupt

            pipe.send_text(key)

            await _wait(lambda: not _pid_is_live(pid), what=f"child {pid} to be reaped")
            await _wait(
                lambda: any("stopped" in c for c in commits),
                what="the stopped line",
                detail=lambda: f"commits: {commits!r}",
            )
            await _wait(lambda: not chrome.running, what="running to come back down")
            assert chrome.on_interrupt is idle_interrupt
            assert impl.list() == []
            assert seam.spawns == 1

            # The input loop is alive and Enter is a plain submit again.
            harness._released.set()
            pipe.send_text("still here\n")
            await _wait(lambda: ("still here", "interactive") in harness.prompts)
            pipe.send_text("/quit\n")
            code = await _quit_within(task)
        finally:
            harness._released.set()
            if not task.done():
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
            await impl.stop_all()
    assert code == 0


async def test_a_teardown_cancel_that_meets_an_interrupt_still_cancels_the_loop(
    tmp_path: Path,
) -> None:
    """The work's cancel is ``CommandInterrupted`` only when NOBODY is cancelling
    the input loop itself (``_run_cancellable``'s ``cancelling()`` check).

    The interrupt and a teardown that cancels ``run_tui`` land together: both
    cancel the same work task, and if the handler turned that into
    ``CommandInterrupted`` it would print "stopped" and go back to reading
    input — the teardown's cancel swallowed, ``run_tui`` never ending. The
    verify pass removed the check and every D15 test stayed green (#604 review).
    """

    impl = _real_runtime(tmp_path)
    harness = _DelegatingHarness(_OneProfileRuntime(impl))
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        try:
            await _wait(lambda: chrome.app.is_running)
            idle_interrupt = chrome.on_interrupt
            pipe.send_text("/agents run scout do the long thing\n")
            pid = await _the_live_child(impl)
            await _wait(lambda: chrome.running, what="the command to mark the TUI running")
            interrupt = chrome.on_interrupt
            assert interrupt is not idle_interrupt and interrupt is not None

            # Esc, and in the same breath a teardown cancelling the input loop.
            interrupt()
            task.cancel()

            # An anti-hang bound, not a timing claim: without the check the loop
            # takes the interrupt as its own and never ends.
            done, _pending = await asyncio.wait({task}, timeout=30)
            assert task in done, "the input loop swallowed the teardown's cancel"
            assert task.cancelled()
            await _wait(lambda: not _pid_is_live(pid), what=f"child {pid} to be reaped")
        finally:
            harness._released.set()
            if not task.done():
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
            await impl.stop_all()


async def test_enter_during_agents_run_is_queued_as_the_next_input_not_a_steer(
    tmp_path: Path,
) -> None:
    """``running`` now means "a command runs" too, and there is no turn to steer.

    The chrome routes a running Enter to ``on_steer``; the shell sends a line
    that arrives while a command (not a turn) runs to the input queue, which is
    what Enter did while a handler blocked the loop before D15 — so the line runs
    as the next input instead of sitting orphaned in the harness's steer queue.
    """

    impl = _real_runtime(tmp_path)
    harness = _DelegatingHarness(_OneProfileRuntime(impl))
    steered: list[str] = []

    async def _steer(text: str) -> None:
        steered.append(text)

    harness.steer = _steer  # type: ignore[attr-defined]
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        task = _launch(runtime, chrome)
        try:
            await _wait(lambda: chrome.app.is_running)
            pipe.send_text("/agents run scout do the long thing\n")
            pid = await _the_live_child(impl)
            await _wait(lambda: chrome.running)
            pipe.send_text("after it\n")
            # Let the steer route run before the command ends.
            await asyncio.sleep(0.2)
            pipe.send_text("\x03")
            await _wait(lambda: not _pid_is_live(pid), what=f"child {pid} to be reaped")
            harness._released.set()
            await _wait(lambda: ("after it", "interactive") in harness.prompts)
            assert steered == []
            pipe.send_text("/quit\n")
            await _quit_within(task)
        finally:
            harness._released.set()
            if not task.done():
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
            await impl.stop_all()


# === D16 — SIGTERM / SIGHUP end what is running first =========================


@_POSIX_SIGNALS
@pytest.mark.parametrize("sig_name", ["SIGTERM", "SIGHUP"])
async def test_a_termination_signal_aborts_the_running_turn_before_asking_for_eof(
    monkeypatch: pytest.MonkeyPatch, sig_name: str
) -> None:
    """D16 — the handler aborts the turn, through the same path Esc takes.

    Before D16 it only queued EOF and exited the chrome, while the input loop
    sat in ``await harness.prompt`` — so a turn with a delegation in it kept the
    process alive until the delegation ended, which no clock bounds any more.
    The turn here never ends on its own; only ``abort()`` releases it.
    """

    handlers = _capture_signal_handlers(monkeypatch)
    harness = _DelegatingHarness()
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        task = _launch_with_signals(runtime, chrome)
        try:
            await _wait(lambda: chrome.app.is_running)
            sig = getattr(signal, sig_name)
            assert sig in handlers, f"run_tui installed no {sig_name} handler: {handlers}"
            pipe.send_text("a turn that delegates for a long time\n")
            await _wait(lambda: harness.in_prompt, what="the turn to start")
            assert chrome.running

            handlers[sig]()

            await _wait(lambda: harness.aborts == 1, what="the signal to abort the turn")
            code = await _quit_within(task)
        finally:
            harness._released.set()
            if not task.done():
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
    assert code == 0
    assert harness.aborts == 1


@_POSIX_SIGNALS
async def test_a_termination_signal_interrupts_a_running_agents_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D16 meets D15 — a SIGTERM during ``/agents run`` ends the COMMAND.

    There is no turn to abort, so the handler goes through the command's own
    interrupt: the child is reaped, the handler returns, and the input loop then
    reads the EOF and the TUI exits. Without D16 the loop stays parked in the
    command and ``run_tui`` never returns, which is what ``_quit_within`` would
    report. (The handler's "stopped" line is committed too, but the chrome is
    already exiting by then, so whether it paints is not this case's subject.)
    """

    handlers = _capture_signal_handlers(monkeypatch)
    impl = _real_runtime(tmp_path)
    harness = _DelegatingHarness(_OneProfileRuntime(impl))
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        task = _launch_with_signals(runtime, chrome)
        try:
            await _wait(lambda: chrome.app.is_running)
            pipe.send_text("/agents run scout do the long thing\n")
            pid = await _the_live_child(impl)
            await _wait(lambda: chrome.running)

            handlers[signal.SIGTERM]()

            code = await _quit_within(task)
            await _wait(lambda: not _pid_is_live(pid), what=f"child {pid} to be reaped")
        finally:
            harness._released.set()
            if not task.done():
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
            await impl.stop_all()
    assert code == 0
    assert harness.aborts == 0, "there was no turn for the signal to abort"
    assert impl.list() == []
