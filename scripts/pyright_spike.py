# pyright: strict
"""Pyright narrowing spike for D.1.2 (CR-2 + H-7).

Goal: verify that ``ExtensionAPI.on(event_name, handler)`` narrows the handler
signature to the per-event Result type via @overload + Literal, without relying
on ``Protocol[TResult]`` generic carry-through.

Run: ``uv run pyright --strict .omc/specs/_pyright_spike.py``
Expected: 0 errors, and the `reveal_type` lines show concrete result types.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, overload

# === Minimal event + result definitions ===


HookEventName = Literal["tool_call", "context"]


@dataclass(frozen=True)
class HookEvent:
    """Phantom base; per-event subclasses don't parameterize at the type level."""


@dataclass(frozen=True)
class ToolCallHookEvent(HookEvent):
    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextHookEvent(HookEvent):
    messages: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ToolCallResult:
    block: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class ContextResult:
    messages: list[str] | None = None


# Context passed alongside event.
class ExtensionContext:
    cwd: str = ""


# === Handler type aliases per event ===


ToolCallHandler = Callable[
    [ToolCallHookEvent, ExtensionContext],
    ToolCallResult | None | Awaitable[ToolCallResult | None],
]
ContextHandler = Callable[
    [ContextHookEvent, ExtensionContext],
    ContextResult | None | Awaitable[ContextResult | None],
]


# === ExtensionAPI.on with overload ===


class ExtensionAPI:
    @overload
    def on(
        self,
        event_name: Literal["tool_call"],
        handler: ToolCallHandler,
    ) -> Callable[[], None]: ...

    @overload
    def on(
        self,
        event_name: Literal["context"],
        handler: ContextHandler,
    ) -> Callable[[], None]: ...

    def on(self, event_name: HookEventName, handler: Any) -> Callable[[], None]:
        del event_name, handler
        return lambda: None


# === Usage proves narrowing ===


api = ExtensionAPI()


async def my_tool_call_handler(
    event: ToolCallHookEvent, ctx: ExtensionContext
) -> ToolCallResult | None:
    if event.tool_name == "bash":
        return ToolCallResult(block=True, reason="bash blocked")
    return None


def my_context_handler(
    event: ContextHookEvent, ctx: ExtensionContext
) -> ContextResult | None:
    return ContextResult(messages=[*event.messages, "extra"])


# These should pyright-check cleanly. If narrowing is broken, pyright will
# complain that the handler signature doesn't match the overload.
api.on("tool_call", my_tool_call_handler)
api.on("context", my_context_handler)


# Inverse cases — pyright MUST emit errors for narrowing to be considered working:
api.on("badevent", my_tool_call_handler)  # expect: Literal violation
api.on("tool_call", lambda x: x)  # expect: arity mismatch (lambda takes 1, needs 2)
api.on("tool_call", my_context_handler)  # expect: handler signature mismatch
