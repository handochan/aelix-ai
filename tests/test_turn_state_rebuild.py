"""F-10 acceptance tests — turn-state snapshot rebuilt per ``prompt()``.

Spec reference: Sprint 2 / Phase 1.3 spec §E F-10.

Pre-fix bug: a ``before_agent_start`` handler that chained ``system_prompt``
would leak into subsequent turns, because the harness reused
``self._state.system_prompt`` across runs. After the fix, the chained prompt
lives in ``self._turn_state`` for the duration of one ``prompt()`` only.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import BeforeAgentStartHookEvent, BeforeAgentStartResult
from aelix_ai.messages import AssistantMessage, TextContent
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
            raise AssertionError(f"stream exhausted at idx={i}")
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(message=finals[i])

    return fn


def _text(text: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)],
        stop_reason="end_turn",
    )


async def test_second_prompt_sees_original_system_prompt() -> None:
    """A chained system_prompt must not leak into the next prompt()."""

    h = AgentHarness(
        AgentHarnessOptions(
            system_prompt="original",
            stream_fn=_scripted_stream([_text("one"), _text("two")]),
        )
    )

    chained_views: list[str] = []

    def chainer(event: BeforeAgentStartHookEvent, _ctx: Any) -> BeforeAgentStartResult:
        chained_views.append(event.system_prompt)
        return BeforeAgentStartResult(system_prompt=event.system_prompt + " :: extra")

    h.hooks.on("before_agent_start", chainer)

    await h.prompt("first")
    await h.prompt("second")

    # Each turn must have started from the persisted "original" prompt — no
    # leakage of the chained ":: extra" suffix into the second turn.
    assert chained_views == ["original", "original"]
    # And the persisted system_prompt remains untouched.
    assert h.state.system_prompt == "original"


async def test_get_system_prompt_during_turn_reflects_chained_prompt() -> None:
    """Inside a turn, ``_action_get_system_prompt`` returns the chained value."""

    h = AgentHarness(
        AgentHarnessOptions(
            system_prompt="base",
            stream_fn=_scripted_stream([_text("done")]),
        )
    )

    def chainer(event: BeforeAgentStartHookEvent, _ctx: Any) -> BeforeAgentStartResult:
        return BeforeAgentStartResult(system_prompt="chained")

    h.hooks.on("before_agent_start", chainer)

    # Subscribe a lifecycle listener that samples the in-turn system prompt.
    samples: list[str] = []
    long_lived_field_during_turn: list[str] = []

    def sample(event: Any) -> None:
        if event.type == "turn_start":
            samples.append(h._action_get_system_prompt())
            long_lived_field_during_turn.append(h.state.system_prompt)  # M-5: must remain "base"

    h.subscribe(sample)

    await h.prompt("hi")

    # During the turn, the action reports the chained value.
    assert samples == ["chained"]
    # The long-lived state field must NOT have been mutated to the chained value —
    # proves a future "simplification" that writes back into _state.system_prompt
    # before _run would be caught immediately.
    assert long_lived_field_during_turn == ["base"]
    # After the turn, it falls back to the persisted state.
    assert h._action_get_system_prompt() == "base"
