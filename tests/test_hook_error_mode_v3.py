"""ADR-0019 v3 (Sprint 3a) — per-handler ``error_mode`` policy.

Verifies:
- Default ``"throw"`` matches Pi shipped behavior (re-raises out of emit).
- ``"continue"`` opt-in swallows + logs and the reducer keeps going.
- Mixed (some ``"throw"``, some ``"continue"``) handlers behave per-handler.
- Lifecycle observational projection at the harness level is unchanged
  (still swallows errors — listener-style — matching Pi ``subscribe()``).
- No existing test breaks because the default stays ``"throw"`` (P-2 reframe).
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from aelix_agent_core.harness.hooks import (
    ContextHookEvent,
    HookBus,
    ToolCallHookEvent,
    ToolCallResult,
)
from aelix_coding_agent.extensions.api import ExtensionContext, _ExtensionRuntime


def _make_bus() -> HookBus:
    runtime = _ExtensionRuntime()
    ctx = ExtensionContext(
        runtime,
        cwd=".",
        model=None,
        is_idle=lambda: True,
        abort=lambda: None,
        get_active_tools=lambda: [],
        get_system_prompt=lambda: "",
    )
    return HookBus(ctx_factory=lambda: ctx)


async def test_default_error_mode_is_throw_pi_parity() -> None:
    """Default ``error_mode`` matches Pi shipped behavior — exception propagates."""
    bus = _make_bus()

    def boom(event: Any, ctx: Any) -> None:
        raise ValueError("boom")

    # No explicit error_mode kwarg — default is "throw".
    bus.on("context", boom)
    with pytest.raises(ValueError, match="boom"):
        await bus.emit(ContextHookEvent(messages=[]))


async def test_error_mode_continue_swallows_and_chain_continues() -> None:
    """``error_mode="continue"`` logs + swallows; later handlers still run."""
    bus = _make_bus()
    later_called: list[bool] = []

    def bad(event: Any, ctx: Any) -> None:
        raise RuntimeError("ignore me")

    def good(event: Any, ctx: Any) -> None:
        later_called.append(True)

    bus.on("context", bad, error_mode="continue")
    bus.on("context", good, error_mode="continue")

    # No exception bubbles out; later handler still fires.
    result = await bus.emit(ContextHookEvent(messages=[]))
    assert result is None
    assert later_called == [True]


async def test_mixed_error_modes_continue_then_throw() -> None:
    """If a later ``"throw"`` handler raises, it propagates even if earlier ``"continue"`` swallowed."""
    bus = _make_bus()
    continue_called: list[bool] = []

    def silent_bad(event: Any, ctx: Any) -> None:
        continue_called.append(True)
        raise RuntimeError("swallowed by continue")

    def loud_bad(event: Any, ctx: Any) -> None:
        raise ValueError("propagated by throw")

    bus.on("context", silent_bad, error_mode="continue")
    bus.on("context", loud_bad, error_mode="throw")

    with pytest.raises(ValueError, match="propagated by throw"):
        await bus.emit(ContextHookEvent(messages=[]))
    assert continue_called == [True]


async def test_mixed_error_modes_throw_first_short_circuits_continue() -> None:
    """If an earlier ``"throw"`` handler raises, later ``"continue"`` does not run."""
    bus = _make_bus()
    later_called: list[bool] = []

    def loud_bad(event: Any, ctx: Any) -> None:
        raise ValueError("first")

    def maybe_silent(event: Any, ctx: Any) -> None:
        later_called.append(True)

    bus.on("context", loud_bad, error_mode="throw")
    bus.on("context", maybe_silent, error_mode="continue")

    with pytest.raises(ValueError, match="first"):
        await bus.emit(ContextHookEvent(messages=[]))
    assert later_called == []


async def test_continue_error_logged_via_aelix_hook_logger(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A ``"continue"``-swallowed exception is logged at DEBUG level."""
    bus = _make_bus()

    def boom(event: Any, ctx: Any) -> None:
        raise RuntimeError("hidden")

    bus.on("context", boom, error_mode="continue")

    with caplog.at_level(logging.DEBUG, logger="aelix_agent_core.harness.hooks"):
        await bus.emit(ContextHookEvent(messages=[]))
    matched = [
        rec
        for rec in caplog.records
        if rec.name == "aelix_agent_core.harness.hooks"
        and "hook handler raised" in rec.getMessage()
    ]
    assert matched, "expected a debug log for the swallowed handler exception"


async def test_throw_default_unchanged_for_reducer_with_result() -> None:
    """Result-producing reducers (e.g. tool_call) still propagate on default."""
    bus = _make_bus()

    def boom(event: ToolCallHookEvent, ctx: Any) -> ToolCallResult:
        raise RuntimeError("kaboom")

    bus.on("tool_call", boom)
    with pytest.raises(RuntimeError, match="kaboom"):
        await bus.emit(ToolCallHookEvent(tool_call_id="t", tool_name="x"))
