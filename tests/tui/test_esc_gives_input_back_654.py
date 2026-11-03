"""#654 (ADR-0286) — what Esc takes out of context comes back to the input box.

A real :class:`AgentHarness` behind the headless chrome: the event, the editor and
the transcript line are the three things that have to agree.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.prompt_templates import PromptTemplate
from aelix_agent_core.session import (
    CompactionPreparation,
    CompactResult,
    MemorySessionStorage,
    Session,
)
from aelix_agent_core.types import AgentEndEvent, AgentStartEvent, TurnEndEvent
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantErrorEvent,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.render import INPUT_RETURNED_NOTICE, EventRenderer
from aelix_coding_agent.tui.shell import run_tui
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from tests.tui.test_interrupt_reaches_long_work_604 import _capture_signal_handlers
from tests.tui.test_run_tui_smoke import FakeRuntime, _quit_within, _spy_commits, _wait

_CTRL_C = "\x03"
_ALT_UP = "\x1b[1;3A"
_NOTICE = f"✖ Operation aborted. {INPUT_RETURNED_NOTICE}"


class _Provider:
    """``hang`` holds the request open, ``failed`` is a retryable error, ``overflow`` a
    context overflow."""

    def __init__(self, *script: str) -> None:
        self.script = list(script)
        self.seen: list[list[str]] = []
        self.reached = asyncio.Event()

    async def __call__(
        self, _model: Model, context: Any, _options: SimpleStreamOptions
    ) -> AsyncIterator[Any]:
        self.seen.append(
            [
                block.text
                for message in context.messages
                for block in message.content
                if isinstance(block, TextContent)
            ]
        )
        step = self.script.pop(0) if self.script else "answer"
        if step == "hang":
            self.reached.set()
            await asyncio.Event().wait()
        if step in ("failed", "overflow"):
            yield AssistantErrorEvent(
                reason="error",
                error=AssistantMessage(
                    stop_reason="error",
                    error_message=(
                        "overloaded_error: provider returned error"
                        if step == "failed"
                        else "prompt is too long: 213462 tokens > 200000 maximum"
                    ),
                ),
            )
            return
        yield AssistantEndEvent(message=AssistantMessage(content=[TextContent(text="answer")]))


def _real_harness(provider: _Provider, **options: Any) -> AgentHarness:
    harness = AgentHarness(
        AgentHarnessOptions(
            stream_fn=provider,
            session=Session(MemorySessionStorage()),
            model=Model(id="mock", provider="mock", api="mock"),
            **options,
        )
    )
    harness.state.auto_retry_enabled = False
    harness.state.auto_compaction_enabled = False
    return harness


class _Tui:
    def __init__(self, provider: _Provider, *, signals: bool = False, **options: Any) -> None:
        self.provider = provider
        self.harness = _real_harness(provider, **options)
        self.signals = signals
        self.now = 0.0  # the chrome's clock: only a test moves it

    async def __aenter__(self) -> _Tui:
        self._pipe_cm = create_pipe_input()
        self.pipe = self._pipe_cm.__enter__()
        self._session_cm = create_app_session(input=self.pipe, output=DummyOutput())
        self._session_cm.__enter__()
        self.chrome = AelixChrome(time_fn=lambda: self.now)
        self.commits = _spy_commits(self.chrome)
        self.task = asyncio.ensure_future(
            run_tui(
                FakeRuntime(self.harness),  # type: ignore[arg-type]
                cwd=".",
                chrome=self.chrome,
                install_signal_handlers=self.signals,
            )
        )
        await _wait(lambda: self.chrome.app.is_running)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        try:
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

    async def send_until_request(self, line: str) -> None:
        self.pipe.send_text(line + "\n")
        await asyncio.wait_for(self.provider.reached.wait(), timeout=10)
        await asyncio.sleep(0)

    async def editor_reads(self, text: str) -> None:
        await _wait(lambda: self.chrome.get_editor_text() == text)

    def idle(self) -> bool:
        return self.harness.phase == "idle" and not self.chrome.running


async def test_ctrl_c_puts_the_line_back_and_says_so_once() -> None:
    async with _Tui(_Provider("hang")) as tui:
        await tui.send_until_request("the line I typed")
        tui.pipe.send_text(_CTRL_C)
        await tui.editor_reads("the line I typed")
        await _wait(tui.idle)
        await _wait(lambda: _NOTICE in tui.commits)
        assert tui.commits.count(_NOTICE) == 1
        assert not [c for c in tui.commits if c == "✖ Operation aborted"]
        assert tui.harness.messages[-1].stop_reason == "aborted"  # type: ignore[union-attr]
        assert not [m for m in tui.harness.messages if isinstance(m, UserMessage)]


async def test_esc_is_the_same_key() -> None:
    async with _Tui(_Provider("hang")) as tui:
        await tui.send_until_request("said with esc")
        tui.pipe.send_text("\x1b")
        await tui.editor_reads("said with esc")
        await _wait(lambda: _NOTICE in tui.commits)


async def test_a_template_comes_back_as_it_was_typed_not_expanded() -> None:
    provider = _Provider("hang")
    async with _Tui(provider) as tui:
        tui.harness.set_prompt_templates(
            [PromptTemplate(name="tpl", content="A LONG EXPANSION OF THE TEMPLATE")]
        )
        await tui.send_until_request("/tpl")
        assert provider.seen == [["A LONG EXPANSION OF THE TEMPLATE"]]
        tui.pipe.send_text(_CTRL_C)
        await tui.editor_reads("/tpl")


async def test_a_turn_nobody_typed_gives_back_its_own_text_not_the_last_line() -> None:
    """An extension's ``trigger_turn`` prompt, after a typed turn has ended."""

    provider = _Provider("answer", "hang")
    async with _Tui(provider) as tui:
        tui.pipe.send_text("the line typed earlier\n")
        await _wait(lambda: len(provider.seen) == 1 and tui.idle())
        turn = asyncio.ensure_future(tui.harness.prompt("sent by an extension"))
        await asyncio.wait_for(provider.reached.wait(), timeout=10)
        await asyncio.sleep(0)
        await tui.harness.abort(withdraw_unanswered=True)
        await turn
        await tui.editor_reads("sent by an extension")


async def test_what_the_editor_already_holds_stays_after_it() -> None:
    async with _Tui(_Provider("hang")) as tui:
        await tui.send_until_request("first")
        tui.chrome.set_editor_text("half a thought")
        tui.pipe.send_text(_CTRL_C)
        await tui.editor_reads("first\n\nhalf a thought")


async def test_lines_queued_during_the_turn_come_back_behind_the_prompt() -> None:
    async with _Tui(_Provider("hang")) as tui:
        await tui.send_until_request("first")
        tui.pipe.send_text("a queued steer\n")
        await _wait(lambda: len(tui.harness._steering_queue._messages) == 1)
        tui.pipe.send_text(_CTRL_C)
        await tui.editor_reads("first\n\na queued steer")
        assert tui.harness._steering_queue._messages == []
        await _wait(lambda: _NOTICE in tui.commits)


async def test_nothing_to_give_back_leaves_the_plain_abort_line_and_no_dequeue_notice() -> None:
    """An answered prompt stays, and Esc does not say what only Alt+Up may say."""

    provider = _Provider("answer", "hang")
    async with _Tui(provider) as tui:
        await tui.harness.follow_up("")  # an empty follow-up: nothing to put back
        tui.pipe.send_text("first\n")
        await asyncio.wait_for(provider.reached.wait(), timeout=10)
        await asyncio.sleep(0)
        tui.pipe.send_text(_CTRL_C)
        await _wait(tui.idle)
        await _wait(lambda: "✖ Operation aborted" in tui.commits)
        assert tui.chrome.get_editor_text() == ""
        assert _NOTICE not in tui.commits
        assert not [c for c in tui.commits if "No queued messages" in c]
        tui.pipe.send_text(_ALT_UP)
        await _wait(lambda: any("No queued messages" in c for c in tui.commits))


async def test_esc_in_the_countdown_gives_the_line_back_after_the_retry_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("aelix_agent_core.harness.core._AUTO_RETRY_BASE_DELAY_MS", 60_000)
    async with _Tui(_Provider("failed")) as tui:
        tui.harness.state.auto_retry_enabled = True
        tui.pipe.send_text("to be retried\n")
        await _wait(lambda: "__auto_retry__" in tui.chrome._widgets_above)
        tui.pipe.send_text(_CTRL_C)
        await tui.editor_reads("to be retried")
        await _wait(lambda: INPUT_RETURNED_NOTICE in tui.commits)
        failed = next(i for i, c in enumerate(tui.commits) if "Retry failed" in c)
        assert tui.commits.index(INPUT_RETURNED_NOTICE) > failed
        assert tui.commits.count(INPUT_RETURNED_NOTICE) == 1
        assert not [m for m in tui.harness.messages if isinstance(m, UserMessage)]


@pytest.mark.skipif(sys.platform == "win32", reason="run_tui installs no signal handler on win32")
async def test_a_termination_signal_keeps_the_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """SIGTERM aborts through the helper Esc shares, and nobody is left to give it to."""

    import signal

    handlers = _capture_signal_handlers(monkeypatch)
    async with _Tui(_Provider("hang"), signals=True) as tui:
        await tui.send_until_request("kept for the resume")
        handlers[signal.SIGTERM]()
        code = await _quit_within(tui.task)
        assert code == 0
        assert tui.chrome.get_editor_text() == ""
        assert [m for m in tui.harness.messages if isinstance(m, UserMessage)]


# === Review round 1 ===========================================================

_ALT_ENTER = "\x1b\r"
_BIG = "\n".join(f"line {i:03d} 한글 of the paste" for i in range(200))


def _paste(tui: _Tui, text: str) -> None:
    tui.pipe.send_text(f"\x1b[200~{text}\x1b[201~")


async def _in_the_countdown(tui: _Tui, monkeypatch: pytest.MonkeyPatch, line: str) -> None:
    monkeypatch.setattr("aelix_agent_core.harness.core._AUTO_RETRY_BASE_DELAY_MS", 60_000)
    tui.harness.state.auto_retry_enabled = True
    tui.pipe.send_text(line + "\n")
    await _wait(lambda: "__auto_retry__" in tui.chrome._widgets_above)


async def test_esc_in_the_countdown_gives_queued_lines_back_and_they_never_reach_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _Provider("failed")
    async with _Tui(provider) as tui:
        await _in_the_countdown(tui, monkeypatch, "to be retried")
        tui.pipe.send_text("a queued steer\n")
        await _wait(lambda: len(tui.harness._steering_queue._messages) == 1)
        tui.pipe.send_text("a queued follow-up" + _ALT_ENTER)
        await _wait(lambda: len(tui.harness._follow_up_queue._messages) == 1)
        await _wait(lambda: "2 queued" in tui.chrome._footer_line)
        tui.chrome.set_editor_text("half a thought")
        tui.pipe.send_text(_CTRL_C)
        await tui.editor_reads(
            "to be retried\n\na queued steer\n\na queued follow-up\n\nhalf a thought"
        )
        await _wait(tui.idle)
        assert tui.harness._steering_queue._messages == []
        assert tui.harness._follow_up_queue._messages == []
        assert "queued" not in tui.chrome._footer_line
        await _wait(lambda: INPUT_RETURNED_NOTICE in tui.commits)
        assert tui.commits.count(INPUT_RETURNED_NOTICE) == 1
        failed = next(i for i, c in enumerate(tui.commits) if "Retry failed" in c)
        assert tui.commits.index(INPUT_RETURNED_NOTICE) > failed

        tui.chrome.set_editor_text("")
        tui.pipe.send_text("NEW\n")
        await _wait(lambda: provider.seen[-1] == ["NEW"])
        await _wait(tui.idle)
        assert len(provider.seen) == 2


@pytest.mark.parametrize("first", [_CTRL_C, "\x1b"], ids=["ctrl-c", "esc"])
async def test_a_second_ctrl_c_keeps_what_just_came_back_until_the_window_ends(first: str) -> None:
    async with _Tui(_Provider("hang")) as tui:
        await tui.send_until_request("the line I typed")
        tui.pipe.send_text(first)
        await tui.editor_reads("the line I typed")
        await _wait(tui.idle)

        tui.now += 1.9
        tui.pipe.send_text(_CTRL_C + _CTRL_C + "z")  # "z" says the two were read
        await tui.editor_reads("the line I typedz")
        assert tui.chrome._last_ctrl_c is None, "the exit timer stays unarmed"

        tui.now += 0.2  # past the window: Ctrl+C clears, as ever
        tui.pipe.send_text(_CTRL_C)
        await tui.editor_reads("")
        assert tui.chrome._last_ctrl_c is None


async def test_a_second_ctrl_c_after_the_countdown_keeps_what_came_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _Tui(_Provider("failed")) as tui:
        await _in_the_countdown(tui, monkeypatch, "to be retried")
        tui.pipe.send_text(_CTRL_C + _CTRL_C)
        await tui.editor_reads("to be retried")
        await _wait(tui.idle)
        tui.pipe.send_text(_CTRL_C + "z")
        await tui.editor_reads("to be retriedz")
        tui.now += 2.1
        tui.pipe.send_text(_CTRL_C)
        await tui.editor_reads("")


async def test_a_queued_line_alone_coming_back_is_kept_from_a_second_ctrl_c_too() -> None:
    """The prompt was answered, so no event comes: the queue's give-back stamps it."""

    provider = _Provider("answer", "hang")
    async with _Tui(provider) as tui:
        await tui.harness.follow_up("first queued")
        tui.pipe.send_text("first\n")
        await asyncio.wait_for(provider.reached.wait(), timeout=10)
        await asyncio.sleep(0)
        tui.pipe.send_text("a queued steer\n")
        await _wait(lambda: len(tui.harness._steering_queue._messages) == 1)
        tui.pipe.send_text(_CTRL_C)
        await tui.editor_reads("first queued\n\na queued steer")
        await _wait(tui.idle)
        tui.pipe.send_text(_CTRL_C + "z")
        await tui.editor_reads("first queued\n\na queued steerz")


async def test_a_large_paste_comes_back_collapsed_and_resubmits_byte_equal() -> None:
    provider = _Provider("hang", "answer")
    async with _Tui(provider) as tui:
        _paste(tui, _BIG)
        await _wait(lambda: tui.chrome.buffer.text == "[Pasted text #1 +200 lines]")
        await tui.send_until_request("")
        assert provider.seen[-1] == [_BIG]
        tui.pipe.send_text(_CTRL_C)
        await _wait(lambda: tui.chrome.buffer.text == "[Pasted text #1 +200 lines]")
        assert tui.chrome.get_editor_text() == _BIG
        await _wait(tui.idle)

        tui.pipe.send_text("\n")
        await _wait(lambda: len(provider.seen) == 2)
        assert provider.seen[-1] == [_BIG]
        await _wait(tui.idle)
        assert tui.chrome.buffer.text == ""


async def test_every_give_back_collapses_and_the_draft_keeps_its_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_line = "x" * 1500
    other = "\n".join(f"other {i}" for i in range(8))
    provider = _Provider("failed")
    async with _Tui(provider) as tui:
        await _in_the_countdown(tui, monkeypatch, "short prompt")
        _paste(tui, long_line)
        await _wait(lambda: tui.chrome.buffer.text == "[Pasted text #1 +1 lines]")
        tui.pipe.send_text("\n")  # a steer
        await _wait(lambda: len(tui.harness._steering_queue._messages) == 1)
        _paste(tui, other)
        await _wait(lambda: tui.chrome.buffer.text == "[Pasted text #2 +8 lines]")
        tui.pipe.send_text(_CTRL_C)
        await _wait(lambda: tui.chrome.buffer.text.startswith("short prompt"))
        assert tui.chrome.buffer.text == (
            "short prompt\n\n[Pasted text #3 +1 lines]\n\n[Pasted text #2 +8 lines]"
        )
        assert tui.chrome.get_editor_text() == f"short prompt\n\n{long_line}\n\n{other}"
        await _wait(tui.idle)


async def test_alt_up_brings_a_large_line_back_collapsed_and_ctrl_c_still_clears_it() -> None:
    """A key pressed on purpose: no window after it, as before."""

    async with _Tui(_Provider()) as tui:
        await tui.harness.steer(_BIG)
        tui.pipe.send_text(_ALT_UP)
        await _wait(lambda: tui.chrome.buffer.text == "[Pasted text #1 +200 lines]")
        assert tui.chrome.get_editor_text() == _BIG
        tui.pipe.send_text(_CTRL_C)
        await tui.editor_reads("")


_TYPED_AROUND = "앞에 친 글: [Pasted text #1 +200 lines] 뒤 끝"
_SENT_AROUND = f"앞에 친 글: {_BIG} 뒤 끝"


def _type_around_the_paste(tui: _Tui) -> None:
    tui.pipe.send_text("앞에 친 글: ")
    _paste(tui, _BIG)
    tui.pipe.send_text(" 뒤 끝")


async def test_words_typed_around_a_large_paste_come_back_visible_and_resubmit_byte_equal() -> None:
    provider = _Provider("hang", "hang", "answer")
    async with _Tui(provider) as tui:
        _type_around_the_paste(tui)
        await _wait(lambda: tui.chrome.buffer.text == _TYPED_AROUND)
        await tui.send_until_request("")
        assert provider.seen[-1] == [_SENT_AROUND]

        tui.pipe.send_text(_CTRL_C)
        await _wait(lambda: tui.chrome.buffer.text == _TYPED_AROUND)
        assert tui.chrome.get_editor_text() == _SENT_AROUND
        await _wait(tui.idle)

        # And again: what came back is a submitted line like any other.
        tui.pipe.send_text("\n")
        await _wait(lambda: len(provider.seen) == 2)
        assert provider.seen[-1] == [_SENT_AROUND]
        await _wait(lambda: tui.chrome.buffer.text == "")
        tui.pipe.send_text(_CTRL_C)
        await _wait(lambda: tui.chrome.buffer.text == _TYPED_AROUND)
        await _wait(tui.idle)

        tui.pipe.send_text("\n")
        await _wait(lambda: len(provider.seen) == 3)
        assert provider.seen[-1] == [_SENT_AROUND]
        await _wait(tui.idle)
        assert tui.chrome.buffer.text == ""


async def test_a_long_typed_line_comes_back_as_text_not_collapsed() -> None:
    long_line = "typed by hand " * 100
    assert len(long_line) >= 1000
    async with _Tui(_Provider("hang")) as tui:
        await tui.send_until_request(long_line)
        tui.pipe.send_text(_CTRL_C)
        await _wait(lambda: tui.chrome.buffer.text == long_line)
        assert tui.chrome.get_editor_text() == long_line
        await _wait(tui.idle)


async def test_a_template_line_with_a_paste_comes_back_as_it_was_typed() -> None:
    provider = _Provider("hang")
    async with _Tui(provider) as tui:
        tui.harness.set_prompt_templates([PromptTemplate(name="tpl", content="EXPANDED $1")])
        tui.pipe.send_text("/tpl ")
        _paste(tui, _BIG)
        await _wait(lambda: tui.chrome.buffer.text == "/tpl [Pasted text #1 +200 lines]")
        await tui.send_until_request("")
        tui.pipe.send_text(_CTRL_C)
        await _wait(lambda: tui.chrome.buffer.text == "/tpl [Pasted text #1 +200 lines]")
        assert tui.chrome.get_editor_text() == f"/tpl {_BIG}"
        await _wait(tui.idle)


async def test_an_empty_enter_during_the_turn_does_not_lose_how_the_line_read() -> None:
    async with _Tui(_Provider("hang")) as tui:
        _type_around_the_paste(tui)
        await _wait(lambda: tui.chrome.buffer.text == _TYPED_AROUND)
        await tui.send_until_request("")
        tui.pipe.send_text("\n")  # an empty line: not a steer, it joins the input queue
        await asyncio.sleep(0.05)
        tui.pipe.send_text(_CTRL_C)
        await _wait(lambda: tui.chrome.buffer.text == _TYPED_AROUND)
        await _wait(tui.idle)


async def test_a_turn_that_finished_leaves_nothing_behind_and_alt_up_collapses_as_ever() -> None:
    provider = _Provider("answer")
    async with _Tui(provider) as tui:
        _type_around_the_paste(tui)
        await _wait(lambda: tui.chrome.buffer.text == _TYPED_AROUND)
        tui.pipe.send_text("\n")
        await _wait(lambda: len(provider.seen) == 1)
        await _wait(tui.idle)
        assert tui.chrome._paste_registry == {}

        # The same bytes, queued: how the finished line read is not theirs to take.
        await tui.harness.steer(_SENT_AROUND)
        tui.pipe.send_text(_ALT_UP)
        await _wait(lambda: tui.chrome.buffer.text == "[Pasted text #2 +200 lines]")
        assert tui.chrome.get_editor_text() == _SENT_AROUND


async def test_a_cancelled_turn_restores_only_the_entries_its_line_names() -> None:
    """A paste deleted before Enter stays gone, even with a placeholder left in the box."""

    async with _Tui(_Provider("hang")) as tui:
        _paste(tui, "\n".join(f"deleted {i}" for i in range(8)))
        await _wait(lambda: tui.chrome.buffer.text == "[Pasted text #1 +8 lines]")
        tui.chrome.buffer.text = "kept "
        tui.chrome.buffer.cursor_position = len("kept ")
        _paste(tui, _BIG)
        await _wait(lambda: tui.chrome.buffer.text == "kept [Pasted text #2 +200 lines]")
        await tui.send_until_request("")
        tui.pipe.send_text(_CTRL_C)
        await _wait(lambda: tui.chrome.buffer.text == "kept [Pasted text #2 +200 lines]")
        assert list(tui.chrome._paste_registry) == ["[Pasted text #2 +200 lines]"]
        await _wait(tui.idle)


async def test_esc_during_the_overflow_summary_says_the_input_is_back_once_per_run() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def summarise(_model: Any, prep: CompactionPreparation, _ci: Any) -> CompactResult:
        entered.set()
        await release.wait()
        return CompactResult(
            summary="SUMMARY", first_kept_entry_id=prep.first_kept_entry_id, tokens_before=99
        )

    provider = _Provider("overflow", "hang")
    async with _Tui(provider, _summarizer_override=summarise) as tui:
        session = tui.harness.session
        assert session is not None
        for _ in range(6):
            await session.append_message(UserMessage(content=[TextContent(text="x" * 30_000)]))
        tui.harness.state.auto_compaction_enabled = True
        tui.harness.state.model = Model(
            id="m", provider="p", api="unknown", context_window=200_000, max_tokens=4096
        )

        tui.pipe.send_text("the prompt\n")
        await asyncio.wait_for(entered.wait(), timeout=10)
        tui.pipe.send_text("a queued steer\n")
        await _wait(lambda: len(tui.harness._steering_queue._messages) == 1)
        tui.pipe.send_text(_CTRL_C)
        await tui.editor_reads("a queued steer")
        assert tui.commits.count(INPUT_RETURNED_NOTICE) == 1, "the queued lines are back"

        release.set()
        await tui.editor_reads("the prompt\n\na queued steer")
        await _wait(tui.idle)
        assert tui.commits.count(INPUT_RETURNED_NOTICE) == 1, "the prompt is the same fact"

        # A new run says it for itself.
        tui.chrome.set_editor_text("")
        await tui.send_until_request("the next line")
        tui.pipe.send_text(_CTRL_C)
        await _wait(lambda: _NOTICE in tui.commits)
        await tui.editor_reads("the next line")
        await _wait(tui.idle)
        assert tui.commits.count(_NOTICE) == 1


def _renderer() -> tuple[EventRenderer, list[str]]:
    commits: list[Any] = []
    renderer = EventRenderer(commit=commits.append, set_tail=lambda _t: None, width=100)
    return renderer, commits


def _said(commits: list[Any]) -> int:
    return sum(INPUT_RETURNED_NOTICE in getattr(c, "plain", "") for c in commits)


def test_an_abort_line_that_said_it_speaks_for_the_whole_run() -> None:
    renderer, commits = _renderer()
    renderer.on_agent_event(AgentStartEvent())
    renderer.note_input_returned()
    renderer.on_agent_event(
        TurnEndEvent(message=AssistantMessage(stop_reason="aborted"), tool_results=[])
    )
    assert _said(commits) == 1
    renderer.note_input_returned()  # still inside the run: no stale flag for the next one
    renderer.on_agent_event(AgentEndEvent(messages=[]))
    renderer.note_input_returned()
    assert _said(commits) == 1


def test_a_turn_that_ended_without_the_line_has_not_said_it() -> None:
    """The latch stands for words on the screen, not for having been asked."""

    renderer, commits = _renderer()
    renderer.on_agent_event(AgentStartEvent())
    renderer.note_input_returned()
    renderer.on_agent_event(
        TurnEndEvent(message=AssistantMessage(stop_reason="stop"), tool_results=[])
    )
    assert _said(commits) == 0
    renderer.on_agent_event(AgentEndEvent(messages=[]))
    renderer.note_input_returned()
    assert _said(commits) == 1
