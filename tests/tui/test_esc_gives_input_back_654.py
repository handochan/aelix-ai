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
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantErrorEvent,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.tui.chrome import AelixChrome
from aelix_coding_agent.tui.render import INPUT_RETURNED_NOTICE
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
    """``hang`` holds the request open, ``failed`` is a retryable error."""

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
        if step == "failed":
            yield AssistantErrorEvent(
                reason="error",
                error=AssistantMessage(
                    stop_reason="error",
                    error_message="overloaded_error: provider returned error",
                ),
            )
            return
        yield AssistantEndEvent(message=AssistantMessage(content=[TextContent(text="answer")]))


def _real_harness(provider: _Provider) -> AgentHarness:
    harness = AgentHarness(
        AgentHarnessOptions(
            stream_fn=provider,
            session=Session(MemorySessionStorage()),
            model=Model(id="mock", provider="mock", api="mock"),
        )
    )
    harness.state.auto_retry_enabled = False
    harness.state.auto_compaction_enabled = False
    return harness


class _Tui:
    def __init__(self, provider: _Provider, *, signals: bool = False) -> None:
        self.provider = provider
        self.harness = _real_harness(provider)
        self.signals = signals

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
