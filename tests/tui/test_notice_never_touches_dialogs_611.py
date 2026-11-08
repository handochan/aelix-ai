"""A notice never resolves, cancels or answers an open dialog (#722; Claude Code #85408's shape).

Owner decision 1 (2026-11-07): waiting for the user means an open dialog only,
and a notice never closes or answers one. A tool approval open mid-turn, a
parallel pair of them, and an extension's select open at idle must each be left
exactly as they are while a job's notice arrives: the dialog's future stays
pending, nothing aborts, and the notice waits for the boundary after the answer.

Fails on main ``a385c64a``: there is no inbox or waker, so no notice arrives at
all. Naive: delivering a notice by ``abort()`` + a new prompt (how the follow-up
path wakes a busy harness), or by answering the pending dialog to clear the way,
resolves the approval future — the bug reported against Claude Code (#85408)
where a background task's completion dismissed a permission prompt.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.inbox import Notice
from aelix_agent_core.types import AgentTool
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from aelix_ai.streaming import AssistantEndEvent, Model
from aelix_ai.tools import ToolResult
from aelix_coding_agent.tui.jobs_ui import JobsUi

from tests.jobs.conftest import fast_service, make_session


class _Provider:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.seen: list[list[Any]] = []

    async def __call__(self, _model: Any, context: Any, _options: Any = None) -> AsyncIterator[Any]:
        self.seen.append(list(context.messages))
        if len(self.seen) == 1:
            content = [ToolCallContent(tool_call_id=c, tool_name="needs_ok", input={}) for c in self.calls]
            yield AssistantEndEvent(message=AssistantMessage(content=content, stop_reason="tool_use"))
            return
        yield AssistantEndEvent(message=AssistantMessage(content=[TextContent(text="answer")]))


class _Chrome:
    def __init__(self) -> None:
        self.modal = False
        self.running = False
        self.queue: list[Any] = []

    def is_modal_open(self) -> bool:
        return self.modal

    def queue_wake(self, item: Any) -> None:
        self.queue.append(item)

    def invalidate(self) -> None:
        pass


class _Host:
    stopped = None
    reload_phase = None

    def __init__(self, harness: AgentHarness) -> None:
        self.harness = harness


def _notice(n: int = 1) -> Notice:
    return Notice(notice_id=f"job-{n}:final", custom_type="aelix.job_notice", body=f"[notice {n}]", brief="done")


async def _turn_with_dialogs(tmp_path: Path, calls: list[str]) -> tuple[AgentHarness, _Provider, dict[str, asyncio.Future[str]], asyncio.Task[Any]]:
    dialogs: dict[str, asyncio.Future[str]] = {}

    async def approval(event: Any, _ctx: Any) -> None:
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        dialogs[event.tool_call_id] = future
        await future  # the user's answer

    async def execute(_args: Any, _ctx: Any) -> ToolResult:
        return ToolResult(content=[TextContent(text="tool ran")])

    provider = _Provider(calls)
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", provider="p"),
            stream_fn=provider,
            session=await make_session(tmp_path),
            tools=[AgentTool(name="needs_ok", execute=execute)],
            notice_inbox=True,
        )
    )
    harness.hooks.on("tool_call", approval)
    turn = asyncio.ensure_future(harness.prompt("do it"))
    # The first call's approval opens; a second call's opens once the first is
    # answered (calls are prepared one by one, ``loop.py`` ``_prepare_tool_call``).
    while not dialogs:
        await asyncio.sleep(0.01)
    return harness, provider, dialogs, turn


async def test_a_notice_never_resolves_or_cancels_an_open_dialog(tmp_path: Path) -> None:
    harness, provider, dialogs, turn = await _turn_with_dialogs(tmp_path, ["c1"])
    chrome = _Chrome()
    chrome.modal = True
    ui = JobsUi(fast_service(), chrome=chrome, runtime_host=_Host(harness))
    ui.parked = False  # the input loop is inside harness.prompt

    harness.post_notice(_notice())
    await asyncio.sleep(0.05)

    assert not dialogs["c1"].done(), "the dialog's future is untouched"
    assert not turn.done() and not harness.is_idle, "nothing aborted"
    assert harness.pending_notices == 1, "not placed while the dialog is open"
    assert chrome.queue == [], "no wake while a turn runs or a dialog is open"

    dialogs["c1"].set_result("allow")
    await asyncio.wait_for(turn, 5)
    second = provider.seen[1]
    tool_at = next(i for i, m in enumerate(second) if isinstance(m, ToolResultMessage))
    notice_at = next(
        i for i, m in enumerate(second) if isinstance(m, UserMessage) and m.content[0].text == "[notice 1]"  # type: ignore[union-attr]
    )
    assert notice_at > tool_at, "placed at the boundary after the approval"
    ui.close()


async def test_parallel_tool_approval(tmp_path: Path) -> None:
    harness, provider, dialogs, turn = await _turn_with_dialogs(tmp_path, ["c1", "c2"])
    harness.post_notice(_notice())
    await asyncio.sleep(0.05)
    assert not dialogs["c1"].done() and "c2" not in dialogs
    dialogs["c1"].set_result("allow")
    while "c2" not in dialogs:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    assert not dialogs["c2"].done(), "the other dialog is still the user's"
    assert harness.pending_notices == 1, "the batch is not over"
    dialogs["c2"].set_result("allow")
    await asyncio.wait_for(turn, 5)
    assert harness.pending_notices == 0
    assert any(
        isinstance(m, UserMessage) and m.content[0].text == "[notice 1]"  # type: ignore[union-attr]
        for m in provider.seen[1]
    )


async def test_idle_extension_select_dialog_holds_the_wake(tmp_path: Path) -> None:
    from tests.jobs.conftest import RecordingModel, make_harness

    harness = make_harness(await make_session(tmp_path), RecordingModel())
    service = fast_service()
    await service.bind(harness)
    chrome = _Chrome()
    ui = JobsUi(service, chrome=chrome, runtime_host=_Host(harness))
    ui.parked = True
    chrome.modal = True  # an extension's ctx.ui.select, opened at idle
    ui.force = True  # as if live (G7 is not what this test is about)
    harness.post_notice(_notice())
    assert chrome.queue == [] and ui.gates() == "G2: a dialog is open"
    chrome.modal = False
    assert ui.maybe_wake() and len(chrome.queue) == 1, "re-checked when the dialog closes"
    ui.close()


async def test_sentinel_queued_then_modal_opens_before_dequeue(tmp_path: Path) -> None:
    from tests.jobs.conftest import RecordingModel, make_harness

    harness = make_harness(await make_session(tmp_path), RecordingModel())
    chrome = _Chrome()
    ui = JobsUi(fast_service(), chrome=chrome, runtime_host=_Host(harness))
    ui.parked = True
    ui.force = True
    harness.post_notice(_notice())
    assert len(chrome.queue) == 1
    chrome.modal = True  # opened before the input loop took the item
    assert not ui.take(), "dropped at dequeue: the dialog is open"
    assert harness.pending_notices == 1, "the notice is untouched"
    chrome.modal = False
    ui.parked = True
    assert ui.maybe_wake(), "queued again once the dialog is gone"
    assert ui.take()
    result = await harness.prompt_notices()
    assert result.started
    ui.close()
