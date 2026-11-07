"""``before_agent_start`` says what started the run (#611 S1a).

An additive field: every run today is a user's message, so every event says
``source="interactive"``, and the reducer that hands each handler its own copy
of the event keeps it. #611 S1b's notice turns will say ``"notice"``; nothing
emits that yet. On main ``fc8f3048`` the event has no ``source`` at all.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, get_args

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import (
    BeforeAgentStartHookEvent,
    BeforeAgentStartResult,
    BeforeAgentStartSource,
)
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import AssistantEndEvent, AssistantMessageEvent, AssistantStartEvent


async def _stream(*_args: Any, **_kwargs: Any) -> AsyncIterator[AssistantMessageEvent]:
    yield AssistantStartEvent(partial=AssistantMessage(content=[]))
    yield AssistantEndEvent(
        message=AssistantMessage(content=[TextContent(text="ok")], stop_reason="end_turn")
    )


def test_the_field_defaults_to_interactive_and_names_two_sources() -> None:
    assert BeforeAgentStartHookEvent().source == "interactive"
    assert set(get_args(BeforeAgentStartSource)) == {"interactive", "notice"}


async def test_a_prompt_emits_interactive_and_every_chained_handler_sees_it() -> None:
    seen: list[str] = []

    def first(event: BeforeAgentStartHookEvent, _ctx: Any) -> BeforeAgentStartResult:
        seen.append(event.source)
        return BeforeAgentStartResult(system_prompt="changed by the first handler")

    def second(event: BeforeAgentStartHookEvent, _ctx: Any) -> None:
        assert event.system_prompt == "changed by the first handler", "a chained copy"
        seen.append(event.source)

    harness = AgentHarness(AgentHarnessOptions(stream_fn=_stream))
    harness.hooks.on("before_agent_start", first)
    harness.hooks.on("before_agent_start", second)

    await harness.prompt("hello")

    assert seen == ["interactive", "interactive"]


async def test_the_reducer_keeps_a_notice_source_for_the_next_handler() -> None:
    seen: list[str] = []

    def first(event: BeforeAgentStartHookEvent, _ctx: Any) -> BeforeAgentStartResult:
        return BeforeAgentStartResult(system_prompt="x")

    def second(event: BeforeAgentStartHookEvent, _ctx: Any) -> None:
        seen.append(event.source)

    harness = AgentHarness(AgentHarnessOptions(stream_fn=_stream))
    harness.hooks.on("before_agent_start", first)
    harness.hooks.on("before_agent_start", second)

    await harness.hooks.emit(BeforeAgentStartHookEvent(prompt="p", source="notice"))

    assert seen == ["notice"]
