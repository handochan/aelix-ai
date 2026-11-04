"""What #672 and #673 (ADR-0290) pin their rows with: a batch of calls, then a cancel.

Results are read where they matter: live state, the reopened JSONL file, and the
next request after the wire transform every adapter runs.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.session import JsonlSessionStorage, LocalFileSystem, Session
from aelix_agent_core.types import AgentEvent, AgentTool
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
)
from aelix_ai.providers._transform_messages import transform_messages
from aelix_ai.streaming import AssistantEndEvent, Model
from aelix_ai.tools import ToolResult

MODEL = Model(id="mock", provider="mock", api="mock")
NOT_STARTED = "No result provided"
CANCEL_WAYS = ["abort", "withdrawing_abort", "dispose", "outer_cancel"]


def text_of(message: Any) -> str:
    return "".join(b.text for b in message.content if isinstance(b, TextContent))


def results_of(messages: list[Any]) -> dict[str, tuple[str, bool]]:
    """Each result as ``id: (text, is_error)``; a second result for one id fails here."""

    out: dict[str, tuple[str, bool]] = {}
    for message in messages:
        if isinstance(message, ToolResultMessage):
            assert message.tool_call_id not in out, f"two results for {message.tool_call_id}"
            out[message.tool_call_id] = (text_of(message), message.is_error)
    return out


def order_of(messages: list[Any]) -> list[str]:
    return [m.tool_call_id for m in messages if isinstance(m, ToolResultMessage)]


class Provider:
    """The first request answers with ``calls``, every later one with text."""

    def __init__(self, calls: list[tuple[str, str]]) -> None:
        self.calls = [
            ToolCallContent(tool_call_id=call_id, tool_name=name, input={})
            for call_id, name in calls
        ]
        self.seen: list[list[Any]] = []

    async def __call__(self, _model: Any, context: Any, _options: Any) -> AsyncIterator[Any]:
        self.seen.append(list(context.messages))
        if len(self.seen) == 1:
            yield AssistantEndEvent(
                message=AssistantMessage(content=list(self.calls), stop_reason="tool_use")
            )
            return
        yield AssistantEndEvent(message=AssistantMessage(content=[TextContent(text="answer")]))


def finished_tool(name: str, text: str, *, sequential: bool = False) -> AgentTool:
    async def execute(_args: Any, _ctx: Any) -> ToolResult:
        return ToolResult(content=[TextContent(text=text)])

    return AgentTool(
        name=name, execute=execute, execution_mode="sequential" if sequential else None
    )


def held_tool(name: str, reached: asyncio.Event, *, sequential: bool = False) -> AgentTool:
    """Runs until something cancels it."""

    async def execute(_args: Any, _ctx: Any) -> ToolResult:
        reached.set()
        await asyncio.Event().wait()
        return ToolResult(content=[TextContent(text="never")])

    return AgentTool(
        name=name, execute=execute, execution_mode="sequential" if sequential else None
    )


@dataclass
class Run:
    harness: AgentHarness
    provider: Provider
    path: Path | None
    events: list[AgentEvent] = field(default_factory=list)
    task: asyncio.Task[Any] | None = None

    def types(self, *names: str) -> list[str]:
        return [e.type for e in self.events if e.type in names]

    def ended(self) -> list[str]:
        return [e.tool_call_id for e in self.events if e.type == "tool_execution_end"]

    def started(self) -> list[str]:
        return [e.tool_call_id for e in self.events if e.type == "tool_execution_start"]

    async def reopened(self) -> list[Any]:
        assert self.path is not None
        session = Session(await JsonlSessionStorage.open(LocalFileSystem(), str(self.path)))
        return list((await session.build_context()).messages)

    async def next_request(self) -> list[Any]:
        """What the next prompt puts on the wire, past the transform."""

        await self.harness.prompt("NEW")
        return list(transform_messages(self.provider.seen[-1], MODEL))

    def begin(self) -> asyncio.Task[Any]:
        self.task = asyncio.create_task(self.harness.prompt("KEEP"))
        return self.task

    async def cancel(self, way: str = "abort") -> None:
        """Cancel the turn the way ``way`` names, and let it finish."""

        assert self.task is not None
        if way == "abort":
            await self.harness.abort()
        elif way == "withdrawing_abort":
            await self.harness.abort(withdraw_unanswered=True)
        elif way == "dispose":
            await self.harness.dispose()
        else:
            assert way == "outer_cancel"
            self.task.cancel()
        await settle(self.task)


async def settle(task: asyncio.Task[Any]) -> None:
    """The prompt's end: an abort returns, an outer cancel raises. Neither hangs."""

    await asyncio.wait([task], timeout=10)
    assert task.done(), "the turn did not end"
    if not task.cancelled():
        task.result()


async def start(
    tmp_path: Path,
    calls: list[tuple[str, str]],
    tools: list[AgentTool],
    *,
    attached: bool = True,
    **options: Any,
) -> Run:
    path = tmp_path / "session.jsonl" if attached else None
    session = None
    if path is not None:
        session = Session(
            await JsonlSessionStorage.create(
                LocalFileSystem(), str(path), cwd=str(tmp_path), session_id="issue-672-673"
            )
        )
    provider = Provider(calls)
    harness = AgentHarness(
        AgentHarnessOptions(
            model=MODEL, stream_fn=provider, session=session, tools=tools, **options
        )
    )
    harness.state.auto_retry_enabled = False
    harness.state.auto_compaction_enabled = False
    run = Run(harness, provider, path)
    harness.subscribe(lambda event: run.events.append(event))
    return run


async def until(predicate: Callable[[], bool], *, turns: int = 2000) -> None:
    for _ in range(turns):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("never reached")


async def until_real(predicate: Callable[[], bool], *, timeout: float = 20.0) -> None:
    """The same on a clock, for a real tool or process, which no count of loop turns can bound."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        assert loop.time() < deadline, "never reached"
        await asyncio.sleep(0.01)
