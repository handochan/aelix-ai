"""#347 — Esc and Ctrl+C stop a compaction, and the working row's hint is true.

A real :class:`AgentHarness` behind the headless chrome, the summary held until it is
cancelled: nobody releases it, so a test can only pass through the key. The driver for
the real terminal is in ``.omc/probes/compact-esc/``.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarnessError, NavigateTreeOptions
from aelix_agent_core.session import CompactionPreparation, CompactResult
from aelix_ai.messages import TextContent, UserMessage
from aelix_ai.streaming import Model
from aelix_coding_agent.tui.commands import (
    BUILTIN_COMMANDS,
    CommandContext,
    CommandInterrupted,
    match_command,
)
from aelix_coding_agent.tui.render import INPUT_RETURNED_NOTICE

from tests.rpc.test_runtime_control_lifecycle_594 import _commands
from tests.tui.test_esc_gives_input_back_654 import _Provider, _Tui
from tests.tui.test_interrupt_reaches_long_work_604 import _capture_signal_handlers
from tests.tui.test_run_tui_smoke import _quit_within, _wait

_ESC = "\x1b"
_CTRL_C = "\x03"
_LINE = "Compaction cancelled."


class _Summary:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.cancelled = 0
        self.hold = True

    async def __call__(self, _model: Any, prep: CompactionPreparation, _ci: Any) -> CompactResult:
        self.entered.set()
        if self.hold:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
        return CompactResult(
            summary="SUMMARY", first_kept_entry_id=prep.first_kept_entry_id, tokens_before=99
        )


async def _fill(tui: _Tui, *, automatic: bool = False) -> None:
    session = tui.harness.session
    assert session is not None
    for _ in range(6):
        await session.append_message(UserMessage(content=[TextContent(text="x" * 30_000)]))
    if automatic:
        tui.harness.state.auto_compaction_enabled = True
        tui.harness.state.model = Model(
            id="m", provider="p", api="unknown", context_window=200_000, max_tokens=4096
        )


async def _entries(tui: _Tui) -> int:
    session = tui.harness.session
    assert session is not None
    return len([e for e in await session.get_entries() if e.type == "compaction"])


def _row(tui: _Tui) -> str:
    return tui.chrome._render_working()


# === /compact ===============================================================


@pytest.mark.parametrize("key", [_ESC, _CTRL_C], ids=["esc", "ctrl-c"])
async def test_a_key_stops_a_manual_compaction_and_the_next_prompt_works(key: str) -> None:
    summary = _Summary()
    provider = _Provider()
    async with _Tui(provider, _summarizer_override=summary) as tui:
        await _fill(tui)
        idle_interrupt = tui.chrome.on_interrupt

        tui.pipe.send_text("/compact\n")
        await asyncio.wait_for(summary.entered.wait(), timeout=10)
        await _wait(lambda: "Compacting context…" in _row(tui))
        assert "esc to cancel" in _row(tui), "the row says what the key does"
        assert tui.chrome.running

        tui.pipe.send_text(key)
        await _wait(lambda: summary.cancelled == 1)
        await _wait(lambda: _LINE in tui.commits)
        await _wait(tui.idle)

        assert tui.commits.count(_LINE) == 1
        assert not [c for c in tui.commits if "compact failed" in c or "✖" in c]
        assert _row(tui) == "", "the row went with the compaction"
        assert tui.chrome.on_interrupt is idle_interrupt
        assert await _entries(tui) == 0

        # What the second key meets is an idle input: it does nothing to the harness.
        tui.pipe.send_text(_ESC)
        await asyncio.sleep(1.2)  # a lone Esc waits this long to be no Alt+ prefix (#460)
        await _wait(tui.idle)
        assert tui.commits.count(_LINE) == 1

        tui.pipe.send_text("the next line\n")
        await _wait(lambda: bool(provider.seen))
        await _wait(tui.idle)
        assert provider.seen[-1][-1] == "the next line"
        assert not tui.harness._abort_requested


async def test_a_key_pressed_while_the_compaction_unwinds_is_harmless() -> None:
    summary = _Summary()
    async with _Tui(_Provider(), _summarizer_override=summary) as tui:
        await _fill(tui)
        tui.pipe.send_text("/compact\n")
        await asyncio.wait_for(summary.entered.wait(), timeout=10)
        await _wait(lambda: "esc to cancel" in _row(tui))
        tui.pipe.send_text(_CTRL_C)
        tui.pipe.send_text(_CTRL_C)
        await _wait(lambda: _LINE in tui.commits)
        await _wait(tui.idle)
        assert tui.commits.count(_LINE) == 1
        assert await _entries(tui) == 0


async def test_a_compaction_nobody_stops_prints_its_result_and_says_no_such_thing() -> None:
    summary = _Summary()
    summary.hold = False
    async with _Tui(_Provider(), _summarizer_override=summary) as tui:
        await _fill(tui)
        tui.pipe.send_text("/compact\n")
        await asyncio.wait_for(summary.entered.wait(), timeout=10)
        await _wait(lambda: any("SUMMARY" in c for c in tui.commits))
        await _wait(tui.idle)
        assert _LINE not in tui.commits
        assert await _entries(tui) == 1
        assert _row(tui) == ""


async def test_a_key_after_the_entry_is_written_says_what_happened() -> None:
    """The entry stands, and the hook after the write is not cut: the line must not say
    "cancelled" over a compaction that was saved. A cancel of the handler's task did."""

    summary = _Summary()
    summary.hold = False
    async with _Tui(_Provider(), _summarizer_override=summary) as tui:
        await _fill(tui)
        entered = asyncio.Event()
        release = asyncio.Event()
        cancelled: list[bool] = []

        async def hook(_event: Any, _ctx: Any) -> None:
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

        tui.harness.hooks.on("session_compact", hook)
        tui.pipe.send_text("/compact\n")
        await asyncio.wait_for(entered.wait(), timeout=10)
        await _wait(lambda: "esc to cancel" in _row(tui))
        tui.pipe.send_text(_CTRL_C)
        await asyncio.sleep(0.05)
        assert cancelled == []
        release.set()
        await _wait(lambda: any("SUMMARY" in c for c in tui.commits))
        await _wait(tui.idle)
        assert _LINE not in tui.commits
        assert await _entries(tui) == 1
        assert _row(tui) == ""


async def test_a_line_entered_during_compact_is_the_next_input_as_it_was() -> None:
    """The TUI reads as running, and no turn is there to steer."""

    summary = _Summary()
    provider = _Provider()
    async with _Tui(provider, _summarizer_override=summary) as tui:
        await _fill(tui)
        tui.pipe.send_text("/compact\n")
        await asyncio.wait_for(summary.entered.wait(), timeout=10)
        await _wait(lambda: "esc to cancel" in _row(tui))
        tui.pipe.send_text("typed meanwhile\n")
        await asyncio.sleep(0.05)
        assert tui.harness._steering_queue._messages == []
        tui.pipe.send_text(_ESC)
        await _wait(lambda: _LINE in tui.commits)
        await _wait(lambda: bool(provider.seen))
        await _wait(tui.idle)
        assert provider.seen[-1][-1] == "typed meanwhile"


@pytest.mark.skipif(sys.platform == "win32", reason="run_tui installs no SIGTERM handler on win32")
async def test_a_signal_during_compact_stops_it_and_ends_the_tui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handlers = _capture_signal_handlers(monkeypatch)
    summary = _Summary()
    async with _Tui(_Provider(), signals=True, _summarizer_override=summary) as tui:
        await _fill(tui)
        await _wait(lambda: signal.SIGTERM in handlers)
        tui.pipe.send_text("/compact\n")
        await asyncio.wait_for(summary.entered.wait(), timeout=10)
        await _wait(lambda: tui.chrome.running)

        handlers[signal.SIGTERM]()

        await _wait(lambda: summary.cancelled == 1)
        assert await _quit_within(tui.task) == 0
        assert await _entries(tui) == 0


@pytest.mark.skipif(sys.platform == "win32", reason="run_tui installs no SIGTERM handler on win32")
async def test_a_signal_drops_a_line_entered_during_compact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The line was the only thing left between the signal and the exit, and it sent a request."""

    handlers = _capture_signal_handlers(monkeypatch)
    summary = _Summary()
    provider = _Provider()
    async with _Tui(provider, signals=True, _summarizer_override=summary) as tui:
        await _fill(tui)
        await _wait(lambda: signal.SIGTERM in handlers)
        requests = len(provider.seen)
        tui.pipe.send_text("/compact\n")
        await asyncio.wait_for(summary.entered.wait(), timeout=10)
        await _wait(lambda: tui.chrome.running)
        tui.pipe.send_text("typed meanwhile\n")
        await _wait(lambda: tui.chrome._input_queue.qsize() == 1)

        handlers[signal.SIGTERM]()

        assert await _quit_within(tui.task) == 0
        assert len(provider.seen) == requests
        assert await _entries(tui) == 0


class _HoldsTheFirstCancel(_Summary):
    """A way out that parks: the summary eats the first cancel and waits again."""

    async def __call__(self, _model: Any, prep: CompactionPreparation, _ci: Any) -> CompactResult:
        self.entered.set()
        for _ in range(2):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled += 1
        raise asyncio.CancelledError


@pytest.mark.skipif(sys.platform == "win32", reason="run_tui installs no SIGTERM handler on win32")
async def test_a_signal_during_compact_aborts_again_while_it_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nobody is there to press the key twice: a ``/compact``'s interrupt is one abort, and a
    signal goes the turn's way (ADR-0290)."""

    handlers = _capture_signal_handlers(monkeypatch)
    summary = _HoldsTheFirstCancel()
    async with _Tui(_Provider(), signals=True, _summarizer_override=summary) as tui:
        await _fill(tui)
        await _wait(lambda: signal.SIGTERM in handlers)
        tui.pipe.send_text("/compact\n")
        await asyncio.wait_for(summary.entered.wait(), timeout=10)
        await _wait(lambda: tui.chrome.running)

        handlers[signal.SIGTERM]()

        assert await _quit_within(tui.task) == 0
        assert summary.cancelled == 2
        assert await _entries(tui) == 0


@pytest.mark.skipif(sys.platform == "win32", reason="run_tui installs no SIGTERM handler on win32")
@pytest.mark.parametrize("command", ["navigate", "compact-and-wait"])
async def test_a_signal_during_an_extension_commands_compaction_stops_it(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    """The command holds the input loop with the chrome idle: no key reaches it and the
    EOF waits behind it, so only the signal's own abort can end this."""

    handlers = _capture_signal_handlers(monkeypatch)
    summary = _Summary()

    async def branch_summary(*_args: Any, **_kwargs: Any) -> str:
        summary.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            summary.cancelled += 1
            raise
        return "unreachable"

    async def navigate(_args: str, ctx: Any) -> None:
        session = tui.harness.session
        assert session is not None
        entries = await session.get_entries()
        await ctx.navigate_tree(entries[0].id, NavigateTreeOptions(summarize=True))

    async def compact_and_wait(_args: str, ctx: Any) -> None:
        ctx.compact()
        await asyncio.sleep(0)
        await ctx.wait_for_idle()

    extension = _commands(**{"navigate": navigate, "compact-and-wait": compact_and_wait})
    async with _Tui(
        _Provider(),
        signals=True,
        extensions=[extension],
        _summarizer_override=summary,
        _branch_summarizer_override=branch_summary,
    ) as tui:
        await _fill(tui)
        await _wait(lambda: signal.SIGTERM in handlers)
        tui.pipe.send_text(f"/{command}\n")
        await asyncio.wait_for(summary.entered.wait(), timeout=10)
        assert not tui.chrome.running
        assert tui.harness.phase != "idle"

        handlers[signal.SIGTERM]()

        await _wait(lambda: summary.cancelled == 1)
        assert await _quit_within(tui.task) == 0
        assert await _entries(tui) == 0


async def test_a_key_during_a_session_before_compact_hook_counts_when_it_returns() -> None:
    """The hook is not cut, the row stays while it holds (ADR-0023's limit 1), and no
    request goes out after it."""

    summary = _Summary()
    async with _Tui(_Provider(), _summarizer_override=summary) as tui:
        await _fill(tui)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def hook(_event: Any, _ctx: Any) -> None:
            entered.set()
            await release.wait()

        tui.harness.hooks.on("session_before_compact", hook)
        tui.pipe.send_text("/compact\n")
        await asyncio.wait_for(entered.wait(), timeout=10)
        await _wait(lambda: "esc to cancel" in _row(tui))
        tui.pipe.send_text(_CTRL_C)
        await _wait(lambda: tui.harness._abort_requested)
        await asyncio.sleep(0.05)
        assert "esc to cancel" in _row(tui)
        assert _LINE not in tui.commits

        release.set()
        await _wait(lambda: _LINE in tui.commits)
        await _wait(tui.idle)
        assert not summary.entered.is_set()
        assert await _entries(tui) == 0
        assert _row(tui) == ""


# === A compaction inside a turn =============================================


@pytest.mark.parametrize("key", [_ESC, _CTRL_C], ids=["esc", "ctrl-c"])
async def test_a_key_stops_an_overflow_summary_and_the_input_comes_back(key: str) -> None:
    summary = _Summary()
    provider = _Provider("overflow")
    async with _Tui(provider, _summarizer_override=summary) as tui:
        await _fill(tui, automatic=True)
        tui.pipe.send_text("the prompt\n")
        await asyncio.wait_for(summary.entered.wait(), timeout=10)
        await _wait(lambda: "Compacting context…" in _row(tui))
        assert "esc to cancel" in _row(tui)

        tui.pipe.send_text(key)
        await _wait(lambda: summary.cancelled == 1)
        await tui.editor_reads("the prompt")
        await _wait(lambda: INPUT_RETURNED_NOTICE in tui.commits)
        await _wait(tui.idle)

        # The second press finds the input it gave back, and leaves it (ADR-0286 §6).
        tui.pipe.send_text(key)
        await asyncio.sleep(1.2 if key == _ESC else 0.05)
        assert tui.chrome.get_editor_text() == "the prompt"
        assert tui.task.done() is False

        assert "Auto-compaction cancelled" in tui.commits
        assert tui.commits.count(INPUT_RETURNED_NOTICE) == 1
        assert _row(tui) == ""
        assert await _entries(tui) == 0
        assert len(provider.seen) == 1, "no re-run"

        tui.chrome.set_editor_text("")
        tui.pipe.send_text("the next line\n")
        await _wait(lambda: len(provider.seen) == 2)
        await _wait(tui.idle)
        assert provider.seen[-1][-1] == "the next line"
        assert "the prompt" not in provider.seen[-1]


@pytest.mark.parametrize("key", [_ESC, _CTRL_C], ids=["esc", "ctrl-c"])
async def test_a_key_stops_a_threshold_summary_and_the_answer_stays(key: str) -> None:
    summary = _Summary()

    class _Big(_Provider):
        async def __call__(self, _model: Any, context: Any, _options: Any) -> Any:
            from aelix_ai.messages import AssistantMessage
            from aelix_ai.streaming import AssistantEndEvent

            self.seen.append([])
            yield AssistantEndEvent(
                message=AssistantMessage(
                    content=[TextContent(text="the answer")],
                    usage={"input": 199_000, "output": 10},
                )
            )

    async with _Tui(_Big(), _summarizer_override=summary) as tui:
        await _fill(tui, automatic=True)
        tui.pipe.send_text("the prompt\n")
        await asyncio.wait_for(summary.entered.wait(), timeout=10)
        await _wait(lambda: "Compacting context…" in _row(tui))

        tui.pipe.send_text(key)
        await _wait(lambda: summary.cancelled == 1)
        await _wait(lambda: "Auto-compaction cancelled" in tui.commits)
        await _wait(tui.idle)

        assert tui.commits.count(INPUT_RETURNED_NOTICE) == 0
        assert tui.chrome.get_editor_text() == ""
        assert await _entries(tui) == 0
        assert [getattr(m, "role", "") for m in tui.harness.messages][-2:] == ["user", "assistant"]


# === The handler ============================================================


def _handler() -> Any:
    command = match_command("/compact", list(BUILTIN_COMMANDS))
    assert command is not None and command.handler is not None
    return command.handler


class _Raises:
    def __init__(self, raises: BaseException) -> None:
        self.raises = raises

    async def compact(self, _instructions: str | None = None) -> Any:
        raise self.raises


def _ctx(harness: Any, committed: list[Any], run_cancellable: Any = None) -> CommandContext:
    return CommandContext(
        chrome=None,  # type: ignore[arg-type] — the handler never touches the chrome
        harness=harness,
        commit=committed.append,
        cwd=".",
        commands=list(BUILTIN_COMMANDS),
        run_cancellable=run_cancellable,
    )


async def test_the_handler_says_the_same_line_for_each_way_a_stop_reaches_it() -> None:
    async def interrupted(work: Any, *, abort: bool = False) -> Any:
        assert abort is True, "a /compact is what the harness's abort reaches"
        work.close()
        raise CommandInterrupted

    for harness, runner in (
        (_Raises(AgentHarnessError("compaction", "Compaction cancelled")), None),
        (_Raises(AgentHarnessError("compaction", "Compaction cancelled")), interrupted),
    ):
        committed: list[Any] = []
        ctx = _ctx(harness, committed, runner)
        await _handler()(ctx, "")
        assert [(str(c), c.style) for c in committed] == [(_LINE, "yellow")]


@pytest.mark.parametrize(
    ("raised", "shown"),
    [
        (AgentHarnessError("compaction", "not now"), "✖ compact failed: not now"),
        (AgentHarnessError("hook", "Compaction cancelled"), "✖ compact failed: Compaction cancelled"),
        (AgentHarnessError("compaction", "Error code: 502"), "✖ compact failed: Error code: 502"),
    ],
    ids=["a-hooks-reason", "another-code", "a-failure"],
)
async def test_a_hooks_reason_and_a_failure_stay_red(raised: Exception, shown: str) -> None:
    committed: list[Any] = []
    await _handler()(_ctx(_Raises(raised), committed), "")
    assert [(str(c), c.style) for c in committed] == [(shown, "bold red")]
