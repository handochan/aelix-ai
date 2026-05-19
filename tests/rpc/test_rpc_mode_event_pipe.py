"""Pi parity: session events flow out via ``session.subscribe`` →
``output(event)`` without transformation (P-109, ``rpc-mode.ts:86-87``).

The fake harness emits :class:`AgentEvent` dataclasses; the RPC mode
serializes them to JSONL on stdout with the Pi-shape ``{type, ...}``
envelope (no wrapping ``response`` layer).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.types import AgentEndEvent
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.rpc._jsonl import serialize_json_line
from aelix_coding_agent.rpc.rpc_mode import run_rpc_mode


def _stream_fn_one_turn() -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")],
                stop_reason="end_turn",
            )
        )

    return fn


async def test_session_events_flow_to_stdout_without_response_wrapper() -> None:
    """Real prompt emits ``agent_start``/``agent_end`` events on stdout.

    Each event MUST carry its Pi-native ``type`` discriminator (e.g.
    ``"agent_end"``), NOT be wrapped in a ``{type: "response"}`` envelope.
    """

    stdin = asyncio.StreamReader()
    stdin.feed_data(
        serialize_json_line({"type": "prompt", "message": "hi", "id": "r1"}).encode()
    )
    # Defer EOF so the harness has time to run the prompt and emit events.
    captured: list[bytes] = []

    async def _defer_eof() -> None:
        await asyncio.sleep(0.2)
        stdin.feed_eof()

    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream_fn_one_turn(),
        )
    )

    await asyncio.gather(
        run_rpc_mode(
            harness,
            stdin=stdin,
            stdout_write=captured.append,
            install_signal_handlers=False,
        ),
        _defer_eof(),
    )

    records = [
        json.loads(line)
        for line in b"".join(captured).decode("utf-8").splitlines()
        if line.strip()
    ]
    # Filter to non-response records — these are session events.
    events = [r for r in records if r.get("type") != "response"]
    event_types = [e.get("type") for e in events]
    # The Pi parity invariant: the harness emits ``agent_start`` and
    # ``agent_end`` lifecycle events around the turn. Both must appear in
    # the JSONL stream without a ``response`` wrapper.
    assert "agent_start" in event_types
    assert "agent_end" in event_types


async def test_event_dataclass_serializes_to_pi_shape_dict() -> None:
    """A standalone AgentEndEvent dataclass serializes to ``{type, messages}``."""

    from aelix_coding_agent.rpc.rpc_mode import _event_to_dict

    event = AgentEndEvent(messages=[])
    record = _event_to_dict(event)
    assert record["type"] == "agent_end"
    assert "messages" in record


async def test_unsubscribe_on_shutdown_prevents_late_emissions() -> None:
    """After EOF the listener is removed; later events do NOT leak to stdout."""

    stdin = asyncio.StreamReader()
    stdin.feed_eof()
    captured: list[bytes] = []
    harness = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream_fn_one_turn(),
        )
    )
    # Subscribe a sentinel listener BEFORE run_rpc_mode so we can verify
    # the RPC mode itself unsubscribes its own listener on shutdown.
    pre_count = len(harness._listeners)
    await run_rpc_mode(
        harness,
        stdin=stdin,
        stdout_write=captured.append,
        install_signal_handlers=False,
    )
    # No RPC listener should remain after shutdown (Aelix listeners list
    # has at most the pre-existing sentinels).
    assert len(harness._listeners) == pre_count
