"""§E.6 — message_end wiring tests (Sprint 4a; updated P0 #7 Wave 3, ADR-0145).

Pi parity (``agent-harness.ts:483-510`` + ``runner.ts:714``): every
``message_end`` event is persisted via ``session.appendMessage``. P0 #7
Wave 3 made ``message_end`` a REPLACEMENT reducer (ADR-0145, supersedes
ADR-0018), so the harness ``emit`` callback now REORDERS: the hook reduction
runs FIRST (to compute the possibly-replaced message), THEN
``session.append_message`` persists the REPLACEMENT, THEN the local listeners
fire. This module pins the final persisted state + the new ordering; the
replacement-semantics behavior lives in
``test_message_end_replacement_reducer.py``.
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


async def test_message_end_reduces_before_session_append() -> None:
    """P0 #7 Wave 3 (ADR-0145): the ``message_end`` hook reduction now runs
    BEFORE ``session.append_message`` so the harness can persist the
    REPLACEMENT (reorder vs the old observational persist-then-emit path).

    The loop emits ``message_end`` for both the user prompt
    (``loop.py``) and the assistant reply. The hook (a reducer) runs first,
    so at the moment each handler fires the CURRENT entry has NOT yet been
    persisted: the user-prompt handler sees 0 entries, and the assistant
    handler sees 1 (the user prompt, already persisted in its own emit cycle).
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

    # Two message_end events fire (user + assistant). The reduction runs
    # BEFORE the persist, so the current message is not yet appended when its
    # handler fires.
    assert observed_session_entry_count_at_emit == [0, 1]
    # Final persisted state: both messages landed.
    entries = await session.get_entries()
    assert len(entries) == 2


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
