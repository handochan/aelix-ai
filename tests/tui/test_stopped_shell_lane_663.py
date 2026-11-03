"""#663 x #596 — "stopped" on the ``!`` paths #596 added, and #596's records through #663's transitions.

#663 (ADR-0289) stops aelix when a rebuild and the restore after it both fail:
every prompt, ``!`` line and tool call is refused with "aelix is stopped until
/reload succeeds" until a build works. Its refusal sat in the input loop. #596
(ADR-0287) added ``!`` paths that never pass it — Enter on a ``!`` while a turn
or a command holds the session runs it at once (``ShellLane.run``), Alt+Enter
queues one to run after the turn — and holds a ``!`` record in the lane, written
later under ``AgentHarness.idle_write``. Measured on the rebase of #663 onto
#596 before this file's fix:

- a ``!`` / ``!!`` Entered while stopped under a turn or a command ran, through
  a harness whose ``user_bash`` handlers were gone with its HookBus;
- an Alt+Enter ``!`` queued before a stop sat in the pending area as "runs after
  the turn" for good: the input loop's refusal printed a line and left it there;
- a record held across a stop went to the stopped harness: ``idle_write`` let it
  in, the session took the write, and the stop held nothing.

The second half pins #596's guarantees through #663's lifecycle lock
(``AgentSessionRuntime._transition``) on a REAL runtime: a held record for a
session the user left is dropped, a ``/reload`` (kept or not) delivers it, and
``idle_write`` deadlocks neither against a transition nor against ``dispose()``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessError
from aelix_agent_core.runtime import STOPPED_MESSAGE, RebuildFailedError, ReloadSeed
from aelix_agent_core.session import CustomMessageEntry, Session
from aelix_coding_agent.cli.repl import BASH_EXECUTION_TYPE, UserBashRun, record_user_bash
from aelix_coding_agent.tui import shell_lane as lane_mod
from aelix_coding_agent.tui.shell_lane import ShellLane
from rich.text import Text
from test_run_tui_smoke import (
    FakeHarness,
    FakeRuntime,
    _harness_chrome,
    _launch,
    _quit_within,
    _spy_commits,
    _wait,
)
from test_shell_mid_turn_596 import (
    _Bash,
    _HoldingHarness,
    _pending_text,
    _plain,
    _QuietCompactHarness,
)

from tests.runtime.test_failed_rebuild_663 import _always, _once, _Rig


async def _stop(rig: _Rig) -> AgentHarness:
    """A ``/reload`` whose build and restore both fail: the runtime is stopped."""

    rig.fail = _always(RuntimeError("build boom"))
    with pytest.raises(RebuildFailedError) as caught:
        await rig.runtime.reload()
    assert caught.value.state == "stopped"
    assert rig.runtime.stopped == STOPPED_MESSAGE
    return rig.runtime.harness


def _works(_session: Session, _seed: ReloadSeed | None) -> BaseException | None:
    return None


async def _bash_records(session: Session | None) -> list[str]:
    assert session is not None
    return [
        str(e.content)
        for e in await session.get_entries()
        if isinstance(e, CustomMessageEntry) and e.custom_type == BASH_EXECUTION_TYPE
    ]


# === AgentHarness.idle_write on a disposed harness ==============================


async def test_idle_write_refuses_a_stopped_harness_before_it_waits(tmp_path: Any) -> None:
    rig = await _Rig.create(tmp_path)
    reloaded = rig.runtime.harness
    rig.fail = _works
    await rig.runtime.reload()
    torn_down = reloaded  # a successful reload's superseded harness: disposed, not stopped
    stopped = await _stop(rig)
    wrote: list[str] = []

    async def enter(harness: AgentHarness) -> None:
        async with harness.idle_write():
            wrote.append("body")

    for harness, reason in (
        (stopped, STOPPED_MESSAGE),
        (torn_down, "AgentHarness has been disposed"),
    ):
        # A drain in flight holds the lock: the refusal must not wait behind it.
        async with harness._drain_lock:
            with pytest.raises(AgentHarnessError) as refused:
                await asyncio.wait_for(enter(harness), 2)
        assert refused.value.code == "invalid_state"
        assert reason in str(refused.value)
    assert wrote == []
    assert await _bash_records(rig.session) == []


async def test_a_write_waiting_behind_a_drain_finds_the_harness_stopped_and_writes_nothing(
    tmp_path: Any,
) -> None:
    rig = await _Rig.create(tmp_path)
    harness = rig.runtime.harness
    wrote: list[str] = []

    async def enter() -> None:
        async with harness.idle_write():
            wrote.append("body")

    await harness._drain_lock.acquire()  # dispose()'s drain, say
    task = asyncio.create_task(enter())
    for _ in range(5):
        await asyncio.sleep(0)
    assert not task.done()  # past the entry checks, waiting for the lock
    harness.stop(STOPPED_MESSAGE)
    harness._drain_lock.release()
    with pytest.raises(AgentHarnessError) as refused:
        await asyncio.wait_for(task, 5)
    assert refused.value.code == "invalid_state"
    assert STOPPED_MESSAGE in str(refused.value)
    assert wrote == []


@pytest.mark.parametrize("ending", ["reload", "new_session", "dispose"])
async def test_a_teardown_waits_for_a_held_write_and_neither_deadlocks(
    tmp_path: Any, ending: str
) -> None:
    """``_transition`` → teardown → ``dispose()`` waits on ``idle_write``'s lock; the
    write never waits on the transition, so both end and the write lands first."""

    rig = await _Rig.create(tmp_path)
    harness = rig.runtime.harness
    held, release = asyncio.Event(), asyncio.Event()

    async def write() -> None:
        async with harness.idle_write():
            held.set()
            await release.wait()
            await record_user_bash(harness, UserBashRun("echo HELD", "HELD\n", 0, False))

    writer = asyncio.create_task(write())
    await held.wait()
    builds = len(rig.builds)
    end = asyncio.create_task(getattr(rig.runtime, ending)())
    # The teardown begins (dispose marks the harness first) and waits for the write.
    await _wait(lambda: harness.disposed, what="the teardown to reach dispose()")
    for _ in range(20):
        await asyncio.sleep(0)
    assert not end.done() and len(rig.builds) == builds
    release.set()
    await asyncio.wait_for(asyncio.gather(writer, end), 5)
    [record] = await _bash_records(rig.session)
    assert "echo HELD" in record


# === the lane over a real runtime ===============================================


class _RealLane:
    """A ``ShellLane`` over ``rig.runtime.harness``: the real record, a fake run."""

    def __init__(self, rig: _Rig, monkeypatch: pytest.MonkeyPatch) -> None:
        self.turn = {"on": False}
        self.commits: list[str] = []
        self.rows: list[list[str]] = [[]]

        async def _run(
            harness: Any, command: str, *, exclude_from_context: bool, cwd: str
        ) -> UserBashRun:
            return UserBashRun(command, f"{command} output\n", 0, exclude_from_context)

        monkeypatch.setattr(lane_mod, "run_user_bash", _run)
        self.lane = ShellLane(
            busy=lambda: self.turn["on"],
            current_harness=lambda: rig.runtime.harness,
            commit=lambda r: self.commits.append(_plain(r)),
            show=self.rows.append,
            theme=lambda: None,
            width=lambda: 60,
            output_text=lambda out: Text(out.rstrip("\n")),
        )

    async def held(self, command: str) -> None:
        """A ``!`` run while a turn holds the session: its record waits in the lane."""

        self.turn["on"] = True
        await self.lane.run(self.lane.begin("!", command, pending=True), cwd=".")
        self.turn["on"] = False

    @property
    def shown(self) -> str:
        return "\n".join(Text.from_ansi(r).plain for r in self.rows[-1])


async def test_a_held_record_waits_out_a_stop_and_lands_after_the_reload_that_ends_it(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await _Rig.create(tmp_path)
    t = _RealLane(rig, monkeypatch)
    await t.held("echo BEFORE_STOP")
    await _stop(rig)

    await t.lane.settle(from_loop=True)
    assert await _bash_records(rig.session) == []  # nothing written while stopped
    assert "! echo BEFORE_STOP" in t.shown  # still waiting in the pending area
    assert t.commits == []

    rig.fail = _works
    await rig.runtime.reload()
    assert rig.runtime.stopped is None
    await t.lane.settle(from_loop=True)
    [record] = await _bash_records(rig.session)
    assert "echo BEFORE_STOP" in record
    assert any("echo BEFORE_STOP" in str(m) for m in rig.runtime.harness.messages)
    assert t.rows[-1] == []
    [moved] = t.commits
    assert "the model sees the output next turn" in moved


async def test_a_settle_during_a_reload_holds_the_record_and_does_not_wait_for_it(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await _Rig.create(tmp_path)
    t = _RealLane(rig, monkeypatch)
    await t.held("echo MID_RELOAD")
    entered, release = asyncio.Event(), asyncio.Event()

    async def factory(session: Session, *, reload_seed: ReloadSeed | None = None) -> AgentHarness:
        entered.set()
        await release.wait()
        return rig.harness(session)

    rig.runtime._create_harness = factory
    reload = asyncio.create_task(rig.runtime.reload())
    await entered.wait()
    assert rig.runtime.harness.disposed  # torn down; nothing installed yet
    # The settle a turn's end (or the watcher) runs meanwhile: it neither waits
    # for the transition nor writes through the torn-down harness.
    await asyncio.wait_for(t.lane.settle(), 2)
    assert await _bash_records(rig.session) == []
    release.set()
    await asyncio.wait_for(reload, 5)
    await t.lane.settle(from_loop=True)
    [record] = await _bash_records(rig.session)
    assert "echo MID_RELOAD" in record


async def test_a_record_held_across_a_real_new_session_is_dropped(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await _Rig.create(tmp_path)
    t = _RealLane(rig, monkeypatch)
    await t.held("echo LEFT_BEHIND")
    await rig.runtime.new_session()
    assert rig.runtime.harness.session is not rig.session
    await t.lane.settle(from_loop=True)
    assert await _bash_records(rig.session) == []
    assert await _bash_records(rig.runtime.harness.session) == []
    [note] = t.commits
    assert "not recorded: you left its session" in note


@pytest.mark.parametrize("outcome", ["applied", "kept"])
async def test_a_record_held_across_a_reload_lands_through_the_rebuilt_harness(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    rig = await _Rig.create(tmp_path)
    t = _RealLane(rig, monkeypatch)
    await t.held("echo ACROSS_RELOAD")
    before = rig.runtime.harness
    if outcome == "kept":
        rig.fail = _once(RuntimeError("build boom"))
        with pytest.raises(RebuildFailedError) as caught:
            await rig.runtime.reload()
        assert caught.value.state == "kept"
    else:
        await rig.runtime.reload()
    assert rig.runtime.harness is not before and rig.runtime.stopped is None
    await t.lane.settle(from_loop=True)
    [record] = await _bash_records(rig.session)
    assert "echo ACROSS_RELOAD" in record
    assert any("echo ACROSS_RELOAD" in str(m) for m in rig.runtime.harness.messages)


# === run_tui end to end =========================================================


def _stop_fakes(runtime: FakeRuntime, harness: FakeHarness) -> None:
    """What ``AgentSessionRuntime._stop`` leaves: the runtime stopped, its harness disposed."""

    runtime.stopped = STOPPED_MESSAGE  # type: ignore[attr-defined]
    harness.disposed = True  # type: ignore[attr-defined]
    harness.turns_held = STOPPED_MESSAGE  # type: ignore[attr-defined]


def _refusals(commits: list[str], command: str) -> list[str]:
    return [" ".join(c.split()) for c in commits if command in c]


@pytest.mark.parametrize("holder", ["turn", "command"])
async def test_a_bang_entered_while_stopped_under_a_turn_or_a_command_is_refused(
    monkeypatch: pytest.MonkeyPatch, holder: str
) -> None:
    harness = _QuietCompactHarness()
    chrome_ref: dict[str, Any] = {}
    bash = _Bash(monkeypatch, chrome_ref)
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        chrome_ref["chrome"] = chrome
        commits = _spy_commits(chrome)
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running, what="the TUI to start")
        if holder == "turn":
            pipe.send_text("answer\n")
            await _wait(lambda: chrome.running, what="the turn to start")
        else:
            pipe.send_text("/compact\n")
            await _wait(lambda: harness.compacting, what="the command to start")
        # A rebuild an extension started (``ctx.reload()``) failed and stopped aelix.
        _stop_fakes(runtime, harness)
        pipe.send_text("!echo STOPPED_BANG\n")
        # The pending area cuts a row at the width: the refusal's head is enough.
        await _wait(
            lambda: "✖ aelix is stopped until /reload succeeds" in _pending_text(chrome),
            what="the ! refused in its block",
        )
        pipe.send_text("!!echo STOPPED_QUIET\n")
        await _wait(lambda: "STOPPED_QUIET" in _pending_text(chrome), what="the !! block")
        harness.release.set()
        harness.compact_gate.set()
        await _wait(lambda: not chrome._pending_rows, what="the blocks to reach the transcript")
        pipe.send_text("/quit\n")
        await _quit_within(task)
    assert bash.ran == [] and bash.recorded == []
    for command in ("echo STOPPED_BANG", "echo STOPPED_QUIET"):
        [moved] = _refusals(commits, command)
        assert moved.endswith(f"✖ {STOPPED_MESSAGE}")


async def test_an_alt_enter_bang_queued_before_a_stop_is_refused_and_leaves_the_pending_area(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _HoldingHarness()
    chrome_ref: dict[str, Any] = {}
    bash = _Bash(monkeypatch, chrome_ref)
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        chrome_ref["chrome"] = chrome
        commits = _spy_commits(chrome)
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running, what="the TUI to start")
        pipe.send_text("answer\n")
        await _wait(lambda: chrome.running, what="the turn to start")
        pipe.send_text("!echo QUEUED\x1b\r")  # Alt+Enter
        await _wait(
            lambda: "! echo QUEUED  (runs after the turn)" in _pending_text(chrome),
            what="the queued shell follow-up",
        )
        _stop_fakes(runtime, harness)
        harness.release.set()
        await _wait(lambda: not chrome._pending_rows, what="the pending area to empty")
        pipe.send_text("/quit\n")
        await _quit_within(task)
    assert bash.ran == [] and bash.recorded == []
    [moved] = _refusals(commits, "echo QUEUED")
    assert moved.startswith("! echo QUEUED") and moved.endswith(f"✖ {STOPPED_MESSAGE}")


async def test_a_collapsed_paste_that_begins_with_a_bang_is_refused_while_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _HoldingHarness()
    harness.release.set()
    chrome_ref: dict[str, Any] = {}
    bash = _Bash(monkeypatch, chrome_ref)
    body = "!echo PASTED\n" + "\n".join(f"echo l{i}" for i in range(6))
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        chrome_ref["chrome"] = chrome
        commits = _spy_commits(chrome)
        _stop_fakes(runtime, harness)
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running, what="the TUI to start")
        pipe.send_text(f"\x1b[200~{body}\x1b[201~")
        await _wait(lambda: "[Pasted text" in chrome.buffer.text, what="the paste to collapse")
        pipe.send_text("\n")
        await _wait(
            lambda: f"✖ {STOPPED_MESSAGE}" in [" ".join(c.split()) for c in commits],
            what="the stop's refusal",
        )
        pipe.send_text("/quit\n")
        await _quit_within(task)
    assert harness.prompts == [] and bash.ran == []
