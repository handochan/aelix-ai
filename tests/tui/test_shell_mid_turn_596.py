"""#596 (ADR-0287) — a ``!`` command Entered while something holds the session.

Before #596 the chrome steered the line to the model as text. pi checks for
``!`` before its streaming-steer branch and runs it; Aelix now does too. The
block waits in a pending area under the stream (pi's
``pendingMessagesContainer``) and reaches the transcript, and what the model
gets of it (``record_user_bash``) is written, only once nothing holds the
session, as pi holds ``_pendingBashMessages`` until its run ends: written
mid-turn, the record could land between a tool call and its result.

Three layers: the lane on its own (fake harnesses), the lane over a REAL
``AgentHarness`` and JSONL session (the ``idle_write`` race), and ``run_tui``
end to end.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aelix_coding_agent.cli.repl import UserBashRun
from aelix_coding_agent.tui import shell_lane as lane_mod
from aelix_coding_agent.tui.shell_lane import _PENDING_MAX_ROWS, ShellLane
from prompt_toolkit.utils import get_cwidth
from rich.console import Console
from rich.text import Text
from test_run_tui_smoke import (
    FakeHarness,
    _harness_chrome,
    _launch,
    _quit_within,
    _spy_commits,
    _wait,
)


def _run(command: str, *, transient: bool = False, output: str = "out\n") -> UserBashRun:
    return UserBashRun(
        command=command, output=output, exit_code=0, exclude_from_context=transient
    )


def _plain(renderable: object) -> str:
    console = Console(width=100, force_terminal=False, color_system=None)
    with console.capture() as cap:
        console.print(renderable)
    return cap.get()


class _Lane:
    """A lane over a switchable fake harness, with what it commits and shows."""

    def __init__(self, harness: Any | None = None) -> None:
        self.turn = {"on": False}
        self.harness = harness or SimpleNamespace(session=object(), is_idle=True)
        self.commits: list[str] = []
        self.rows: list[list[str]] = [[]]
        self.ran: list[str] = []
        self.recorded: list[tuple[Any, str]] = []
        self.gates: dict[str, asyncio.Event] = {}
        self.lane = ShellLane(
            busy=lambda: self.turn["on"],
            current_harness=lambda: self.harness,
            commit=lambda r: self.commits.append(_plain(r)),
            show=self.rows.append,
            theme=lambda: None,
            width=lambda: 40,
            output_text=lambda out: Text(out.rstrip("\n")),
        )

    def patch(self, monkeypatch: pytest.MonkeyPatch, *, failing_record: bool = False) -> None:
        async def _run_user_bash(
            harness: Any, command: str, *, exclude_from_context: bool, cwd: str
        ) -> UserBashRun:
            self.ran.append(command)
            gate = self.gates.get(command)
            if gate is not None:
                await gate.wait()
            if "many" in command:
                output = "\n".join(f"{command} line {i}" for i in range(6))
            else:
                output = f"{command} out\n"
            return _run(command, transient=exclude_from_context, output=output)

        async def _record(harness: Any, run: UserBashRun) -> None:
            if failing_record:
                raise RuntimeError("disk full")
            self.recorded.append((harness, run.command))

        monkeypatch.setattr(lane_mod, "run_user_bash", _run_user_bash)
        monkeypatch.setattr(lane_mod, "record_user_bash", _record)

    @property
    def shown(self) -> str:
        return "\n".join(Text.from_ansi(r).plain for r in self.rows[-1])

    def commands(self) -> list[str]:
        return [c for _, c in self.recorded]


# === the lane ===================================================================


async def test_a_record_waits_while_anything_runs_and_lands_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = _Lane()
    t.patch(monkeypatch)
    t.turn["on"] = True
    for name in ("first", "second"):
        await t.lane.run(t.lane.begin("!", name, pending=True), cwd=".")
    await t.lane.settle()
    assert t.recorded == [] and t.commits == []  # the turn is still running
    assert "! first" in t.shown and "! second" in t.shown

    t.turn["on"] = False
    t.harness.is_idle = False  # an extension's turn, or a compaction
    await t.lane.settle()
    assert t.recorded == []

    t.harness.is_idle = True
    await t.lane.settle()
    assert t.commands() == ["first", "second"]
    assert t.rows[-1] == []  # the pending area is empty again
    # Each block reaches the transcript ONCE, its command line over its output.
    assert len(t.commits) == 2
    assert t.commits[0].startswith("! first  (the model sees the output next turn)\nfirst out")

    await t.lane.run(t.lane.begin("!", "idle", state="queued"), cwd=".")
    assert t.commands()[-1] == "idle"  # nothing runs: at once
    assert t.commits[-1] == "idle out\n"  # an idle ``!``: its output alone, as before


async def test_a_double_bang_is_shown_but_never_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _Lane()
    t.patch(monkeypatch)
    t.turn["on"] = True
    await t.lane.run(t.lane.begin("!!", "secret", pending=True), cwd=".")
    assert "!! secret  (the model won't see it)" in t.shown
    t.turn["on"] = False
    await t.lane.settle()
    assert t.recorded == []
    assert t.commits[0].startswith("!! secret  (the model won't see it)")


async def test_a_record_for_a_session_left_meanwhile_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Codex 596-03 / verify V2: ``/new`` (``/resume``, ``/fork``, …) REPLACES the
    # harness; the old one keeps its session, so comparing the old harness with
    # itself never fired. The lane compares with the harness in force.
    t = _Lane()
    t.patch(monkeypatch)
    t.turn["on"] = True
    await t.lane.run(t.lane.begin("!", "old", pending=True), cwd=".")
    t.harness = SimpleNamespace(session=object(), is_idle=True)  # the runtime's swap
    t.turn["on"] = False
    await t.lane.settle()
    assert t.recorded == []
    assert t.commits[0].startswith("! old  (not recorded: you left its session)")


async def test_a_reload_keeps_the_session_and_records_through_the_new_harness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``/reload`` rebuilds the harness over the SAME session: delivered.
    t = _Lane()
    t.patch(monkeypatch)
    t.turn["on"] = True
    await t.lane.run(t.lane.begin("!", "kept", pending=True), cwd=".")
    rebuilt = SimpleNamespace(session=t.harness.session, is_idle=True)
    t.harness = rebuilt
    t.turn["on"] = False
    await t.lane.settle()
    assert t.recorded == [(rebuilt, "kept")]


async def test_a_failing_record_does_not_reach_the_input_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = _Lane()
    t.patch(monkeypatch, failing_record=True)
    await t.lane.run(t.lane.begin("!", "x", state="queued"), cwd=".")  # does not raise
    await t.lane.settle()


async def test_a_command_that_cannot_run_shows_why_in_its_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = _Lane()

    async def _boom(*_a: object, **_k: object) -> UserBashRun:
        raise OSError("no shell")

    monkeypatch.setattr(lane_mod, "run_user_bash", _boom)
    t.turn["on"] = True
    await t.lane.run(t.lane.begin("!", "x", pending=True), cwd=".")
    assert "✖ no shell" in t.shown
    t.turn["on"] = False
    await t.lane.settle()
    assert "✖ no shell" in t.commits[0]


async def test_settle_waits_for_a_record_being_written(monkeypatch: pytest.MonkeyPatch) -> None:
    # The input loop's ``settle`` right before ``set_running(True)``: a record
    # already being written finishes before the turn is marked running.
    t = _Lane()
    t.patch(monkeypatch)
    gate = asyncio.Event()
    order: list[str] = []

    async def _slow(harness: object, run: UserBashRun) -> None:
        order.append("record start")
        await gate.wait()
        order.append("record end")

    monkeypatch.setattr(lane_mod, "record_user_bash", _slow)
    writer = asyncio.create_task(t.lane.run(t.lane.begin("!", "x", state="queued"), cwd="."))
    await _wait(lambda: order == ["record start"], what="the record to start")

    async def _turn() -> None:
        await t.lane.settle(from_loop=True)
        order.append("turn starts")

    turn = asyncio.create_task(_turn())
    await asyncio.sleep(0)
    assert order == ["record start"]
    gate.set()
    await asyncio.gather(writer, turn)
    assert order == ["record start", "record end", "turn starts"]


async def test_a_command_holds_records_until_the_input_loop_settles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ADR-0287 D6: a ``!`` during a command runs at once and its record waits.
    t = _Lane()
    t.patch(monkeypatch)
    t.lane.command_running = True
    assert t.lane.busy()
    await t.lane.run(t.lane.begin("!", "during", pending=True), cwd=".")
    assert t.recorded == []
    await t.lane.settle(from_loop=True)  # the loop, between lines
    assert t.commands() == ["during"]


async def test_records_held_by_a_turn_an_extension_ran_are_written_when_it_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Codex 596-01/04: nothing in the input loop settles after a turn an
    # extension started; the lane waits for the harness itself.
    idle = asyncio.Event()

    class _Harness:
        session = object()

        @property
        def is_idle(self) -> bool:
            return idle.is_set()

        async def wait_for_idle(self) -> None:
            await idle.wait()

    t = _Lane(_Harness())
    t.patch(monkeypatch)
    await t.lane.run(t.lane.begin("!", "ext", pending=True), cwd=".")
    assert t.recorded == []
    idle.set()  # the extension's turn ends; nobody calls settle
    await _wait(lambda: t.commands() == ["ext"], what="the record after it")
    await t.lane.close()


async def test_close_records_what_finished_unless_a_turn_holds_the_harness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Codex 596-04: a record held during ``/compact`` was lost on Ctrl+D.
    t = _Lane()
    t.patch(monkeypatch)
    t.lane.command_running = True
    await t.lane.run(t.lane.begin("!", "held", pending=True), cwd=".")
    assert t.recorded == []
    await t.lane.close()
    assert t.commands() == ["held"]

    # Verify R1V1: a harness with ``wait_for_idle``, as the real one has. Closed
    # during an extension's turn, the lane left a waiter that wrote the record
    # once the teardown's dispose aborted the turn (Ctrl+D did; ``/quit`` not).
    idle = asyncio.Event()

    class _Turn:
        session = object()

        @property
        def is_idle(self) -> bool:
            return idle.is_set()

        async def wait_for_idle(self) -> None:
            await idle.wait()

    t2 = _Lane(_Turn())
    t2.patch(monkeypatch)
    await t2.lane.run(t2.lane.begin("!", "mid", pending=True), cwd=".")
    await t2.lane.close()
    idle.set()  # the teardown's dispose aborts the turn
    await asyncio.sleep(0.05)
    assert t2.recorded == []  # a turn still held the harness: not written into it


async def test_one_shell_command_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    # Verify V10 — a queued block waits for the one running.
    t = _Lane()
    t.patch(monkeypatch)
    t.gates["slow"] = asyncio.Event()
    slow = asyncio.create_task(t.lane.run(t.lane.begin("!", "slow", pending=True), cwd="."))
    await _wait(lambda: t.ran == ["slow"], what="the first to run")
    assert t.lane.running()
    waiter = asyncio.create_task(t.lane.wait_until_quiet())
    await asyncio.sleep(0.05)
    assert not waiter.done()
    t.gates["slow"].set()
    await asyncio.gather(slow, waiter)
    assert not t.lane.running()


async def test_the_pending_area_draws_each_block_and_fits_the_width(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t = _Lane()
    t.patch(monkeypatch)
    t.turn["on"] = True
    hostile = "echo \x1b]52;c;aGk=\x07 done"
    t.gates[hostile] = asyncio.Event()
    queued = t.lane.begin("!", "echo AFTER", state="queued", pending=True, after_turn=True)
    assert "! echo AFTER  (runs after the turn)" in t.shown
    running = asyncio.create_task(t.lane.run(t.lane.begin("!", hostile, pending=True), cwd="."))
    await _wait(lambda: "running…" in t.shown, what="the running line")
    # Verify V11: the command is shown sanitised, as its echo is — no OSC 52
    # reaches the terminal from the pending area.
    raw = "".join(t.rows[-1])
    assert "\x1b]" not in raw and "52;c;" not in raw and "echo  done" in t.shown
    # What ran comes first, what waits for the turn last.
    assert t.shown.index("running…") < t.shown.index("runs after the turn")
    t.gates[hostile].set()
    await running
    await t.lane.run(t.lane.begin("!", "many lines of output", pending=True), cwd=".")
    assert len(t.rows[-1]) == 7  # 2 + 4 + the queued line: not capped yet
    await t.lane.run(t.lane.begin("!", "one more", pending=True), cwd=".")
    rows = t.rows[-1]
    assert len(rows) <= _PENDING_MAX_ROWS
    for row in rows:
        plain = Text.from_ansi(row).plain
        assert get_cwidth(plain) <= 40, plain
    assert "… 1 more command above" in t.shown  # capped: the newest blocks kept
    assert "more lines, in full" in t.shown
    assert "line 5" in t.shown  # the tail of the output
    assert queued.state == "queued"


async def test_the_pending_area_cap_drops_whole_blocks_never_a_command_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Verify R1V5: five blocks of four output lines are 20 rows. Cut by rows, the
    # area showed "… 2 more lines", d3 and d4 under no command line at all.
    t = _Lane()

    async def _four(
        harness: Any, command: str, *, exclude_from_context: bool, cwd: str
    ) -> UserBashRun:
        return _run(command, output="".join(f"{command}{i}\n" for i in range(1, 5)))

    monkeypatch.setattr(lane_mod, "run_user_bash", _four)
    t.turn["on"] = True
    for name in "abcde":
        await t.lane.run(t.lane.begin("!", name, pending=True), cwd=".")
    lines = [Text.from_ansi(r).plain for r in t.rows[-1]]
    assert len(lines) <= _PENDING_MAX_ROWS
    assert lines[0].startswith("… 4 more commands above, in full")  # cut to 40 columns
    assert [line[:6] for line in lines[1:] if line.startswith("! ")] == ["! e  ("]
    for name in "abcd":
        assert not any(line.startswith(name) for line in lines[1:]), lines
    assert lines[-2:] == ["e3", "e4"]


async def test_a_queued_block_that_ran_settles_in_the_order_it_ran(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Alt+Enter queued Q, then Enter ran X: X ran first, so it lands first.
    t = _Lane()
    t.patch(monkeypatch)
    t.turn["on"] = True
    queued = t.lane.begin("!", "Q", state="queued", pending=True, after_turn=True)
    await t.lane.run(t.lane.begin("!", "X", pending=True), cwd=".")
    t.turn["on"] = False
    await t.lane.settle()
    assert t.commands() == ["X"]  # not held back by the queued one
    assert t.lane.next_after_turn() is queued
    await t.lane.run(queued, cwd=".")
    assert t.commands() == ["X", "Q"]


# === the lane over a real harness: idle_write (Codex 596-02, verify V12) ========


async def test_a_turn_started_while_a_record_writes_waits_and_chains_after_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
    from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem, Session
    from aelix_ai.messages import AssistantMessage, TextContent
    from aelix_ai.streaming import AssistantEndEvent, AssistantStartEvent, Model

    class _SlowFS(LocalFileSystem):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def append_file(self, path: str, content: str) -> None:
            if "bash_execution" in content:
                self.entered.set()
                await self.release.wait()
            await super().append_file(path, content)

    fs = _SlowFS()
    storage = await JsonlSessionStorage.create(
        fs, str(tmp_path / "s.jsonl"), cwd=str(tmp_path), session_id="race"
    )
    session = Session(storage)
    contexts: list[str] = []

    async def _stream(model: Any, ctx: Any, opts: Any) -> AsyncIterator[Any]:
        contexts.append(" ".join(str(m) for m in ctx.messages))
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="done")], stop_reason="end_turn")
        )

    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"), session=session, stream_fn=_stream
        )
    )

    async def _ran(*_a: object, **_k: object) -> UserBashRun:
        return _run("echo RECORD", output="RECORD\n")

    monkeypatch.setattr(lane_mod, "run_user_bash", _ran)
    lane = ShellLane(
        busy=lambda: False,
        current_harness=lambda: harness,
        commit=lambda _r: None,
        show=lambda _rows: None,
        theme=lambda: None,
        width=lambda: 80,
        output_text=Text,
    )
    writer = asyncio.create_task(
        lane.run(lane.begin("!", "echo RECORD", state="queued"), cwd=".")
    )
    await asyncio.wait_for(fs.entered.wait(), 5)
    # An extension's ``send_message(..., trigger_turn=True)``: a prompt task.
    turn = asyncio.create_task(harness.prompt("extension turn", source="extension"))
    await asyncio.sleep(0.05)
    assert not contexts  # the turn waits for the record instead of racing it
    fs.release.set()
    await asyncio.wait_for(asyncio.gather(writer, turn), 5)

    entries = await session.get_entries()
    record = next(e for e in entries if getattr(e, "custom_type", None) == "bash_execution")
    user = next(
        e for e in entries if getattr(getattr(e, "message", None), "role", None) == "user"
    )
    assert user.parent_id == record.id  # chained after it: no fork
    assert record.id in [e.id for e in await session.get_branch()]
    assert "RECORD" in contexts[0]  # the turn's request carries it
    await harness.dispose()


# === run_tui end to end ========================================================


class _HoldingHarness(FakeHarness):
    """A turn that runs until the test lets it end; steer is recorded."""

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()
        self.steers: list[str] = []
        self.session = object()

    async def prompt(self, text: str, *, source: str = "interactive", images: Any = None):
        self.prompts.append((text, source))
        await self.release.wait()
        return []

    async def steer(self, text: str) -> None:
        self.steers.append(text)


class _Bash:
    """``run_user_bash`` / ``record_user_bash`` doubles for ``run_tui``."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, chrome_ref: dict[str, Any]) -> None:
        self.ran: list[tuple[str, bool, bool]] = []
        self.recorded: list[tuple[str, bool]] = []
        self.gates: dict[str, asyncio.Event] = {}
        self.cancelled: list[str] = []

        async def _fake_run(
            h: object, command: str, *, exclude_from_context: bool, cwd: str
        ) -> UserBashRun:
            self.ran.append((command, exclude_from_context, chrome_ref["chrome"].running))
            gate = self.gates.get(command)
            if gate is not None:
                try:
                    await gate.wait()
                except asyncio.CancelledError:
                    self.cancelled.append(command)
                    raise
            return UserBashRun(command, f"{command} output\n", 0, exclude_from_context)

        async def _fake_record(h: object, run: UserBashRun) -> None:
            self.recorded.append((run.command, chrome_ref["chrome"].running))

        monkeypatch.setattr(lane_mod, "run_user_bash", _fake_run)
        monkeypatch.setattr(lane_mod, "record_user_bash", _fake_record)


def _pending_text(chrome: Any) -> str:
    return "\n".join(Text.from_ansi(r).plain for r in chrome._pending_rows)


def _shell_running(chrome: Any) -> bool:
    return bool(chrome.shell_running and chrome.shell_running())


async def test_bang_mid_turn_runs_now_waits_in_the_pending_area_and_reaches_the_model_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _HoldingHarness()
    chrome_ref: dict[str, Any] = {}
    bash = _Bash(monkeypatch, chrome_ref)
    bash.gates["echo MIDTURN"] = asyncio.Event()
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        chrome_ref["chrome"] = chrome
        commits = _spy_commits(chrome)
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running, what="the TUI to start")
        pipe.send_text("write a long answer\n")
        await _wait(lambda: chrome.running and bool(harness.prompts), what="the turn to start")
        before = len(commits)

        pipe.send_text("!echo MIDTURN\n")
        await _wait(lambda: len(bash.ran) == 1, what="the ! command to run during the turn")
        await _wait(
            lambda: "! echo MIDTURN  (running…)" in _pending_text(chrome),
            what="the pending area to show it running",
        )
        bash.gates["echo MIDTURN"].set()
        await _wait(
            lambda: "echo MIDTURN output" in _pending_text(chrome),
            what="its output in the pending area",
        )
        pipe.send_text("!!echo QUIET\n")
        await _wait(lambda: len(bash.ran) == 2, what="the !! command to run during the turn")
        await _wait(lambda: "echo QUIET output" in _pending_text(chrome), what="the !! output")
        await asyncio.sleep(0.1)
        assert bash.ran == [("echo MIDTURN", False, True), ("echo QUIET", True, True)]
        assert harness.steers == []  # not steered to the model as text
        assert bash.recorded == []  # held while the turn runs
        # Verify V4: nothing of a block lands among the answer's lines mid-turn.
        assert not any("MIDTURN" in c or "QUIET" in c for c in commits[before:])

        harness.release.set()
        await _wait(
            lambda: bash.recorded == [("echo MIDTURN", False)], what="the record after the turn"
        )
        await _wait(lambda: not chrome._pending_rows, what="the pending area to empty")
        moved = [c for c in commits if "MIDTURN" in c or "QUIET" in c]
        # Moved into the transcript once each, command line and output together.
        assert len(moved) == 2
        assert "! echo MIDTURN" in moved[0] and "echo MIDTURN output" in moved[0]
        assert "!! echo QUIET" in moved[1]
        pipe.send_text("/quit\n")
        await _quit_within(task)
    assert harness.prompts == [("write a long answer", "interactive")]
    assert bash.recorded == [("echo MIDTURN", False)]  # the !! one never


async def test_alt_enter_on_a_bang_mid_turn_runs_it_after_the_turn_as_an_idle_bang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _HoldingHarness()
    chrome_ref: dict[str, Any] = {}
    bash = _Bash(monkeypatch, chrome_ref)
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        chrome_ref["chrome"] = chrome
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running, what="the TUI to start")
        pipe.send_text("answer\n")
        await _wait(lambda: chrome.running, what="the turn to start")
        pipe.send_text("!echo AFTER\x1b\r")  # Alt+Enter
        await _wait(
            lambda: "! echo AFTER  (runs after the turn)" in _pending_text(chrome),
            what="the queued shell follow-up",
        )
        await asyncio.sleep(0.1)
        assert bash.ran == []  # not now
        assert harness.steers == []
        harness.release.set()
        await _wait(
            lambda: bash.ran == [("echo AFTER", False, False)], what="it to run after the turn"
        )
        # Idle semantics once the turn is over: recorded at once.
        await _wait(lambda: bash.recorded == [("echo AFTER", False)], what="its record")
        await _wait(lambda: not chrome._pending_rows, what="the pending area to empty")
        pipe.send_text("/quit\n")
        await _quit_within(task)
    assert harness.prompts == [("answer", "interactive")]  # never sent to the model


async def test_a_second_bang_is_refused_while_one_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _HoldingHarness()
    chrome_ref: dict[str, Any] = {}
    bash = _Bash(monkeypatch, chrome_ref)
    bash.gates["sleep 9"] = asyncio.Event()
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        chrome_ref["chrome"] = chrome
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running, what="the TUI to start")
        pipe.send_text("answer\n")
        await _wait(lambda: chrome.running, what="the turn to start")
        pipe.send_text("!sleep 9\n")
        await _wait(lambda: len(bash.ran) == 1, what="the first to run")
        pipe.send_text("!echo TWO\n")
        await _wait(lambda: chrome.buffer.text == "!echo TWO", what="the second typed")
        await asyncio.sleep(0.2)
        assert len(bash.ran) == 1 and chrome.buffer.text == "!echo TWO"  # refused, kept
        bash.gates["sleep 9"].set()
        await _wait(lambda: not _shell_running(chrome), what="the first to end")
        pipe.send_text("\n")
        await _wait(lambda: len(bash.ran) == 2, what="the second to run now")
        harness.release.set()
        pipe.send_text("/quit\n")
        await _quit_within(task)


class _PhaseHarness(_HoldingHarness):
    """``is_idle`` / ``wait_for_idle`` and a ``compact`` that holds the harness."""

    def __init__(self) -> None:
        super().__init__()
        self._idle = asyncio.Event()
        self._idle.set()
        self.compact_gate = asyncio.Event()

    @property
    def is_idle(self) -> bool:
        return self._idle.is_set()

    async def wait_for_idle(self) -> None:
        await self._idle.wait()

    def hold(self) -> None:
        self._idle.clear()

    def let_go(self) -> None:
        self._idle.set()

    async def compact(self, instructions: str | None = None) -> Any:
        self.hold()
        try:
            await self.compact_gate.wait()
        finally:
            self.let_go()
        return SimpleNamespace(tokens_before=10, summary="")


async def test_a_bang_during_a_turn_an_extension_runs_is_held_until_it_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Codex 596-01: ``chrome.running`` stayed False, the line took the idle path
    # and the record landed between the extension turn's tool call and result.
    harness = _PhaseHarness()
    chrome_ref: dict[str, Any] = {}
    bash = _Bash(monkeypatch, chrome_ref)
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        chrome_ref["chrome"] = chrome
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running, what="the TUI to start")
        harness.hold()  # send_message(..., trigger_turn=True) started a turn
        pipe.send_text("!echo EXTENSION_RECORD\n")
        await _wait(lambda: len(bash.ran) == 1, what="the ! to run at once")
        await _wait(
            lambda: "EXTENSION_RECORD output" in _pending_text(chrome), what="its pending block"
        )
        await asyncio.sleep(0.1)
        assert bash.recorded == []
        harness.let_go()  # the extension's turn ends; the input loop is idle
        await _wait(
            lambda: bash.recorded == [("echo EXTENSION_RECORD", False)], what="the record"
        )
        pipe.send_text("/quit\n")
        await _quit_within(task)


async def test_a_bang_during_compact_runs_at_once_and_survives_ctrl_d(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Codex 596-05: it waited behind ``/compact``. Codex 596-04: held through
    # it, the record was lost when Ctrl+D came next.
    harness = _PhaseHarness()
    chrome_ref: dict[str, Any] = {}
    bash = _Bash(monkeypatch, chrome_ref)
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        chrome_ref["chrome"] = chrome
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running, what="the TUI to start")
        pipe.send_text("/compact\n")
        await _wait(lambda: not harness.is_idle, what="the compaction to start")
        pipe.send_text("!echo NOW\n")
        await _wait(lambda: len(bash.ran) == 1, what="the ! to run during /compact")
        assert not harness.compact_gate.is_set()
        await asyncio.sleep(0.1)
        assert bash.recorded == []
        harness.compact_gate.set()
        await _wait(lambda: bash.recorded == [("echo NOW", False)], what="the record after it")
        pipe.send_text("\x04")  # Ctrl+D
        await _quit_within(task)
    assert bash.recorded == [("echo NOW", False)]


@pytest.mark.parametrize("exit_key", ["\x04", "/quit\n"], ids=["ctrl-d", "quit"])
async def test_exit_during_an_extension_turn_does_not_record_what_it_held(
    monkeypatch: pytest.MonkeyPatch, exit_key: str
) -> None:
    # Verify R1V1: Ctrl+D recorded the block once the teardown's dispose ended
    # the extension's turn; ``/quit`` did not. ADR-0287 Limits: neither does.
    harness = _PhaseHarness()
    chrome_ref: dict[str, Any] = {}
    bash = _Bash(monkeypatch, chrome_ref)
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        chrome_ref["chrome"] = chrome

        async def _dispose() -> None:
            runtime.disposed += 1
            harness.let_go()  # the real dispose aborts the turn and waits for it

        monkeypatch.setattr(runtime, "dispose", _dispose)
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running, what="the TUI to start")
        harness.hold()  # send_message(..., trigger_turn=True) started a turn
        pipe.send_text("!echo EXIT_HELD\n")
        await _wait(lambda: "EXIT_HELD output" in _pending_text(chrome), what="its pending block")
        pipe.send_text(exit_key)
        await _quit_within(task)
        await asyncio.sleep(0.1)
    assert runtime.disposed == 1 and harness.is_idle
    assert bash.recorded == []


async def test_a_bang_still_running_when_the_tui_closes_is_stopped_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # What the exit cancel is for (the verify measured that removing it changed
    # nothing it looked at): the task is cancelled, so it neither prints nor
    # records into a session being torn down, and the bash operations kill its
    # process tree.
    harness = _HoldingHarness()
    chrome_ref: dict[str, Any] = {}
    bash = _Bash(monkeypatch, chrome_ref)
    bash.gates["sleep 297"] = asyncio.Event()
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        chrome_ref["chrome"] = chrome
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running, what="the TUI to start")
        pipe.send_text("answer\n")
        await _wait(lambda: chrome.running, what="the turn to start")
        pipe.send_text("!sleep 297\n")
        await _wait(lambda: len(bash.ran) == 1, what="the ! to run")
        harness.release.set()
        await _wait(lambda: not chrome.running, what="the turn to end")
        pipe.send_text("/quit\n")
        await _quit_within(task)
    assert bash.cancelled == ["sleep 297"]
    assert bash.recorded == []


async def test_a_paste_that_begins_with_a_bang_reaches_the_model_as_text_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Verify V1, idle form: the input loop parsed the EXPANDED line and ran it.
    harness = _HoldingHarness()
    harness.release.set()
    chrome_ref: dict[str, Any] = {}
    bash = _Bash(monkeypatch, chrome_ref)
    body = "!echo PASTED\n" + "\n".join(f"echo l{i}" for i in range(6))
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        chrome_ref["chrome"] = chrome
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running, what="the TUI to start")
        pipe.send_text(f"\x1b[200~{body}\x1b[201~")
        await _wait(lambda: "[Pasted text" in chrome.buffer.text, what="the paste to collapse")
        pipe.send_text("\n")
        await _wait(lambda: bool(harness.prompts), what="the prompt")
        pipe.send_text("/quit\n")
        await _quit_within(task)
    assert harness.prompts == [(body, "interactive")]
    assert bash.ran == []


async def test_a_late_steer_of_a_bang_text_is_a_prompt_not_a_shell_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The steer re-route (the turn ended before the steer task ran) used to
    # re-read the text; a steered paste that begins with ``!`` would run.
    harness = _HoldingHarness()
    harness.release.set()
    chrome_ref: dict[str, Any] = {}
    bash = _Bash(monkeypatch, chrome_ref)
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        chrome_ref["chrome"] = chrome
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running, what="the TUI to start")
        assert chrome.on_steer is not None and not chrome.running
        chrome.on_steer("!echo STEERED")
        await _wait(lambda: bool(harness.prompts), what="the re-routed prompt")
        pipe.send_text("/quit\n")
        await _quit_within(task)
    assert harness.prompts == [("!echo STEERED", "interactive")]
    assert bash.ran == []


async def test_an_idle_bang_whose_run_outlives_the_start_of_a_turn_waits_for_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Codex 596-01, the idle half: the input loop's own ``!`` wrote its record
    # straight away, so a turn an extension started while it ran got it
    # between its tool call and its result. It goes through the lane now.
    harness = _PhaseHarness()
    chrome_ref: dict[str, Any] = {}
    bash = _Bash(monkeypatch, chrome_ref)
    bash.gates["sleep 2; echo IDLE_SLOW"] = asyncio.Event()
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        chrome_ref["chrome"] = chrome
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running, what="the TUI to start")
        pipe.send_text("!sleep 2; echo IDLE_SLOW\n")  # idle: the input loop runs it
        await _wait(lambda: len(bash.ran) == 1, what="the idle ! to run")
        harness.hold()  # an extension's turn starts meanwhile
        bash.gates["sleep 2; echo IDLE_SLOW"].set()
        await _wait(lambda: "IDLE_SLOW output" in _pending_text(chrome), what="held, shown")
        await asyncio.sleep(0.1)
        assert bash.recorded == []
        harness.let_go()
        await _wait(
            lambda: bash.recorded == [("sleep 2; echo IDLE_SLOW", False)], what="the record"
        )
        pipe.send_text("/quit\n")
        await _quit_within(task)


class _QuietCompactHarness(_HoldingHarness):
    """A ``/compact`` whose harness stays idle: only the input loop knows it runs."""

    def __init__(self) -> None:
        super().__init__()
        self.is_idle = True
        self.compact_gate = asyncio.Event()
        self.compacting = False

    async def compact(self, instructions: str | None = None) -> Any:
        self.compacting = True
        await self.compact_gate.wait()
        return SimpleNamespace(tokens_before=10, summary="")


async def test_any_command_the_input_loop_runs_holds_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ADR-0287 D6 — "or any running command": a command that does not touch the
    # harness's phase still makes a ``!`` run at once and hold its record.
    harness = _QuietCompactHarness()
    chrome_ref: dict[str, Any] = {}
    bash = _Bash(monkeypatch, chrome_ref)
    async with _harness_chrome(harness=harness) as (runtime, chrome, pipe):
        chrome_ref["chrome"] = chrome
        task = _launch(runtime, chrome)
        await _wait(lambda: chrome.app.is_running, what="the TUI to start")
        pipe.send_text("/compact\n")
        await _wait(lambda: harness.compacting, what="the command to start")
        pipe.send_text("!echo DURING_COMMAND\n")
        await _wait(lambda: len(bash.ran) == 1, what="the ! to run during the command")
        await asyncio.sleep(0.1)
        assert bash.recorded == []
        harness.compact_gate.set()
        await _wait(
            lambda: bash.recorded == [("echo DURING_COMMAND", False)], what="the record after it"
        )
        pipe.send_text("/quit\n")
        await _quit_within(task)
