"""ADR-0030: ``_to_hook_event`` is exhaustive over ``AgentEvent``.

Runtime smoke test that every AgentEvent variant produces a HookEvent
(no ``None`` return). The pyright-level drift test is enforced separately
by ``scripts/pyright_spike.py`` and the CI pyright run — if a new
:data:`AgentEvent` variant is added without a matching ``case`` in
``_to_hook_event``, the runtime branch raises and pyright also reports
the unhandled variant via ``assert_never``.
"""

from __future__ import annotations

from typing import get_args

from aelix_agent_core.harness.core import _to_hook_event
from aelix_agent_core.harness.hooks import HookEvent
from aelix_agent_core.types import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from aelix_ai.messages import AssistantMessage
from aelix_ai.streaming import AssistantStartEvent
from aelix_ai.tools import ToolResult


def _stub_assistant_message() -> AssistantMessage:
    return AssistantMessage(content=[])


def _stub_tool_result() -> ToolResult:
    return ToolResult(content=[])


def test_to_hook_event_handles_every_agent_event_variant() -> None:
    """Every variant in :data:`AgentEvent` is either projected to a concrete
    :class:`HookEvent` (extension lifecycle observers) or is explicitly
    listener-only (auto_retry events — Sprint 6h₂₀ ADR-0128). The listener-only
    set is asserted by behavior: ``_to_hook_event`` raises ``RuntimeError`` if
    those events ever reach it (they're skipped at the caller).
    """

    from aelix_agent_core.types import AutoRetryEndEvent, AutoRetryStartEvent

    am = _stub_assistant_message()
    # Variants projected to HookEvent (every AgentEvent except the listener-only
    # auto_retry pair — see ``_run``'s ``emit`` closure skip-guard).
    projected_samples: list[AgentEvent] = [
        AgentStartEvent(),
        TurnStartEvent(),
        MessageStartEvent(message=am),
        MessageUpdateEvent(
            message=am,
            assistant_message_event=AssistantStartEvent(),
        ),
        MessageEndEvent(message=am),
        ToolExecutionStartEvent(tool_call_id="t1", tool_name="x", args={}),
        ToolExecutionUpdateEvent(
            tool_call_id="t1",
            partial_result=_stub_tool_result(),
        ),
        ToolExecutionEndEvent(tool_call_id="t1", result=_stub_tool_result()),
        TurnEndEvent(message=am, tool_results=[]),
        AgentEndEvent(messages=[]),
    ]
    # Listener-only variants — must not reach ``_to_hook_event``; documented by
    # an explicit raise inside the match.
    listener_only_samples: list[AgentEvent] = [
        AutoRetryStartEvent(
            attempt=1, max_attempts=3, delay_ms=2000, error_message="overloaded"
        ),
        AutoRetryEndEvent(success=False, attempt=1, final_error="x"),
    ]
    # Sanity: the union has no other variants beyond these two buckets.
    variant_count = len(get_args(AgentEvent))
    assert len(projected_samples) + len(listener_only_samples) == variant_count, (
        f"covered {len(projected_samples) + len(listener_only_samples)} variants "
        f"but AgentEvent has {variant_count}"
    )
    for ev in projected_samples:
        hook_ev = _to_hook_event(ev)
        assert isinstance(hook_ev, HookEvent)
        # Pi parity: the projected type literal matches the source type literal.
        assert hook_ev.type == ev.type
    for ev in listener_only_samples:
        # Documented invariant: these are skipped at the caller; reaching
        # ``_to_hook_event`` is a contract violation.
        import pytest as _pytest

        with _pytest.raises(RuntimeError, match=r"auto_retry event"):
            _to_hook_event(ev)


def test_to_hook_event_return_type_is_hook_event_not_optional() -> None:
    """Caller no longer guards on None — projection always succeeds."""
    out = _to_hook_event(AgentStartEvent())
    assert out is not None
    assert isinstance(out, HookEvent)
