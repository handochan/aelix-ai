"""Sprint 6h₂ (ADR-0071, P-248/P-253) — sync mode setters.

Pi parity: ``session.setSteeringMode`` (``rpc-mode.ts:585-588``) and
``session.setFollowUpMode`` (``rpc-mode.ts:590-593``).

The Aelix setters:

- validate ``mode in ("all", "one-at-a-time")`` → :exc:`ValueError`,
- update :attr:`AgentState.steering_mode` / :attr:`AgentState.follow_up_mode`,
- mutate the existing :class:`_MessageQueue` instance's ``mode`` so
  subsequent drains observe the new mode immediately,
- do NOT emit events (P-4 setter-no-emit rule).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
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


def _make_harness() -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            steering_mode="one-at-a-time",
            follow_up_mode="one-at-a-time",
            stream_fn=_stream(),
        )
    )


async def test_set_steering_mode_updates_state_and_queue() -> None:
    """Pi parity (P-248/P-253): both ``_state.steering_mode`` and
    ``_steering_queue.mode`` flip together.
    """

    h = _make_harness()
    try:
        assert h.state.steering_mode == "one-at-a-time"
        assert h._steering_queue.mode == "one-at-a-time"
        h.set_steering_mode("all")
        assert h.state.steering_mode == "all"
        assert h._steering_queue.mode == "all"
        # Public property reflects the new mode.
        assert h.steering_mode == "all"
    finally:
        await h.dispose()


async def test_set_follow_up_mode_updates_state_and_queue() -> None:
    h = _make_harness()
    try:
        assert h.state.follow_up_mode == "one-at-a-time"
        h.set_follow_up_mode("all")
        assert h.state.follow_up_mode == "all"
        assert h._follow_up_queue.mode == "all"
        assert h.follow_up_mode == "all"
    finally:
        await h.dispose()


async def test_set_steering_mode_invalid_raises_value_error() -> None:
    """Pi narrows at compile time; Aelix runtime-checks → :exc:`ValueError`."""

    h = _make_harness()
    try:
        with pytest.raises(ValueError, match="steering_mode"):
            h.set_steering_mode("bogus")
        # State unchanged on failure.
        assert h.state.steering_mode == "one-at-a-time"
        assert h._steering_queue.mode == "one-at-a-time"
    finally:
        await h.dispose()


async def test_set_follow_up_mode_invalid_raises_value_error() -> None:
    h = _make_harness()
    try:
        with pytest.raises(ValueError, match="follow_up_mode"):
            h.set_follow_up_mode("bogus")
        assert h.state.follow_up_mode == "one-at-a-time"
        assert h._follow_up_queue.mode == "one-at-a-time"
    finally:
        await h.dispose()


async def test_mode_setters_do_not_emit_events() -> None:
    """Pi parity (P-4): setters do NOT emit ``queue_update``."""

    h = _make_harness()
    seen: list[Any] = []
    for name in ("queue_update", "save_point", "settled"):
        h.hooks.on(name, lambda e, _c, _s=seen: _s.append(e))  # type: ignore[arg-type, call-overload]
    try:
        h.set_steering_mode("all")
        h.set_follow_up_mode("all")
        assert seen == []
    finally:
        await h.dispose()
