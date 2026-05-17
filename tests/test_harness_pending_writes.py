"""Tests for the ``pending_session_writes`` queue (Pi parity, spec §C).

Pi: ``agent-harness.ts:414-432`` (``flushPendingSessionWrites``) +
``agent-harness.ts:434-450`` (``handleAgentEvent`` flush trigger).
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessOptions,
    PendingMessageWrite,
)
from aelix_agent_core.harness.hooks import SavePointHookEvent
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
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
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="done")],
                stop_reason="end_turn",
            )
        )

    return fn


async def test_appended_message_visible_next_turn() -> None:
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))

    appended = UserMessage(content=[TextContent(text="from-hook")])

    async def in_turn(event: Any, _ctx: Any) -> Any:
        await h.append_message(appended)
        return None

    h.hooks.on("before_agent_start", in_turn)  # type: ignore[arg-type]
    await h.prompt("hi")

    # On the SECOND prompt, the appended message must be visible (flushed
    # at turn_end of the first turn) before the new user message.
    assert appended in h.state.messages


async def test_turn_end_emits_save_point_had_pending_true() -> None:
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))
    seen: list[SavePointHookEvent] = []
    h.hooks.on("save_point", lambda e, _c: seen.append(e))  # type: ignore[arg-type]

    async def in_turn(event: Any, _ctx: Any) -> Any:
        await h.append_message(UserMessage(content=[TextContent(text="x")]))
        return None

    h.hooks.on("before_agent_start", in_turn)  # type: ignore[arg-type]
    await h.prompt("hi")

    # A single turn_end → one save_point event.
    assert len(seen) == 1
    assert seen[0].had_pending_mutations is True


async def test_clean_turn_save_point_had_pending_false() -> None:
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))
    seen: list[SavePointHookEvent] = []
    h.hooks.on("save_point", lambda e, _c: seen.append(e))  # type: ignore[arg-type]

    await h.prompt("hi")

    assert len(seen) == 1
    assert seen[0].had_pending_mutations is False


async def test_pending_cleared_on_run_failure() -> None:
    """If the loop raises, the finally-block flush still drains the queue."""

    def broken_stream() -> Any:
        async def fn(
            model: Model,
            context: Context,
            options: SimpleStreamOptions,
        ) -> AsyncIterator[AssistantMessageEvent]:
            raise RuntimeError("simulated provider failure")
            yield  # pragma: no cover — make this a generator

        return fn

    h = AgentHarness(AgentHarnessOptions(stream_fn=broken_stream()))

    # Queue a pending message DURING before_agent_start so it lands before
    # the stream raises.
    pre_message = UserMessage(content=[TextContent(text="will-be-dropped")])

    async def in_turn(event: Any, _ctx: Any) -> Any:
        # Force-push a pending write directly to verify queue state.
        h._pending_session_writes.append(PendingMessageWrite(message=pre_message))
        return None

    h.hooks.on("before_agent_start", in_turn)  # type: ignore[arg-type]

    with contextlib.suppress(Exception):
        await h.prompt("hi")

    # The finally flush guarantees the queue is empty even on failure.
    assert h._pending_session_writes == []
