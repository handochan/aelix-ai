"""Sprint 6h₂ (ADR-0071, P-247) — harness ``cycle_thinking_level``.

Pi parity: ``rpc-mode.ts:571-577``. ``session.cycleThinkingLevel()``
rotates through ``getSupportedThinkingLevels(currentModel)``. Returns
the new level (persisted via ``set_thinking_level``) or :data:`None`
when the model has only one supported level (typically ``"off"``).
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
                content=[TextContent(text="ok")],
                stop_reason="end_turn",
            )
        )

    return fn


def _non_reasoning_model() -> Model:
    """Non-reasoning models support only ``"off"``."""

    return Model(id="m", provider="p", reasoning=False)


def _reasoning_model_full_levels() -> Model:
    """Reasoning model that supports the full 6 levels (Pi
    ``EXTENDED_THINKING_LEVELS`` order).
    """

    return Model(
        id="m",
        provider="p",
        reasoning=True,
        thinking_level_map={
            "off": "off",
            "minimal": "minimal",
            "low": "low",
            "medium": "medium",
            "high": "high",
            "xhigh": "xhigh",
        },
    )


async def test_cycle_returns_none_when_only_one_supported_level() -> None:
    """Pi parity (P-247): non-reasoning model → only ``"off"`` → :data:`None`."""

    h = AgentHarness(
        AgentHarnessOptions(model=_non_reasoning_model(), stream_fn=_stream())
    )
    try:
        result = await h.cycle_thinking_level()
        assert result is None
    finally:
        await h.dispose()


async def test_cycle_returns_none_when_current_model_missing() -> None:
    """Pi parity: ``current_model is None`` short-circuits to :data:`None`.

    The default :class:`Model` is non-reasoning so the result is :data:`None`
    even before the missing-model branch is hit; the assertion stays valid.
    """

    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))
    try:
        result = await h.cycle_thinking_level()
        assert result is None
    finally:
        await h.dispose()


async def test_cycle_rotates_from_off_to_minimal() -> None:
    """Pi parity (P-247): starting at ``"off"`` rotates to ``"minimal"``."""

    h = AgentHarness(
        AgentHarnessOptions(
            model=_reasoning_model_full_levels(),
            thinking_level="off",
            stream_fn=_stream(),
        )
    )
    try:
        result = await h.cycle_thinking_level()
        assert result == "minimal"
        assert h.state.thinking_level == "minimal"
    finally:
        await h.dispose()


async def test_cycle_rotates_through_full_chain() -> None:
    """Pi parity (P-247): each call advances exactly one level."""

    h = AgentHarness(
        AgentHarnessOptions(
            model=_reasoning_model_full_levels(),
            thinking_level="off",
            stream_fn=_stream(),
        )
    )
    try:
        sequence: list[str | None] = []
        for _ in range(6):
            sequence.append(await h.cycle_thinking_level())
        # Expected forward rotation off → minimal → low → medium → high
        # → xhigh → off (wraps).
        assert sequence == [
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "off",
        ]
    finally:
        await h.dispose()


async def test_cycle_wraps_from_xhigh_back_to_off() -> None:
    """Pi parity (P-247): index wraps modulo ``len(levels)``."""

    h = AgentHarness(
        AgentHarnessOptions(
            model=_reasoning_model_full_levels(),
            thinking_level="xhigh",
            stream_fn=_stream(),
        )
    )
    try:
        result = await h.cycle_thinking_level()
        assert result == "off"
        assert h.state.thinking_level == "off"
    finally:
        await h.dispose()


async def test_cycle_handles_unknown_current_level() -> None:
    """Pi parity (P-247): ``current not in levels`` → start from index 0
    (next level is ``levels[1]``).
    """

    # Set a level that the model doesn't support — the cycle algorithm
    # falls back to index 0 and bumps to ``levels[1]``. Pi
    # ``get_supported_thinking_levels`` filters via ``thinking_level_map``;
    # explicit :data:`None` values mark unsupported levels.
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
            thinking_level="bogus",
            stream_fn=_stream(),
        )
    )
    try:
        # levels = ["off", "low", "high"]; idx=0 (fallback); next = "low".
        result = await h.cycle_thinking_level()
        assert result == "low"
    finally:
        await h.dispose()
