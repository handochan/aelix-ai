"""§H.6 — state.messages source-flip tests (Sprint 4b §F).

When a :class:`Session` is attached, the per-turn ``_TurnState.messages``
list is derived from ``session.build_context().messages``. When no Session
is attached, the in-memory ``state.messages`` remains primary (backward
compat per ADR-0022 §"Aelix-additive divergences" item 3).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.session import MemorySessionStorage, Session
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)


def _stream() -> Any:
    async def fn(
        _m: Model, _c: Context, _o: SimpleStreamOptions
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")], stop_reason="end_turn"
            )
        )

    return fn


async def test_session_attached_turn_messages_derived_from_build_context() -> None:
    """When Session attached, the turn snapshot uses build_context.messages."""

    session = Session(MemorySessionStorage())
    h = AgentHarness(
        AgentHarnessOptions(stream_fn=_stream(), session=session)
    )
    # Capture _turn_state.messages length AT turn_start so we observe the
    # snapshot built inside _run() (not the post-turn appended state).
    observed_lengths: list[int] = []
    expected_session_lengths: list[int] = []

    async def on_turn_start(_ev: Any, _ctx: Any) -> None:
        # _turn_state is set inside _run BEFORE any hook fires.
        ts = h._turn_state  # type: ignore[attr-defined]
        observed_lengths.append(0 if ts is None else len(ts.messages))
        ctx = await session.build_context()
        expected_session_lengths.append(len(ctx.messages))

    h.hooks.on("turn_start", on_turn_start)
    await h.prompt("first")
    assert observed_lengths == expected_session_lengths


async def test_session_none_turn_messages_fall_back_to_state_messages() -> None:
    """When Session is None, turn messages come from state.messages."""

    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))
    observed_lengths: list[int] = []
    expected_state_lengths: list[int] = []

    async def on_turn_start(_ev: Any, _ctx: Any) -> None:
        ts = h._turn_state  # type: ignore[attr-defined]
        observed_lengths.append(0 if ts is None else len(ts.messages))
        expected_state_lengths.append(len(h.state.messages))

    h.hooks.on("turn_start", on_turn_start)
    await h.prompt("first")
    assert observed_lengths == expected_state_lengths
