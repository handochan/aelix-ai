"""Sprint 6h₂ (ADR-0071, P-247) — ``cycle_thinking_level`` RPC handler.

Pi parity: ``rpc-mode.ts:571-577``. Response shape:

- ``{success: true, command: "cycle_thinking_level", data: null}`` when
  the harness has nothing to cycle (single-level model).
- ``{success: true, command: "cycle_thinking_level", data: {level: ...}}``
  on rotation.
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
from aelix_coding_agent.rpc.rpc_mode import _handle_cycle_thinking_level
from aelix_coding_agent.rpc.rpc_types import (
    RpcCommandCycleThinkingLevel,
    RpcSuccessResponse,
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
                content=[TextContent(text="ok")],
                stop_reason="end_turn",
            )
        )

    return fn


async def test_cycle_thinking_level_returns_null_data_for_single_level_model() -> None:
    """Non-reasoning model → only ``"off"`` → data is :data:`None`."""

    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(id="m", provider="p", reasoning=False),
            stream_fn=_stream(),
        )
    )
    try:
        cmd = RpcCommandCycleThinkingLevel(id="r1")
        response = await _handle_cycle_thinking_level(h, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "cycle_thinking_level"
        assert response.id == "r1"
        assert response.data is None
    finally:
        await h.dispose()


async def test_cycle_thinking_level_returns_level_dict_on_rotation() -> None:
    """Pi shape: ``{level: <next_thinking_level>}``."""

    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(
                id="m",
                provider="p",
                reasoning=True,
                thinking_level_map={
                    "off": "off",
                    "minimal": None,
                    "low": "low",
                    "medium": None,
                    "high": "high",
                },
            ),
            thinking_level="off",
            stream_fn=_stream(),
        )
    )
    try:
        cmd = RpcCommandCycleThinkingLevel(id="r2")
        response = await _handle_cycle_thinking_level(h, cmd)
        assert isinstance(response, RpcSuccessResponse)
        assert response.command == "cycle_thinking_level"
        # Pi supported levels: ["off", "low", "high"]; rotating from "off"
        # advances to "low".
        assert response.data == {"level": "low"}
        assert h.state.thinking_level == "low"
    finally:
        await h.dispose()
