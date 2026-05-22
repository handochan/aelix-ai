"""Sprint 6h₇c §E (Phase 5a-iii-γ, ADR-0093) — session lifecycle emit tests.

Pi parity: ``emitSessionShutdownEvent`` (``runner.ts:177-189``) +
``session_start`` emit site (``agent-session.ts:2407``) (P-448).

Verifies:

- :meth:`AgentHarness._emit_session_shutdown` returns ``False`` and
  does NOT emit when no handler is registered.
- :meth:`AgentHarness._emit_session_shutdown` returns ``True`` and
  emits :class:`SessionShutdownHookEvent` with the right ``reason``
  when a handler is registered.
- Symmetric coverage for
  :meth:`AgentHarness._emit_session_start` and
  :class:`SessionStartHookEvent`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import AgentHarness, AgentHarnessOptions
from aelix_agent_core.harness.hooks import (
    SessionShutdownHookEvent,
    SessionStartHookEvent,
)
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.extensions.api import Extension


def _stream() -> Any:
    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantEndEvent(
            message=AssistantMessage(
                content=[TextContent(text="ok")], stop_reason="end_turn"
            )
        )

    return fn


def _new_harness(
    *,
    extensions: list[Extension] | None = None,
) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream(),
            extensions=extensions or [],
        )
    )


# ──────────────────────────────────────────────────────────────────
# _emit_session_shutdown
# ──────────────────────────────────────────────────────────────────


async def test_emit_session_shutdown_no_handler_returns_false() -> None:
    """When no extension handler is registered, returns False + no emit."""

    harness = _new_harness()

    result = await harness._emit_session_shutdown("reload")

    assert result is False


async def test_emit_session_shutdown_with_handler_returns_true() -> None:
    """When a handler is registered, returns True and fires the event."""

    observed: list[SessionShutdownHookEvent] = []

    async def handler(event: SessionShutdownHookEvent, ctx: Any) -> None:
        observed.append(event)

    ext = Extension(name="watcher")
    ext.handlers["session_shutdown"] = [handler]
    harness = _new_harness(extensions=[ext])

    result = await harness._emit_session_shutdown("reload")

    assert result is True
    assert len(observed) == 1
    assert observed[0].reason == "reload"
    assert observed[0].type == "session_shutdown"


async def test_emit_session_shutdown_propagates_quit_reason() -> None:
    """``reason`` field flows verbatim to the emitted event."""

    observed: list[SessionShutdownHookEvent] = []

    async def handler(event: SessionShutdownHookEvent, ctx: Any) -> None:
        observed.append(event)

    ext = Extension(name="w")
    ext.handlers["session_shutdown"] = [handler]
    harness = _new_harness(extensions=[ext])

    await harness._emit_session_shutdown("quit")

    assert observed[0].reason == "quit"


# ──────────────────────────────────────────────────────────────────
# _emit_session_start
# ──────────────────────────────────────────────────────────────────


async def test_emit_session_start_no_handler_returns_false() -> None:
    """When no extension handler is registered, returns False + no emit."""

    harness = _new_harness()

    result = await harness._emit_session_start("reload")

    assert result is False


async def test_emit_session_start_with_handler_returns_true() -> None:
    """When a handler is registered, returns True and fires the event."""

    observed: list[SessionStartHookEvent] = []

    async def handler(event: SessionStartHookEvent, ctx: Any) -> None:
        observed.append(event)

    ext = Extension(name="watcher")
    ext.handlers["session_start"] = [handler]
    harness = _new_harness(extensions=[ext])

    result = await harness._emit_session_start("reload")

    assert result is True
    assert len(observed) == 1
    assert observed[0].reason == "reload"
    assert observed[0].type == "session_start"


async def test_emit_session_start_propagates_startup_reason() -> None:
    observed: list[SessionStartHookEvent] = []

    async def handler(event: SessionStartHookEvent, ctx: Any) -> None:
        observed.append(event)

    ext = Extension(name="w")
    ext.handlers["session_start"] = [handler]
    harness = _new_harness(extensions=[ext])

    await harness._emit_session_start("startup")

    assert observed[0].reason == "startup"


# ──────────────────────────────────────────────────────────────────
# Symmetric isolation — one event's handler does NOT fire the other
# ──────────────────────────────────────────────────────────────────


async def test_emit_session_start_does_not_fire_shutdown_handler() -> None:
    """``has_handlers`` predicate isolates by event name."""

    fired: list[str] = []

    async def shutdown_handler(event: SessionShutdownHookEvent, ctx: Any) -> None:
        fired.append("shutdown")

    ext = Extension(name="w")
    ext.handlers["session_shutdown"] = [shutdown_handler]
    harness = _new_harness(extensions=[ext])

    # Only a session_shutdown handler is registered.
    result = await harness._emit_session_start("reload")

    assert result is False
    assert fired == []
