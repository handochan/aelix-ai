"""#376 review round 3 — the harness asks whether a turn can run, AFTER the ``input`` hook.

pi's ``prompt`` runs the extension input handlers (``_runInputHandlers``,
``agent-session.ts:1993`` at ``pi@1cedd3272``) and only then validates the
model and its auth (``:2032-2050``), before it records anything. Round 2 of
#376 asked in RPC's ``_handle_prompt`` and the TUI's input loop, before the
hook ran: an extension that handled the input itself (``InputHandled``) on a
keyless setup was refused (it worked on ``8f7d98aa``), a model an ``input``
handler switched to was never judged, and an extension's
``send_message(..., trigger_turn=True)`` skipped the question entirely.

Now :meth:`AgentHarness.prompt` asks the bound question
(:meth:`AgentHarness.set_prompt_check`) at one point every prompt path goes
through, after the hook and before the user message is built, and
``on_accept`` is pi's ``preflightResult``.

Review round 4: the CLI's question asks no credential (owner decision
2026-10-08), so the model these rows refuse is one with no adapter
(``_NOWHERE``); the question itself is injected here, the harness does not care
what it asks.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
)
from aelix_agent_core.harness.hooks import InputHandled, InputTransform
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)

WAIT = 10.0
_RUNS = Model(id="runs", provider="p", api="anthropic-messages")
_NOWHERE = Model(id="nowhere", provider="q", api="no-such-api")
_REASON = "No adapter for api 'no-such-api'."


def _stream(sent: list[str]) -> Any:
    async def fn(
        model: Model, context: Context, options: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        sent.append(model.id)
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
        )

    return fn


def _harness(model: Model) -> tuple[AgentHarness, Session, list[str], list[str]]:
    """A harness on ``model`` whose question refuses ``_NOWHERE``; it records
    every model it is asked about, and every model a request went out on."""

    session = Session(MemorySessionStorage())
    sent: list[str] = []
    asked: list[str] = []
    h = AgentHarness(AgentHarnessOptions(model=model, stream_fn=_stream(sent), session=session))

    def check(m: Model) -> str | None:
        asked.append(m.id)
        return _REASON if m.provider == "q" else None

    h.set_prompt_check(check)
    return h, session, asked, sent


async def _messages(session: Session) -> list[Any]:
    return [e for e in await session.get_branch() if e.type == "message"]


async def test_a_refused_prompt_raises_and_writes_nothing() -> None:
    h, session, asked, sent = _harness(_NOWHERE)
    accepted: list[str] = []

    with pytest.raises(AgentHarnessError, match="No adapter for api") as info:
        await h.prompt("hi", on_accept=accepted.append)

    assert info.value.code == "invalid_state"
    assert asked == ["nowhere"]
    assert accepted == []
    assert sent == []
    assert await _messages(session) == []
    assert h.state.messages == []
    assert h.phase == "idle"
    await h.dispose()


async def test_an_input_handled_by_an_extension_is_never_asked() -> None:
    """pi: a handled input returns before the model is validated."""

    h, session, asked, sent = _harness(_NOWHERE)
    accepted: list[str] = []
    h.hooks.on("input", lambda event, _ctx=None: InputHandled())  # type: ignore[arg-type]

    assert await h.prompt("ping", on_accept=accepted.append) == []

    assert asked == []
    assert accepted == ["handled"]
    assert sent == []
    assert await _messages(session) == []
    assert h.phase == "idle"
    await h.dispose()


async def test_the_model_an_input_handler_switched_to_is_the_one_asked_about() -> None:
    h, session, asked, sent = _harness(_RUNS)

    async def switch(event: Any, _ctx: Any = None) -> None:
        await h.set_model(_NOWHERE)

    h.hooks.on("input", switch)  # type: ignore[arg-type]

    with pytest.raises(AgentHarnessError, match="No adapter for api"):
        await h.prompt("hi")

    assert asked == ["nowhere"]
    assert sent == []
    assert await _messages(session) == []
    await h.dispose()


async def test_the_question_comes_after_the_hook_and_before_the_message_is_written() -> None:
    """Order: the ``input`` hook, then the question, then ``on_accept("started")``
    with nothing of the prompt written yet, then the turn."""

    session = Session(MemorySessionStorage())
    sent: list[str] = []
    h = AgentHarness(AgentHarnessOptions(model=_RUNS, stream_fn=_stream(sent), session=session))
    order: list[str] = []
    written_at_accept: list[int] = []

    def transform(event: Any, _ctx: Any = None) -> Any:
        order.append(f"input:{event.text}")
        return InputTransform(text="rewritten")

    def check(m: Model) -> str | None:
        order.append("check")
        return None

    def accept(disposition: str) -> None:
        order.append(disposition)
        written_at_accept.append(len(h.state.messages))

    h.hooks.on("input", transform)  # type: ignore[arg-type]
    h.set_prompt_check(check)

    await h.prompt("hi", on_accept=accept)

    assert order == ["input:hi", "check", "started"]
    assert written_at_accept == [0]
    assert sent == ["runs"]
    texts = [
        c.text
        for e in await _messages(session)
        if isinstance(e.message, UserMessage)
        for c in e.message.content
        if isinstance(c, TextContent)
    ]
    assert texts == ["rewritten"]
    await h.dispose()


async def test_a_triggered_turn_is_asked_too_and_writes_nothing() -> None:
    """Codex r2 cat1: an extension's ``send_message(..., trigger_turn=True)``
    never met the mode-level gates, so a turn on a model that cannot run ran, failed and
    was written."""

    h, session, asked, sent = _harness(_NOWHERE)

    h._action_send_message(  # the extension API's ``send_message``
        UserMessage(content=[TextContent(text="triggered")]), trigger_turn=True
    )
    outcomes = await asyncio.wait_for(
        asyncio.gather(*list(h._pending_tasks), return_exceptions=True), WAIT
    )

    assert [str(o) for o in outcomes] == [_REASON]

    assert asked == ["nowhere"]
    assert sent == []
    assert await _messages(session) == []
    assert h.state.messages == []
    assert h.phase == "idle"
    await h.dispose()


async def test_with_no_question_bound_nothing_is_asked() -> None:
    """An embedder's harness binds none: the turn runs as before #376."""

    session = Session(MemorySessionStorage())
    sent: list[str] = []
    h = AgentHarness(AgentHarnessOptions(model=_NOWHERE, stream_fn=_stream(sent), session=session))

    await h.prompt("hi")

    assert sent == ["nowhere"]
    assert len(await _messages(session)) == 2
    await h.dispose()


async def test_the_turn_gates_refused_turn_is_not_asked() -> None:
    """#367: a ``trigger_turn`` while turns are held runs as a refused turn that
    sends nothing and wakes a handler awaiting its ``agent_end``. It is not the
    question's to refuse."""

    h, _session, asked, sent = _harness(_NOWHERE)
    ended = asyncio.Event()
    h.subscribe(lambda event: ended.set() if getattr(event, "type", "") == "agent_end" else None)
    h.hold_turns("held")

    h._action_send_message(UserMessage(content=[TextContent(text="x")]), trigger_turn=True)
    await asyncio.wait_for(ended.wait(), WAIT)
    await asyncio.wait_for(
        asyncio.gather(*list(h._pending_tasks), return_exceptions=True), WAIT
    )

    assert asked == []
    assert sent == []
    h.hold_turns(None)
    await h.dispose()
