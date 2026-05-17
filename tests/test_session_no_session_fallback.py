"""§E.7 — no-session fallback tests (Sprint 4a).

Aelix-additive divergence (ADR-0022 §"Aelix-additive divergences"): the
harness permits ``session=None`` for Phase 1/2 backward compat. Pi assumes
session always present. The harness must continue to operate when no
Session is attached: message_end wiring is skipped, flush dispatcher
mirrors ``message`` into ``state.messages`` and drops the other 7 variants
with a debug log.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
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


async def test_prompt_works_without_session() -> None:
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))
    out = await h.prompt("hi")
    # Pi parity: loop returns user + assistant messages from this turn.
    assert len(out) == 2
    assert h.state.session_id is None


async def test_state_session_id_is_none_when_no_session_attached() -> None:
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))
    assert h.state.session_id is None
