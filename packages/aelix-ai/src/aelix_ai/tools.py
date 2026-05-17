"""Tool and ToolResult types.

Follows the pi-ai tool shape: ``name``, ``description``, JSON-Schema-shaped
``parameters``, and an async ``execute`` callable. Validation is intentionally
minimal in Phase 1.1 — the schema is carried through but not enforced. A real
validator (``jsonschema`` or ``pydantic``) is planned for a later phase.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from aelix_ai.messages import ImageContent, TextContent

# Tool result content reuses the same text/image content blocks as messages.
ToolContent = list[TextContent | ImageContent]


# Sprint 3d (P-9): partial-result callback type. Pi parity ——
# ``AgentToolUpdateCallback`` in ``packages/agent/src/types.ts:357-358`` is a
# sync ``(partialResult) => void``; Aelix tolerates both sync and async return
# types so handlers and tool authors can fan out without an awkward
# ``inspect.iscoroutine`` dance. The runtime (`_execute_and_finalize`) drains
# every scheduled emission before returning, mirroring Pi's
# ``await Promise.all(updateEvents)`` at ``agent-loop.ts:630``.
#
# Note: Pi ``AgentToolUpdateCallback`` is strictly sync (``void``); Aelix
# accepts ``Awaitable[None]`` for type-compat only — the runtime does NOT
# await user-returned awaitables (matches Pi semantics). Only the
# runtime-scheduled internal emission task is drained.
ToolPartialCallback = Callable[["ToolResult"], Awaitable[None] | None]


@dataclass(frozen=True)
class ToolExecutionContext:
    """Minimal context passed to a tool's ``execute`` callable.

    Phase 1.1 keeps this lightweight. Harness layers will extend it with
    session manager, UI handles, audit metadata, and cancellation in later
    phases.

    Sprint 3d (P-9) adds the optional ``on_partial`` callback. Pi parity:
    ``AgentToolUpdateCallback`` (``types.ts:357-358``) — fire-and-forget; the
    runtime guarantees every partial-emit fan-out is drained before the tool's
    final result is returned to the loop (mirrors Pi
    ``await Promise.all(updateEvents)`` at ``agent-loop.ts:630``).

    ``on_partial`` is ``None`` when no harness is registered (bare-loop
    callers); tools MUST tolerate the ``None`` value before invoking it.
    """

    tool_call_id: str = ""
    signal: Any | None = None  # AbortSignal placeholder for Phase 2
    # Sprint 3d (P-9). See ``ToolPartialCallback`` for the Pi-equivalent
    # contract.
    on_partial: ToolPartialCallback | None = None


@dataclass(frozen=True)
class ToolResult:
    """Result returned by a tool ``execute`` callable.

    ``terminate`` is a hint that the loop may stop after the current batch.
    The loop only honors it when every finalized tool result in the batch
    sets it (matches pi-agent-core semantics).
    """

    content: ToolContent = field(default_factory=list)
    details: Any | None = None
    is_error: bool = False
    terminate: bool = False


# An async callable that performs the tool's work.
ToolExecute = Callable[
    [dict[str, Any], ToolExecutionContext],
    Awaitable[ToolResult],
]


@dataclass(frozen=True)
class Tool:
    """Provider-agnostic tool definition.

    ``parameters`` is a JSON-Schema-shaped ``dict`` so any provider adapter
    can emit the right wire format. ``execute`` is optional at the type level
    so Tool definitions can be registered before their handler is bound — the
    agent loop will refuse to call a tool whose ``execute`` is ``None``.
    """

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    execute: ToolExecute | None = None


async def validate_tool_arguments(
    tool: Tool, args: dict[str, Any]
) -> dict[str, Any]:
    """Phase 1.1 minimal validator: returns ``args`` as a shallow copy.

    Phase 2 will add real JSON Schema validation (``jsonschema`` or
    ``pydantic``) and surface validation errors as ``isError`` tool results.
    """

    return dict(args)


__all__ = [
    "Tool",
    "ToolContent",
    "ToolExecute",
    "ToolExecutionContext",
    "ToolPartialCallback",
    "ToolResult",
    "validate_tool_arguments",
]
