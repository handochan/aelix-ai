"""#668 — a steering / follow-up echo lands where the turn hands the line to the model.

Enter during a turn steers, Alt+Enter queues a follow-up (ADR-0119). The echo was
committed at Enter: in the middle of a streamed answer (between the rows already
committed and the live tail) or above a short one (committed whole at its end),
while the model got the line, and the session recorded it, after the answer.
The owner's decision (a), pi's: the line waits in the pending area and its echo
is committed on the user ``message_start`` the loop emits when it takes the line
from the queue — after the answer, after a tool's result.

A real :class:`AgentHarness` (queue, loop, events) behind the headless chrome,
with a provider the test steps through.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from _pyte import render_chrome_to_screen  # sibling helper (pytest prepend import mode)
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_ai.messages import AssistantMessage, TextContent, ToolCallContent, UserMessage
from aelix_ai.streaming import (
    AssistantDoneEvent,
    AssistantStartEvent,
    Model,
    SimpleStreamOptions,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ToolCallEndEvent,
)
from aelix_coding_agent.tui.chrome import AelixChrome, _plain
from aelix_coding_agent.tui.input import ParsedInput
from aelix_coding_agent.tui.input_marks import SubmittedLine
from aelix_coding_agent.tui.queued_messages import _QUEUED_MIN_ROWS, QueuedMessages
from aelix_coding_agent.tui.shell import run_tui
from aelix_coding_agent.tui.shell_lane import _PENDING_MAX_ROWS, ShellLane
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.text import Text

from tests.tui.test_run_tui_smoke import FakeRuntime, _quit_within, _spy_commits, _wait

_ALT_ENTER = "\x1b\r"
_ALT_UP = "\x1b[1;3A"
_CTRL_C = "\x03"


class _Provider:
    """Each request is a list of steps: ``("text", s)`` streams a paragraph,
    ``("gate", name)`` waits for the test, ``("tool", name)`` ends with a tool call."""

    def __init__(self, *requests: list[tuple[str, str]]) -> None:
        self.requests = list(requests)
        self.gates: dict[str, asyncio.Event] = {}
        self.at: dict[str, asyncio.Event] = {}
        self.seen: list[list[str]] = []

    def gate(self, name: str) -> asyncio.Event:
        return self.gates.setdefault(name, asyncio.Event())

    def reached(self, name: str) -> asyncio.Event:
        return self.at.setdefault(name, asyncio.Event())

    async def __call__(
        self, _model: Model, context: Any, _options: SimpleStreamOptions
    ) -> AsyncIterator[Any]:
        self.seen.append(
            [
                block.text
                for message in context.messages
                if isinstance(message, UserMessage)
                for block in message.content
                if isinstance(block, TextContent)
            ]
        )
        steps = self.requests.pop(0) if self.requests else [("text", "done")]
        yield AssistantStartEvent()
        text = ""
        started = False
        for kind, value in steps:
            if kind == "gate":
                self.reached(value).set()
                await self.gate(value).wait()
            elif kind == "text":
                if not started:
                    yield TextStartEvent()
                    started = True
                text += value
                yield TextDeltaEvent(delta=value)
            elif kind == "tool":
                if started:
                    yield TextEndEvent(content=text)
                call = ToolCallContent(tool_call_id="call_1", tool_name=value, input={})
                yield ToolCallEndEvent(tool_call=call)
                yield AssistantDoneEvent(
                    reason="toolUse",
                    message=AssistantMessage(
                        content=[TextContent(text=text), call] if text else [call],
                        stop_reason="tool_use",
                    ),
                )
                return
        if started:
            yield TextEndEvent(content=text)
        yield AssistantDoneEvent(
            reason="stop", message=AssistantMessage(content=[TextContent(text=text)])
        )


def _paragraphs(first: int, last: int) -> str:
    return "".join(f"Line {i:02d} of the streamed answer.\n\n" for i in range(first, last + 1))


class _Tui:
    def __init__(self, provider: _Provider) -> None:
        self.provider = provider
        self.harness = AgentHarness(
            AgentHarnessOptions(
                stream_fn=provider,
                session=Session(MemorySessionStorage()),
                model=Model(id="mock", provider="mock", api="mock"),
            )
        )
        self.harness.state.auto_retry_enabled = False
        self.harness.state.auto_compaction_enabled = False

    async def __aenter__(self) -> _Tui:
        self._pipe_cm = create_pipe_input()
        self.pipe = self._pipe_cm.__enter__()
        self._session_cm = create_app_session(input=self.pipe, output=DummyOutput())
        self._session_cm.__enter__()
        self.chrome = AelixChrome()
        self.commits = _spy_commits(self.chrome)
        self.task = asyncio.ensure_future(
            run_tui(
                FakeRuntime(self.harness),  # type: ignore[arg-type]
                cwd=".",
                chrome=self.chrome,
                install_signal_handlers=False,
            )
        )
        await _wait(lambda: self.chrome.app.is_running)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        try:
            for gate in self.provider.gates.values():
                gate.set()
            await _wait(lambda: not self.chrome.running)
            self.chrome.set_editor_text("")
            self.pipe.send_text("/quit\n")
            with contextlib.suppress(BaseException):
                await _quit_within(self.task)
        finally:
            if not self.task.done():
                self.task.cancel()
                with contextlib.suppress(BaseException):
                    await self.task
            self._session_cm.__exit__(None, None, None)
            self._pipe_cm.__exit__(None, None, None)

    async def at(self, gate: str) -> None:
        await asyncio.wait_for(self.provider.reached(gate).wait(), timeout=10)

    def pending(self) -> str:
        return "\n".join(Text.from_ansi(r).plain for r in self.chrome._pending_rows)

    def index(self, needle: str) -> int:
        hits = [i for i, c in enumerate(self.commits) if needle in c]
        assert len(hits) == 1, (needle, self.commits)
        return hits[0]

    def idle(self) -> bool:
        return self.harness.phase == "idle" and not self.chrome.running


async def test_a_steer_lands_after_the_answer_it_was_typed_under() -> None:
    # The short answer (the issue's second measurement): the echo was committed
    # above it, since the answer is committed whole when it ends.
    provider = _Provider(
        [("text", _paragraphs(1, 2)), ("gate", "mid"), ("text", _paragraphs(3, 6))],
        [("text", "reply 2")],
    )
    async with _Tui(provider) as tui:
        tui.pipe.send_text("T1 go\n")
        await tui.at("mid")
        tui.pipe.send_text("STEER_ME please\n")
        await _wait(lambda: len(tui.harness._steering_queue._messages) == 1)
        # Queued, not delivered: in the pending area, not in the transcript.
        await _wait(lambda: "Steering: STEER_ME please" in tui.pending())
        assert not any("STEER_ME" in c for c in tui.commits)
        provider.gate("mid").set()
        await _wait(tui.idle)
        await _wait(lambda: any("reply 2" in c for c in tui.commits))
    assert tui.index("» T1 go") < tui.index("Line 06") < tui.index("Steering: STEER_ME please")
    assert tui.index("Steering: STEER_ME please") < tui.index("reply 2")
    assert provider.seen[1] == ["T1 go", "STEER_ME please"]
    assert tui.pending() == ""


async def test_a_steer_lands_after_the_rows_a_long_answer_already_committed() -> None:
    # The long answer: rows past the live window are committed while it streams,
    # and the echo was committed between them and the tail.
    first = _paragraphs(1, 30)
    provider = _Provider(
        [("text", first), ("gate", "mid"), ("text", _paragraphs(31, 40))],
        [("text", "reply 2")],
    )
    async with _Tui(provider) as tui:
        tui.pipe.send_text("T1 go\n")
        await tui.at("mid")
        await _wait(lambda: any("Line 01" in c for c in tui.commits))  # committed mid-stream
        tui.pipe.send_text("STEER_ME please\n")
        await _wait(lambda: "Steering: STEER_ME please" in tui.pending())
        provider.gate("mid").set()
        await _wait(tui.idle)
        await _wait(lambda: any("reply 2" in c for c in tui.commits))
    steer = tui.index("Steering: STEER_ME please")
    assert all(i < steer for i, c in enumerate(tui.commits) if "of the streamed answer" in c)
    assert tui.index("Line 40") < steer < tui.index("reply 2")


async def test_a_follow_up_lands_after_the_answer_and_before_its_reply() -> None:
    provider = _Provider(
        [("text", _paragraphs(1, 2)), ("gate", "mid"), ("text", _paragraphs(3, 4))],
        [("text", "reply 2")],
    )
    async with _Tui(provider) as tui:
        tui.pipe.send_text("T1 go\n")
        await tui.at("mid")
        tui.pipe.send_text("FOLLOW_ME later" + _ALT_ENTER)
        await _wait(lambda: "Follow-up: FOLLOW_ME later" in tui.pending())
        assert not any("FOLLOW_ME" in c for c in tui.commits)
        provider.gate("mid").set()
        await _wait(tui.idle)
        await _wait(lambda: any("reply 2" in c for c in tui.commits))
    assert tui.index("Line 04") < tui.index("Follow-up: FOLLOW_ME later") < tui.index("reply 2")


async def test_a_steer_typed_during_a_tool_call_lands_after_its_result() -> None:
    provider = _Provider(
        [("text", "Let me look."), ("gate", "mid"), ("tool", "no_such_tool")],
        [("text", "reply 2")],
    )
    async with _Tui(provider) as tui:
        tui.pipe.send_text("T1 go\n")
        await tui.at("mid")
        tui.pipe.send_text("STEER_ME please\n")
        await _wait(lambda: "Steering: STEER_ME please" in tui.pending())
        provider.gate("mid").set()
        await _wait(tui.idle)
        await _wait(lambda: any("reply 2" in c for c in tui.commits))
    tool = max(i for i, c in enumerate(tui.commits) if "no_such_tool" in c)
    assert tool < tui.index("Steering: STEER_ME please") < tui.index("reply 2")


async def test_an_interrupt_gives_the_queued_line_back_and_clears_the_pending_area() -> None:
    provider = _Provider([("text", "Working on it."), ("gate", "mid")])
    async with _Tui(provider) as tui:
        tui.pipe.send_text("T1 go\n")
        await tui.at("mid")
        tui.pipe.send_text("STEER_ME please\n")
        await _wait(lambda: "Steering: STEER_ME please" in tui.pending())
        tui.pipe.send_text(_CTRL_C)
        await _wait(lambda: "STEER_ME please" in tui.chrome.get_editor_text())
        await _wait(tui.idle)
        await _wait(lambda: tui.pending() == "")
        assert not any("Steering:" in c for c in tui.commits)


async def test_alt_up_takes_the_queued_lines_back_out_of_the_pending_area() -> None:
    provider = _Provider([("text", "Working on it."), ("gate", "mid")])
    async with _Tui(provider) as tui:
        tui.pipe.send_text("T1 go\n")
        await tui.at("mid")
        tui.pipe.send_text("STEER_ME please\n")
        tui.pipe.send_text("FOLLOW_ME later" + _ALT_ENTER)
        await _wait(lambda: "Follow-up: FOLLOW_ME later" in tui.pending())
        assert "Steering: STEER_ME please" in tui.pending()
        tui.pipe.send_text(_ALT_UP)
        await _wait(lambda: tui.pending() == "")
        assert tui.chrome.get_editor_text() == "STEER_ME please\n\nFOLLOW_ME later"
        provider.gate("mid").set()
        await _wait(tui.idle)
        assert not any("STEER_ME" in c or "FOLLOW_ME" in c for c in tui.commits)
        tui.chrome.set_editor_text("")


async def test_the_area_lists_the_lines_in_the_order_the_model_gets_them() -> None:
    # Verify r1: a follow-up queued before a steer was drawn above it, while the
    # steer is handed over first (pi lists every steering line, then every
    # follow-up). The rows now read in delivery order, and the echoes land so.
    provider = _Provider(
        [("text", "Line 01."), ("gate", "mid"), ("text", "Line 02.")],
        [("text", "reply 2")],
        [("text", "reply 3")],
    )
    async with _Tui(provider) as tui:
        tui.pipe.send_text("T1 go\n")
        await tui.at("mid")
        tui.pipe.send_text("FOLLOW_FIRST" + _ALT_ENTER)
        await _wait(lambda: "Follow-up: FOLLOW_FIRST" in tui.pending())
        tui.pipe.send_text("STEER_SECOND\n")
        await _wait(lambda: "Steering: STEER_SECOND" in tui.pending())
        assert tui.pending().splitlines() == [
            "Steering: STEER_SECOND",
            "Follow-up: FOLLOW_FIRST",
            "↳ Alt+Up to edit all queued messages",
        ]
        # The chrome knows which row is the hint, to leave it out of a frame
        # with no row spare (verify r2 B1).
        assert tui.chrome._pending_hint_row == 2
        provider.gate("mid").set()
        await _wait(tui.idle)
        await _wait(lambda: any("reply 3" in c for c in tui.commits))
    assert provider.seen[1] == ["T1 go", "STEER_SECOND"]
    assert provider.seen[2] == ["T1 go", "STEER_SECOND", "FOLLOW_FIRST"]
    assert tui.index("Steering: STEER_SECOND") < tui.index("reply 2")
    assert tui.index("reply 2") < tui.index("Follow-up: FOLLOW_FIRST") < tui.index("reply 3")


async def test_a_line_is_matched_by_the_message_queued_for_it_not_by_its_words() -> None:
    # Codex r1: while a queue_update hook kept steer()/follow_up() open, a line
    # was matched by its text alone. A follow-up and a steer with the same
    # words swapped labels; the steer is handed over first and is the one echoed.
    provider = _Provider(
        [("text", "answer 1"), ("gate", "mid")],
        [("text", "answer 2"), ("gate", "second")],
        [("text", "answer 3")],
    )
    async with _Tui(provider) as tui:
        release = asyncio.Event()
        updates: list[int] = []

        async def slow_update(_event: object, _ctx: object = None) -> None:
            updates.append(1)
            await release.wait()

        tui.harness.hooks.on("queue_update", slow_update)
        try:
            tui.pipe.send_text("T1 go\n")
            await tui.at("mid")
            tui.pipe.send_text("SAME" + _ALT_ENTER)
            await _wait(lambda: len(updates) == 1)
            tui.pipe.send_text("SAME\n")
            await _wait(lambda: len(updates) == 2)
            await _wait(lambda: "Steering: SAME" in tui.pending())
            provider.gate("mid").set()
            await tui.at("second")
            await _wait(lambda: any("SAME" in c for c in tui.commits))
            assert [c.strip() for c in tui.commits if "SAME" in c] == ["Steering: SAME"]
            assert "Follow-up: SAME" in tui.pending()
        finally:
            release.set()
            provider.gate("second").set()
        await _wait(tui.idle)
        await _wait(lambda: any("answer 3" in c for c in tui.commits))
    assert [c.strip() for c in tui.commits if "SAME" in c] == ["Steering: SAME", "Follow-up: SAME"]


async def test_the_prompt_does_not_take_a_queued_line_with_its_words() -> None:
    # Codex r1: the prompt's own message_start, while a follow-up with the same
    # words sat in the queue (its follow_up() held open by a hook), echoed the
    # follow-up there and took it out of the area.
    provider = _Provider([("text", "answer 1")], [("text", "answer 2")])
    async with _Tui(provider) as tui:
        in_input, allow_input = asyncio.Event(), asyncio.Event()
        updated, allow_update = asyncio.Event(), asyncio.Event()
        in_start, allow_start = asyncio.Event(), asyncio.Event()
        starts: list[Any] = []

        async def input_hook(_event: object, _ctx: object = None) -> None:
            in_input.set()
            await allow_input.wait()

        async def queue_hook(_event: object, _ctx: object = None) -> None:
            updated.set()
            await allow_update.wait()

        async def start_hook(event: Any, _ctx: object = None) -> None:
            if getattr(event.message, "role", None) == "user":
                starts.append(event.message)
                if len(starts) == 1:
                    in_start.set()
                    await allow_start.wait()

        tui.harness.hooks.on("input", input_hook)
        tui.harness.hooks.on("queue_update", queue_hook)
        tui.harness.hooks.on("message_start", start_hook)
        try:
            tui.pipe.send_text("SAME\n")
            await asyncio.wait_for(in_input.wait(), 5)
            tui.pipe.send_text("SAME" + _ALT_ENTER)
            await asyncio.wait_for(updated.wait(), 5)
            await _wait(lambda: "Follow-up: SAME" in tui.pending())
            allow_input.set()
            await asyncio.wait_for(in_start.wait(), 5)  # the prompt's message_start
            for _ in range(20):
                await asyncio.sleep(0)
            assert not any("Follow-up:" in c for c in tui.commits)
            assert "Follow-up: SAME" in tui.pending()
            assert len(tui.harness._follow_up_queue._messages) == 1
        finally:
            allow_input.set()
            allow_start.set()
            allow_update.set()
        await _wait(tui.idle)
        await _wait(lambda: any("answer 2" in c for c in tui.commits))
    assert tui.index("answer 1") < tui.index("Follow-up: SAME") < tui.index("answer 2")


async def test_a_line_queued_before_a_queue_update_hook_raised_keeps_its_row() -> None:
    # Codex r1: steer() queues the line, then its queue_update hook raised. The
    # row was withdrawn and "Steering failed" printed, though the line stayed
    # queued and reached the model with no echo; sending it again duplicated it.
    provider = _Provider([("text", "answer 1"), ("gate", "mid")], [("text", "answer 2")])
    async with _Tui(provider) as tui:

        async def fail_update(_event: object, _ctx: object = None) -> None:
            raise RuntimeError("queue observer failed")

        tui.harness.hooks.on("queue_update", fail_update)
        tui.pipe.send_text("T1 go\n")
        await tui.at("mid")
        tui.pipe.send_text("STEER_ME\n")
        await _wait(lambda: any("queue observer failed" in c for c in tui.commits))
        assert "Steering: STEER_ME" in tui.pending()
        provider.gate("mid").set()
        await _wait(tui.idle)
        await _wait(lambda: any("answer 2" in c for c in tui.commits))
    notes = [c for c in tui.commits if "queue observer failed" in c]
    assert notes == [
        "⚠ Steering queued, but queue_update hook handler raised: queue observer failed"
    ]
    assert not any("failed:" in c for c in tui.commits)
    assert provider.seen[1] == ["T1 go", "STEER_ME"]
    assert tui.index("Steering: STEER_ME") < tui.index("answer 2")
    assert tui.pending() == ""


async def test_a_line_the_harness_refused_gets_no_row() -> None:
    provider = _Provider([("text", "answer 1"), ("gate", "mid")])
    async with _Tui(provider) as tui:

        async def refuse(text: str, **_kw: object) -> None:
            raise RuntimeError("the harness refused it")

        tui.harness.steer = refuse  # type: ignore[method-assign]
        shown: list[list[str]] = []
        set_rows = tui.chrome.set_pending_rows

        def spy(rows: list[str] | None, **kwargs: Any) -> None:
            shown.append(list(rows or []))
            set_rows(rows, **kwargs)

        tui.chrome.set_pending_rows = spy  # type: ignore[method-assign]
        tui.pipe.send_text("T1 go\n")
        await tui.at("mid")
        tui.pipe.send_text("STEER_ME\n")
        await _wait(lambda: any("the harness refused it" in c for c in tui.commits))
        assert [c for c in tui.commits if "refused it" in c] == [
            "✖ Steering failed: the harness refused it"
        ]
        assert not any("STEER_ME" in Text.from_ansi(r).plain for rows in shown for r in rows)


async def test_an_abort_the_harness_takes_clears_the_area() -> None:
    # Verify r1 (M10): a plain harness.abort() — from code that holds the
    # harness — clears the queue and ends the run with agent_end, which is what
    # drops the row.
    provider = _Provider([("text", "answer 1"), ("gate", "mid")])
    async with _Tui(provider) as tui:
        tui.pipe.send_text("T1 go\n")
        await tui.at("mid")
        tui.pipe.send_text("STEER_ME\n")
        await _wait(lambda: "Steering: STEER_ME" in tui.pending())
        await tui.harness.abort()
        await _wait(tui.idle)
        await _wait(lambda: tui.pending() == "")
        assert not any("STEER_ME" in c for c in tui.commits)
        assert "STEER_ME" not in tui.chrome.get_editor_text()  # a plain abort keeps nothing


async def test_an_abort_while_a_hook_holds_steer_open_clears_the_area() -> None:
    # Codex r1: abort(withdraw_unanswered=True) while a queue_update hook kept
    # steer() open gave the line back to the input and left its row behind.
    provider = _Provider([("text", "answer 1"), ("gate", "mid")])
    async with _Tui(provider) as tui:
        started, release, returned = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def slow_update(event: Any, _ctx: object = None) -> None:
            if event.steer:
                started.set()
                await release.wait()
                returned.set()

        tui.harness.hooks.on("queue_update", slow_update)
        try:
            tui.pipe.send_text("T1 go\n")
            await tui.at("mid")
            tui.pipe.send_text("RETURN_ME\n")
            await asyncio.wait_for(started.wait(), 5)
            await _wait(lambda: "Steering: RETURN_ME" in tui.pending())
            await tui.harness.abort(withdraw_unanswered=True)
            await _wait(tui.idle)
            release.set()
            await asyncio.wait_for(returned.wait(), 5)
            await _wait(lambda: "RETURN_ME" in tui.chrome.get_editor_text())
            assert tui.pending() == ""
        finally:
            release.set()
        tui.chrome.set_editor_text("")


async def test_an_esc_abort_while_idle_gives_a_left_over_line_back_and_clears_the_area() -> None:
    # A follow-up queued after the loop's last drain waits while nothing runs
    # (verify r1 P4). An abort then has no run to end with agent_end: the
    # input_withdrawn that gives the line back is what drops its row.
    provider = _Provider([("text", "answer 1")])
    async with _Tui(provider) as tui:
        ended, release_end = asyncio.Event(), asyncio.Event()

        async def at_end(_event: object, _ctx: object = None) -> None:
            ended.set()
            await release_end.wait()

        tui.harness.hooks.on("agent_end", at_end)
        try:
            tui.pipe.send_text("T1 go\n")
            await asyncio.wait_for(ended.wait(), 5)
            tui.pipe.send_text("LEFT_OVER" + _ALT_ENTER)
            await _wait(lambda: "Follow-up: LEFT_OVER" in tui.pending())
        finally:
            release_end.set()
        await _wait(tui.idle)
        assert "Follow-up: LEFT_OVER" in tui.pending()
        await tui.harness.abort(withdraw_unanswered=True)
        await _wait(lambda: "LEFT_OVER" in tui.chrome.get_editor_text())
        await _wait(lambda: tui.pending() == "")
        tui.chrome.set_editor_text("")


async def test_a_plain_abort_while_idle_drops_the_row_as_the_next_run_starts() -> None:
    # Verify r2 D14: a follow-up queued after the loop's last drain waits while
    # nothing runs. A plain harness.abort() (from code that holds the harness;
    # an extension's ctx.abort() only marks one) clears it and, idle, emits
    # nothing: its row stayed through the whole next turn, which never sent the
    # line. It goes when that turn's run starts (agent_start).
    provider = _Provider([("text", "answer 1")], [("text", "answer 2"), ("gate", "mid2")])
    async with _Tui(provider) as tui:
        ended, release_end = asyncio.Event(), asyncio.Event()

        async def at_end(_event: object, _ctx: object = None) -> None:
            if not ended.is_set():
                ended.set()
                await release_end.wait()

        tui.harness.hooks.on("agent_end", at_end)
        try:
            tui.pipe.send_text("T1 go\n")
            await asyncio.wait_for(ended.wait(), 5)
            tui.pipe.send_text("LEFT_OVER" + _ALT_ENTER)
            await _wait(lambda: "Follow-up: LEFT_OVER" in tui.pending())
        finally:
            release_end.set()
        await _wait(tui.idle)
        await tui.harness.abort()
        assert tui.harness._follow_up_queue._messages == []
        tui.pipe.send_text("T2 go\n")
        await tui.at("mid2")
        await _wait(lambda: tui.pending() == "")  # while the turn is still held
        provider.gate("mid2").set()
        await _wait(tui.idle)
    assert provider.seen[1] == ["T1 go", "T2 go"]
    assert not any("LEFT_OVER" in c for c in tui.commits)


async def test_two_lines_with_the_same_words_in_one_tick_each_keep_their_own_row() -> None:
    # Verify r2 D8a: two Enters in the same loop tick, with the same words. Both
    # steer() calls queue their message before either _enqueue reads the queue;
    # the second line must not be bound to the first one's message, or the
    # first message is echoed once, the second never, and a row lingers.
    provider = _Provider(
        [("text", _paragraphs(1, 2)), ("gate", "mid")],
        [("text", "reply 2")],
        [("text", "reply 3")],
    )
    async with _Tui(provider) as tui:
        tui.pipe.send_text("T1 go\n")
        await tui.at("mid")
        assert tui.chrome.on_steer is not None
        for _ in range(2):  # synchronous: one tick
            tui.chrome.on_steer(SubmittedLine("SAME", ParsedInput("prompt", "SAME")))
        await _wait(lambda: len(tui.harness._steering_queue._messages) == 2)
        await _wait(lambda: tui.pending().count("Steering: SAME") == 2)
        provider.gate("mid").set()
        await _wait(tui.idle)
        await _wait(lambda: any("reply 3" in c for c in tui.commits))
    echoes = [c.strip() for c in tui.commits if c.strip().startswith("Steering:")]
    assert echoes == ["Steering: SAME", "Steering: SAME"]
    assert tui.pending() == ""


async def test_a_line_is_bound_to_its_own_message_when_a_hook_queues_the_same_words() -> None:
    # Verify r2 D9: while the user's steer() awaits its queue_update hooks, an
    # extension's hook steers the same words. The row belongs to the first new
    # message in the queue, the user's, which the turn hands over first: the
    # echo lands before reply 2, not with the extension's line after it.
    provider = _Provider(
        [("text", _paragraphs(1, 2)), ("gate", "mid")],
        [("text", "reply 2")],
        [("text", "reply 3")],
    )
    async with _Tui(provider) as tui:
        fired: list[int] = []

        async def hook(event: Any, _ctx: object = None) -> None:
            if not fired and event.steer:
                fired.append(1)
                await tui.harness.steer("SAME")

        tui.harness.hooks.on("queue_update", hook)
        tui.pipe.send_text("T1 go\n")
        await tui.at("mid")
        tui.pipe.send_text("SAME\n")
        await _wait(lambda: len(tui.harness._steering_queue._messages) == 2)
        await _wait(lambda: "Steering: SAME" in tui.pending())
        assert tui.pending().count("Steering: SAME") == 1
        provider.gate("mid").set()
        await _wait(tui.idle)
        await _wait(lambda: any("reply 3" in c for c in tui.commits))
    assert provider.seen[1] == ["T1 go", "SAME"]  # one at a time: the user's first
    assert [c.strip() for c in tui.commits if c.strip().startswith("Steering:")] == [
        "Steering: SAME"
    ]
    assert tui.index("Steering: SAME") < tui.index("reply 2")
    assert tui.pending() == ""


async def _reload_with_a_follow_up_queued(
    tmp_path: Any, *, carry: bool
) -> tuple[list[str], list[str], _Provider, list[str]]:
    """A follow-up queued after the turn's last drain, then ``/reload``, then a
    prompt, until the TUI is idle again. *carry*: the rebuilt harness adopts the
    old one's queued messages, the same objects, as #681 does on ``/reload``;
    without it the rebuilt harness refuses them (its ``adopt_queued_messages``
    takes nothing), so the line leaves with the old harness. Returns the
    pending rows right after the reload, the commits, the provider and the
    pending rows at the end."""

    from tests.runtime.test_failed_rebuild_663 import _Rig
    from tests.tui.test_run_tui_smoke import _harness_chrome, _input_loop_is_idle, _launch

    rig = await _Rig.create(tmp_path)
    provider = _Provider([("text", "answer 1")], [("text", "answer 2")], [("text", "answer 3")])

    def make_harness(session: Any) -> AgentHarness:
        harness = AgentHarness(
            AgentHarnessOptions(
                stream_fn=provider,
                session=session,
                model=Model(id="mock", provider="mock", api="mock"),
            )
        )
        harness.state.auto_retry_enabled = False
        harness.state.auto_compaction_enabled = False
        return harness

    old = make_harness(rig.session)

    async def factory(session: Any, **_kwargs: Any) -> AgentHarness:
        new = make_harness(session)
        if not carry:
            new.adopt_queued_messages = lambda *_a, **_k: None  # type: ignore[method-assign]
        return new

    rig.runtime._harness = old
    rig.runtime._create_harness = factory  # type: ignore[assignment]
    ended, release_end = asyncio.Event(), asyncio.Event()

    async def at_end(_event: object, _ctx: object = None) -> None:
        ended.set()
        await release_end.wait()

    old.hooks.on("agent_end", at_end)
    async with _harness_chrome(harness=old) as (_runtime, chrome, pipe):  # type: ignore[arg-type]
        commits = _spy_commits(chrome)
        task = _launch(rig.runtime, chrome)  # type: ignore[arg-type]

        def pending() -> list[str]:
            return [Text.from_ansi(r).plain for r in chrome._pending_rows]

        try:
            await _wait(lambda: chrome.app.is_running)
            pipe.send_text("T1 go\n")
            await asyncio.wait_for(ended.wait(), 5)  # after the loop's last drain
            pipe.send_text("SAVE_THIS_FOLLOWUP" + _ALT_ENTER)
            await _wait(lambda: "Follow-up: SAVE_THIS_FOLLOWUP" in pending())
            release_end.set()
            await _wait(lambda: old.is_idle and not chrome.running)
            assert len(old._follow_up_queue._messages) == 1
            pipe.send_text("/reload\n")
            await _wait(lambda: rig.runtime.harness is not old)
            await _wait(lambda: _input_loop_is_idle(chrome))
            if not carry:
                # The area reaches the chrome through the output queue's next
                # drain, which on a coarse clock (Windows py<=3.12) can come
                # after the input loop is idle again: wait for the row to go.
                await _wait(lambda: "Follow-up: SAVE_THIS_FOLLOWUP" not in pending())
            after_reload = pending()
            new = rig.runtime.harness
            pipe.send_text("next prompt\n")
            await _wait(lambda: len(provider.seen) >= 2)
            await _wait(lambda: new.is_idle and not chrome.running)
            return after_reload, commits, provider, pending()
        finally:
            release_end.set()
            chrome.set_editor_text("")
            pipe.send_text("/quit\n")
            await _quit_within(task)


async def test_reload_drops_a_line_the_rebuilt_harness_does_not_hold(tmp_path: Any) -> None:
    # Verify r1 (M12): _rebind reconciles against the harness it installs.
    # A rebuilt harness that does not take the old one's queue over: the line is
    # gone with the old one, so its row goes too, and nothing echoes it later.
    after_reload, commits, provider, end = await _reload_with_a_follow_up_queued(
        tmp_path, carry=False
    )
    assert after_reload == []
    assert "SAVE_THIS_FOLLOWUP" not in provider.seen[-1]
    assert not any("SAVE_THIS_FOLLOWUP" in c for c in commits)
    assert end == []


async def test_reload_keeps_a_line_the_rebuilt_harness_carries_over(tmp_path: Any) -> None:
    # Codex r1 (high): reconciled against the rebuilt harness's empty queue, the
    # row vanished on /reload. With #681 the rebuilt harness holds the same
    # message: the row stays, the next prompt's turn takes the line, and its
    # echo lands where the model read it.
    after_reload, commits, provider, end = await _reload_with_a_follow_up_queued(
        tmp_path, carry=True
    )
    assert after_reload[0] == "Follow-up: SAVE_THIS_FOLLOWUP"
    assert len(provider.seen) == 3
    assert provider.seen[-1][-2:] == ["next prompt", "SAVE_THIS_FOLLOWUP"]
    echo = next(i for i, c in enumerate(commits) if "Follow-up: SAVE_THIS_FOLLOWUP" in c)
    answer_2 = next(i for i, c in enumerate(commits) if "answer 2" in c)
    answer_3 = next(i for i, c in enumerate(commits) if "answer 3" in c)
    assert answer_2 < echo < answer_3
    assert end == []


# === the queued lines on their own ============================================


class _Queue:
    def __init__(self, *messages: Any) -> None:
        self._messages: list[Any] = list(messages)


class _Harness:
    def __init__(self) -> None:
        self._steering_queue = _Queue()
        self._follow_up_queue = _Queue()
        self._next_turn_queue: list[Any] = []

    def queue(self, kind: str) -> _Queue:
        return self._steering_queue if kind == "steer" else self._follow_up_queue


def _user(text: str) -> UserMessage:
    return UserMessage(content=[TextContent(text=text)])


_Shown = tuple[list[str], bool, "int | None"]  # rows, spacer, the hint's row


def _lines(
    harness: _Harness, blocks: ShellLane | None = None, *, text_above: bool = False
) -> tuple[QueuedMessages, list[tuple[str, str]], list[_Shown]]:
    echoes: list[tuple[str, str]] = []
    shown: list[_Shown] = [([], False, None)]
    current = {"h": harness}
    lines = QueuedMessages(
        current_harness=lambda: current["h"],
        commit_echo=lambda kind, text: echoes.append((kind, text)),
        show=lambda rows, spacer, hint: shown.append((rows, spacer, hint)),
        width=lambda: 40,
        blocks=blocks,
        text_above=lambda: text_above,
    )
    return lines, echoes, shown


def _queue(lines: QueuedMessages, harness: _Harness, kind: Any, text: str) -> Any:
    """What ``_enqueue`` does around ``steer()``: the message, then the row."""

    before = QueuedMessages.queued_now(harness, kind)
    message = _user(text)
    harness.queue(kind)._messages.append(message)
    assert lines.taken(harness, kind, text, before) is not None
    return message


def _plain_rows(shown: list[_Shown]) -> list[str]:
    return [Text.from_ansi(r).plain for r in shown[-1][0]]


def test_a_line_is_known_by_its_message_alone() -> None:
    harness = _Harness()
    lines, echoes, _shown = _lines(harness)
    queued = _queue(lines, harness, "steer", "same words")
    # The prompt's own message_start, with the same words: not the queued line.
    assert lines.delivered(_user("same words")) is False
    assert lines.delivered(_user("an extension's steer")) is False
    assert lines.delivered(queued) is True
    assert echoes == [("steer", "same words")]


def test_a_line_the_harness_did_not_queue_is_not_drawn() -> None:
    harness = _Harness()
    lines, _echoes, shown = _lines(harness)
    # Nothing new in the queue after steer()'s first step: refused, or cleared.
    assert lines.taken(harness, "steer", "refused", []) is None
    old = _user("old")
    harness._steering_queue._messages.append(old)
    # A message that was there before is not the line's, even with its words.
    assert lines.taken(harness, "steer", "old", [old]) is None
    # Nor is one in the other kind's queue.
    harness._follow_up_queue._messages.append(_user("other kind"))
    assert lines.taken(harness, "steer", "other kind", []) is None
    assert shown[-1] == ([], False, None)


def test_a_line_leaves_the_area_when_the_harness_no_longer_holds_it() -> None:
    harness = _Harness()
    lines, echoes, shown = _lines(harness)
    kept = _queue(lines, harness, "steer", "kept")
    lines.reconcile()
    assert _plain_rows(shown)[0] == "Steering: kept"
    # Esc took it out of a turn before the model read it: the next turn has it.
    harness._steering_queue._messages.clear()
    harness._next_turn_queue.append(kept)
    lines.reconcile()
    assert _plain_rows(shown)[0] == "Steering: kept"
    harness._next_turn_queue.clear()  # an abort cleared it
    lines.reconcile()
    assert shown[-1] == ([], False, None) and echoes == []


def test_a_line_carried_to_the_rebuilt_harness_stays_and_is_echoed_there() -> None:
    # Codex r1 (high): a follow-up queued after the loop's last drain left the
    # area on /reload, reconciled against the rebuilt harness's empty queue.
    # #681 (lane B) moves the SAME message objects to the rebuilt harness
    # (take_queued_messages / adopt_queued_messages): the line stays and its
    # echo comes on the new harness's message_start. Without #681 the line is
    # gone with the old harness, and so is its row.
    old = _Harness()
    lines, echoes, shown = _lines(old)
    message = _queue(lines, old, "follow_up", "SAVE_THIS_FOLLOWUP")
    carried = _Harness()
    carried._follow_up_queue._messages.append(message)  # adopted: the same object
    lines.reconcile(carried)
    assert _plain_rows(shown)[0] == "Follow-up: SAVE_THIS_FOLLOWUP"
    assert lines.delivered(message) is True
    assert echoes == [("follow_up", "SAVE_THIS_FOLLOWUP")]

    old = _Harness()
    lines, echoes, shown = _lines(old)
    _queue(lines, old, "follow_up", "LOST")
    rebuilt = _Harness()
    rebuilt._follow_up_queue._messages.append(_user("LOST"))  # same words, not the line
    lines.reconcile(rebuilt)
    assert shown[-1] == ([], False, None) and echoes == []


def test_steering_lines_are_listed_before_follow_ups() -> None:
    harness = _Harness()
    lines, _echoes, shown = _lines(harness)
    _queue(lines, harness, "follow_up", "first follow-up")
    _queue(lines, harness, "steer", "first steer")
    _queue(lines, harness, "follow_up", "second follow-up")
    _queue(lines, harness, "steer", "second steer")
    assert _plain_rows(shown) == [
        "Steering: first steer",
        "Steering: second steer",
        "Follow-up: first follow-up",
        "Follow-up: second follow-up",
        "↳ Alt+Up to edit all queued messages",
    ]


def _lane(harness: _Harness, sink: list[list[str]]) -> ShellLane:
    return ShellLane(
        busy=lambda: True,
        current_harness=lambda: harness,
        commit=lambda r: None,
        show=sink.append,
        theme=lambda: None,
        width=lambda: 40,
        output_text=Text,
    )


def _block(lane: ShellLane, command: str, output_lines: int) -> None:
    block = lane.begin("!", command, pending=True)
    if output_lines:
        block.run = type("Run", (), {"output": "\n".join(f"o{i}" for i in range(output_lines))})()
    block.state = "done"


@pytest.mark.parametrize(
    ("blocks", "queued", "text_above", "want"),
    [
        # No block: the lines may take all eight. A free row takes the spacer
        # (when text sits above the area), the next one the hint.
        ([], 2, True, ["S", "Q", "Q", "H"]),
        ([], 2, False, ["Q", "Q", "H"]),
        ([], 7, True, ["S"] + ["Q"] * 7),
        ([], 7, False, ["Q"] * 7 + ["H"]),
        ([], 8, True, ["Q"] * 8),
        ([], 9, True, ["Q"] * 7 + ["M:2"]),
        # One 2-row block: the lines take 8 - 2 = 6.
        ([1], 5, True, ["S"] + ["Q"] * 5 + ["B", "o"]),
        ([1], 5, False, ["Q"] * 5 + ["H", "B", "o"]),
        ([1], 7, True, ["Q"] * 5 + ["M:2", "B", "o"]),
        # A 4-row block and three lines leave one row: the spacer's, or the
        # hint's; four lines fill the eight, and both go.
        ([5], 3, True, ["S"] + ["Q"] * 3 + ["B", "M", "o", "o"]),
        ([5], 3, False, ["Q"] * 3 + ["H", "B", "M", "o", "o"]),
        ([5], 4, True, ["Q"] * 4 + ["B", "M", "o", "o"]),
        # Two 4-row blocks need eight: the lines keep three rows, the newest
        # block keeps its four under "… 1 more command above".
        ([5, 5], 4, True, ["Q", "Q", "M:2", "A", "B", "M", "o", "o"]),
        ([5, 5, 5], 10, True, ["Q", "Q", "M:8", "A", "B", "M", "o", "o"]),
        # One line under three 4-row blocks: the blocks may take seven rows and
        # use five (whole blocks); the spacer and the hint take the other two.
        ([5, 5, 5], 1, True, ["S", "Q", "H", "A", "B", "M", "o", "o"]),
        # Blocks wanting six rows leave the lines three, so four fold; the
        # blocks keep three of their five (whole blocks). Of the two rows left,
        # one takes the spacer and none the hint: folded lines have the
        # "… more queued" row (verify r2 R8).
        ([5, 1], 4, True, ["S", "Q", "Q", "M:2", "A", "B", "o"]),
        ([5, 1], 4, False, ["Q", "Q", "M:2", "A", "B", "o"]),
    ],
)
def test_the_lines_take_what_the_blocks_leave_of_the_eight_rows(
    blocks: list[int], queued: int, text_above: bool, want: list[str]
) -> None:
    harness = _Harness()
    sink: list[list[str]] = []
    lane = _lane(harness, sink)
    lines, _echoes, shown = _lines(harness, blocks=lane, text_above=text_above)
    for i, n in enumerate(blocks):
        _block(lane, f"echo {i}", n)
    for i in range(queued):
        _queue(lines, harness, "steer", f"line {i}\nsecond row")
    rows = _plain_rows(shown)
    spacer = shown[-1][1]
    assert len(rows) + spacer <= _PENDING_MAX_ROWS  # the spacer counts in the eight
    assert all(len(r) <= 40 for r in rows)

    def shape(row: str) -> str:
        if row.startswith("Steering: line "):
            assert row.endswith(" …")
            return "Q"
        if row == "↳ Alt+Up to edit all queued messages":
            return "H"
        if row.endswith(" more queued (Alt+Up edits them)"):
            return "M:" + row.split()[1]
        if row.startswith("… ") and "more command" in row:
            return "A"
        if row.startswith("! echo"):
            return "B"
        if "more lines" in row:
            return "M"
        return "o"

    assert ["S"] * spacer + [shape(r) for r in rows] == want
    # The chrome is told which row is the hint: it leaves that row out while
    # the frame has none spare.
    hint = shown[-1][2]
    assert [i for i, r in enumerate(rows) if shape(r) == "H"] == ([] if hint is None else [hint])
    if blocks:
        assert f"! echo {len(blocks) - 1}" in "\n".join(rows)  # the newest block keeps its rows
    assert _QUEUED_MIN_ROWS == 3


def test_the_spacer_stands_in_for_the_blank_row_above_the_area() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        chrome = AelixChrome()
        steer = "\x1b[2mSteering: x\x1b[0m"

        def frame() -> tuple[list[str], list[str]]:
            above = _plain(str(chrome._render_widgets_above().value))
            pending = _plain(str(chrome._render_pending_rows().value))
            return above.split("\n"), pending.split("\n")

        # The stream ends with a blank row: the spacer takes its place.
        assert chrome.text_above_pending() is False
        chrome.set_widget("__stream__", ["Line 1", "", "Line 2", ""])
        assert chrome.text_above_pending() is True
        chrome.set_pending_rows([steer], spacer=True)
        assert frame() == (["Line 1", "", "Line 2"], ["", "Steering: x"])
        # No free row: the stream keeps its blank row, the area has no spacer.
        chrome.set_pending_rows([steer], spacer=False)
        assert frame() == (["Line 1", "", "Line 2", ""], ["Steering: x"])
        # A widget that ends on text (the retry countdown): a spacer under it.
        chrome.set_widget("__stream__", None)
        chrome.set_widget("retry", ["\x1b[33m⟳ retrying in 3s\x1b[0m"])
        chrome.set_pending_rows([steer], spacer=True)
        assert frame() == (["⟳ retrying in 3s"], ["", "Steering: x"])
        # Nothing above but the empty row the widgets keep: no spacer.
        chrome.set_widget("retry", None)
        assert chrome.text_above_pending() is False
        assert frame() == ([""], ["Steering: x"])
        # Only ``!`` blocks (no queued line) never ask for one.
        chrome.set_widget("__stream__", ["Line 1", ""])
        chrome.set_pending_rows(["! echo hi"])
        assert frame() == (["Line 1", ""], ["! echo hi"])


# === the spacer and the hint only in a row the frame has spare ===============


_DIM = "\x1b[2m{}\x1b[0m"
_HINT = "↳ Alt+Up to edit all queued messages"


async def test_the_spacer_and_the_hint_take_a_row_only_while_the_frame_has_one_spare() -> None:
    # Verify r2 B1: the eight rows had a row for them, the frame did not. Each
    # is drawn only while the whole frame, with it, fits; the hint goes first.
    # (async: measuring the input row loads its history on the running loop)
    steer = _DIM.format("Steering: x")

    def frame_at(
        above: list[str], pending: list[str]
    ) -> tuple[int, Callable[[int], tuple[list[str], list[str]]]]:
        # A chrome per frame: prompt-toolkit keeps a control's text per render,
        # and outside a running app every call is the same render.
        chrome = AelixChrome()
        chrome.set_widget("__stream__", above)
        chrome.set_pending_rows(pending, spacer=True, hint_row=1)
        full = chrome._body.preferred_height(80, 1000).preferred

        def drawn(height: int) -> tuple[list[str], list[str]]:
            chrome._settle_pending_room(80, height)
            above = _plain(str(chrome._render_widgets_above().value))
            pending = _plain(str(chrome._render_pending_rows().value))
            return above.split("\n"), pending.split("\n")

        return full, drawn

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        # Text right above the area (a stream mid-paragraph): spacer, then hint.
        full, drawn = frame_at(["Line 1", "", "Line 2"], [steer, _DIM.format(_HINT)])
        assert drawn(full) == (["Line 1", "", "Line 2"], ["", "Steering: x", _HINT])
        assert drawn(full - 1) == (["Line 1", "", "Line 2"], ["", "Steering: x"])
        assert drawn(full - 2) == (["Line 1", "", "Line 2"], ["Steering: x"])
        assert drawn(full - 3) == (["Line 1", "", "Line 2"], ["Steering: x"])
        assert drawn(full) == (["Line 1", "", "Line 2"], ["", "Steering: x", _HINT])

        # A stream that ends with a blank row: the spacer takes that row's place,
        # so without the hint the frame keeps its height; a frame that cannot
        # fit even that keeps the stream's own blank row, which a cut takes first.
        full, drawn = frame_at(["Line 1", "", "Line 2", ""], [steer, _DIM.format(_HINT)])
        assert drawn(full) == (["Line 1", "", "Line 2"], ["", "Steering: x", _HINT])
        assert drawn(full - 1) == (["Line 1", "", "Line 2"], ["", "Steering: x"])
        assert drawn(full - 2) == (["Line 1", "", "Line 2", ""], ["Steering: x"])

        # The ``!`` blocks' own rows are never left out, only the hint's.
        full, drawn = frame_at(["Line 1"], [steer, _DIM.format(_HINT), "! echo hi", "hi"])
        assert drawn(full)[1] == ["", "Steering: x", _HINT, "! echo hi", "hi"]
        assert drawn(full - 3)[1] == ["Steering: x", "! echo hi", "hi"]


async def _frame_under_a_stream(
    cols: int, rows: int, paragraphs: int, queued: int, *, extras: bool
) -> list[str]:
    """The real chrome at *cols* x *rows* while a turn runs: a stream of
    *paragraphs* (a text row and a blank row each, as the live tail ends) over
    *queued* lines, with the spacer and hint the area composes (*extras*) or none."""

    def build(chrome: AelixChrome) -> None:
        stream: list[str] = []
        for i in range(1, paragraphs + 1):
            stream += [f"Line {i:02d} of the streamed answer.", ""]
        chrome.set_widget("__stream__", stream)
        chrome.set_working_visible(True)
        lines = [_DIM.format(f"Steering: S{i}") for i in range(queued)]
        if extras:
            chrome.set_pending_rows([*lines, _DIM.format(_HINT)], spacer=True, hint_row=queued)
        else:
            chrome.set_pending_rows(lines)

    grid = await render_chrome_to_screen(rows=rows, cols=cols, build_state=build)
    return [row.rstrip() for row in grid]


def _over_the_lines(grid: list[str]) -> tuple[int, str]:
    """The blank rows right above the first queued line, and the row above them."""

    first = next(i for i, row in enumerate(grid) if row.startswith("Steering: S0"))
    above = first - 1
    while above >= 0 and not grid[above]:
        above -= 1
    return first - 1 - above, grid[above]


@pytest.mark.parametrize(("cols", "rows"), [(80, 24), (50, 24)])
@pytest.mark.parametrize("queued", [1, 3])
@pytest.mark.parametrize("paragraphs", [6, 7, 8])
async def test_a_short_terminal_keeps_the_newest_streamed_rows_over_the_spacer_and_hint(
    cols: int, rows: int, queued: int, paragraphs: int
) -> None:
    # Verify r2 B1: a frame taller than the terminal cuts the bottom of the
    # stream's window. The spacer and the hint took two of its rows: the newest
    # paragraph left the screen, and a cut on a paragraph's blank row put two
    # blank rows over the lines. The stream now loses nothing to them: the
    # frame shows the stream as the lines alone leave it.
    lines_only = await _frame_under_a_stream(cols, rows, paragraphs, queued, extras=False)
    composed = await _frame_under_a_stream(cols, rows, paragraphs, queued, extras=True)
    newest = f"Line {paragraphs:02d}"
    assert any(r.startswith(newest) for r in composed) == any(
        r.startswith(newest) for r in lines_only
    )
    blanks, text = _over_the_lines(composed)
    assert text == _over_the_lines(lines_only)[1]
    assert blanks <= 1
    if any(r == _HINT for r in composed):  # the hint only ever under a spacer
        assert blanks == 1


@pytest.mark.parametrize(("cols", "rows"), [(80, 24), (50, 24)])
@pytest.mark.parametrize("paragraphs", [4, 5, 6, 7])
async def test_a_spacer_without_a_hint_takes_no_row_the_stream_needs(
    cols: int, rows: int, paragraphs: int
) -> None:
    # Verify r3 X5: three lines and a 4-row ! block leave one row, so the area
    # asks for the spacer alone (no hint). Under a stream cut by a short
    # terminal that row is not spare either: the frame is the one without it.
    block = ["! printf a\\nb\\nc", "a", "b", "c"]

    async def frame(spacer: bool) -> list[str]:
        def build(chrome: AelixChrome) -> None:
            stream: list[str] = []
            for i in range(1, paragraphs + 1):
                stream += [f"Line {i:02d} of the streamed answer.", ""]
            stream.append("Newest words still streaming")
            chrome.set_widget("__stream__", stream)
            chrome.set_working_visible(True)
            lines = [_DIM.format(f"Steering: S{i}") for i in range(3)]
            chrome.set_pending_rows([*lines, *block], spacer=spacer, hint_row=None)

        grid = await render_chrome_to_screen(rows=rows, cols=cols, build_state=build)
        return [row.rstrip() for row in grid]

    assert await frame(True) == await frame(False)


@pytest.mark.parametrize(
    ("cols", "rows", "paragraphs"), [(120, 40, 6), (120, 40, 12), (100, 30, 6), (100, 30, 8)]
)
@pytest.mark.parametrize("queued", [1, 3])
async def test_a_tall_terminal_draws_the_spacer_and_the_hint(
    cols: int, rows: int, paragraphs: int, queued: int
) -> None:
    grid = await _frame_under_a_stream(cols, rows, paragraphs, queued, extras=True)
    blanks, text = _over_the_lines(grid)
    assert (blanks, text) == (1, f"Line {paragraphs:02d} of the streamed answer.")
    first = grid.index("Steering: S0")
    assert grid[first : first + queued + 1] == [
        *(f"Steering: S{i}" for i in range(queued)),
        _HINT,
    ]
