"""Tests for the stateful :class:`Agent` wrapper."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core import Agent, AgentEvent, AgentOptions, AgentState
from aelix_ai.messages import (
    AssistantMessage,
    TextContent,
    UserMessage,
)
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)


def _scripted_stream(finals: list[AssistantMessage]) -> Any:
    idx = {"i": 0}

    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        i = idx["i"]
        idx["i"] += 1
        if i >= len(finals):
            raise AssertionError(
                f"scripted stream exhausted at idx={i}"
            )
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=finals[i])

    return fn


def _text_final(text: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)],
        stop_reason="end_turn",
    )


async def test_prompt_appends_to_state_messages() -> None:
    a = Agent(
        AgentOptions(stream_fn=_scripted_stream([_text_final("hello back")]))
    )
    new_messages = await a.prompt("hi")
    # prompt + assistant reply
    assert len(new_messages) == 2
    assert isinstance(new_messages[0], UserMessage)
    assert isinstance(new_messages[1], AssistantMessage)
    # state accumulates the same messages
    assert a.state.messages == new_messages
    assert a.is_streaming is False


async def test_subscribe_receives_events() -> None:
    a = Agent(AgentOptions(stream_fn=_scripted_stream([_text_final("ok")])))
    seen: list[AgentEvent] = []
    a.subscribe(lambda e: seen.append(e))

    await a.prompt("hi")

    types = [e.type for e in seen]
    assert types[0] == "agent_start"
    assert types[-1] == "agent_end"
    assert "turn_end" in types


async def test_unsubscribe_stops_delivery() -> None:
    a = Agent(AgentOptions(stream_fn=_scripted_stream([_text_final("ok")])))
    seen: list[AgentEvent] = []
    unsub = a.subscribe(lambda e: seen.append(e))
    unsub()

    await a.prompt("hi")
    assert seen == []


async def test_consecutive_prompts_accumulate() -> None:
    a = Agent(
        AgentOptions(
            stream_fn=_scripted_stream(
                [_text_final("first"), _text_final("second")]
            )
        )
    )
    await a.prompt("one")
    await a.prompt("two")
    # 2 prompts + 2 assistant replies
    assert len(a.state.messages) == 4
    assert isinstance(a.state.messages[1], AssistantMessage)
    assert a.state.messages[1].content[0].text == "first"
    assert isinstance(a.state.messages[3], AssistantMessage)
    assert a.state.messages[3].content[0].text == "second"


async def test_steer_queues_message_for_drain() -> None:
    a = Agent()
    await a.steer("middle thought")
    drained = await a._drain_steering()
    assert len(drained) == 1
    assert isinstance(drained[0], UserMessage)
    assert drained[0].content[0].text == "middle thought"
    # Second drain returns nothing (queue empty)
    assert await a._drain_steering() == []


async def test_follow_up_queues_message_for_drain() -> None:
    a = Agent()
    await a.follow_up("after the loop would stop")
    drained = await a._drain_follow_up()
    assert len(drained) == 1
    assert drained[0].content[0].text == "after the loop would stop"


async def test_initial_state_is_used() -> None:
    state = AgentState(
        system_prompt="be brief",
        model=Model(id="xyz", provider="mock"),
    )
    a = Agent(
        AgentOptions(
            initial_state=state,
            stream_fn=_scripted_stream([_text_final("ok")]),
        )
    )
    assert a.state.system_prompt == "be brief"
    assert a.state.model.id == "xyz"

    await a.prompt("hi")
    assert a.state.messages[-1].content[0].text == "ok"
