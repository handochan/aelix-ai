"""§E.6 — message_end wiring tests (Sprint 4a).

Pi parity (``agent-harness.ts:483-510``): every ``message_end`` event is
persisted via ``session.appendMessage`` BEFORE the observational hook
fan-out. Aelix Sprint 4a mirrors this in the harness ``emit`` callback
inside ``_run``.
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


async def test_message_end_appends_to_session_before_emit() -> None:
    """Pi parity: ``session.append_message`` runs BEFORE the observational
    hook emit so handlers see the entry already persisted.

    The loop emits ``message_end`` for both the user prompt
    (``loop.py:89``) and the assistant reply (``loop.py:279``). Each one
    must land in the session BEFORE the corresponding hook fires.
    """

    session = Session(MemorySessionStorage())
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream(), session=session))

    observed_session_entry_count_at_emit: list[int] = []

    async def hook(event: Any, _ctx: Any) -> Any:
        entries = await session.get_entries()
        observed_session_entry_count_at_emit.append(len(entries))
        return None

    h.hooks.on("message_end", hook)  # type: ignore[arg-type]
    await h.prompt("hi")

    # Two message_end events fire (user + assistant); the session has the
    # corresponding entry persisted at each emit moment.
    assert observed_session_entry_count_at_emit == [1, 2]


async def test_message_end_does_not_break_without_session() -> None:
    """Backward-compat path: when ``session is None`` the message_end emit
    chain still runs and no session call is attempted. Two events fire
    (user + assistant)."""

    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))
    fired: list[Any] = []
    h.hooks.on("message_end", lambda e, _c: fired.append(e))  # type: ignore[arg-type]
    await h.prompt("hi")
    assert len(fired) == 2


async def test_session_get_entries_has_user_and_assistant_after_turn() -> None:
    session = Session(MemorySessionStorage())
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream(), session=session))
    await h.prompt("hi")
    entries = await session.get_entries()
    # Two message_end events landed: the user prompt + the assistant reply.
    assert [e.type for e in entries] == ["message", "message"]
    roles = [getattr(e.message, "role", None) for e in entries]  # type: ignore[union-attr]
    assert roles == ["user", "assistant"]
