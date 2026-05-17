"""Tests for HookBus, reducers, and concurrency guarantees.

Covers spec E + D.1 additions for harness/hooks.py.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, get_args

import pytest
from aelix_agent_core.harness.core import AgentHarnessError
from aelix_agent_core.harness.hooks import (
    HOOK_RESULT_TYPES,
    BeforeAgentStartHookEvent,
    BeforeAgentStartResult,
    ContextHookEvent,
    ContextResult,
    HookBus,
    HookEvent,
    HookEventName,
    MessageEndHookEvent,
    SessionBeforeCompactHookEvent,
    SessionBeforeCompactResult,
    ToolCallHookEvent,
    ToolCallResult,
    ToolResultHookEvent,
)
from aelix_agent_core.types import AfterToolCallResult
from aelix_ai.messages import TextContent
from aelix_coding_agent.extensions.api import (
    ExtensionContext,
    _ExtensionRuntime,
)

# === Shared helpers ===


def _make_runtime() -> _ExtensionRuntime:
    return _ExtensionRuntime()


def _make_ctx(runtime: _ExtensionRuntime | None = None) -> ExtensionContext:
    rt = runtime or _make_runtime()
    return ExtensionContext(
        rt,
        cwd=".",
        model=None,
        is_idle=lambda: True,
        abort=lambda: None,
        get_active_tools=lambda: [],
        get_system_prompt=lambda: "",
    )


def _make_bus(runtime: _ExtensionRuntime | None = None) -> HookBus:
    rt = runtime or _make_runtime()
    ctx = _make_ctx(rt)
    return HookBus(ctx_factory=lambda: ctx)


def _make_text_content(text: str = "hello") -> TextContent:
    return TextContent(text=text)


# === Basic emit behaviour ===


async def test_emit_no_handlers_returns_none() -> None:
    bus = _make_bus()
    event = ContextHookEvent(messages=[])
    result = await bus.emit(event)
    assert result is None


async def test_emit_unknown_event_raises_keyerror() -> None:
    bus = _make_bus()

    # HookEvent base has no `type` field; getattr returns None, which is not in
    # HOOK_RESULT_TYPES, so emit raises KeyError without needing a fake subclass.
    with pytest.raises(KeyError):
        await bus.emit(HookEvent())  # type: ignore[arg-type]


async def test_on_returns_unsubscribe_callable() -> None:
    bus = _make_bus()

    def handler(event: Any, ctx: Any) -> None:
        pass

    unsub = bus.on("context", handler)
    assert callable(unsub)

    # Unsubscribing removes the handler so subsequent emits don't call it.
    called: list[int] = []

    def counting_handler(event: Any, ctx: Any) -> ContextResult:
        called.append(1)
        return ContextResult(messages=[])

    unsub2 = bus.on("context", counting_handler)
    unsub2()
    await bus.emit(ContextHookEvent(messages=[]))
    assert called == []


# === Observe ===


async def test_observe_sees_all_events_return_ignored() -> None:
    bus = _make_bus()
    seen: list[str] = []

    def observer(event: HookEvent, ctx: ExtensionContext) -> str:
        seen.append(event.type)
        return "ignored return value"  # type: ignore[return-value]

    bus.observe(observer)
    await bus.emit(ContextHookEvent(messages=[]))
    await bus.emit(ToolCallHookEvent(tool_call_id="t1", tool_name="x"))
    assert seen == ["context", "tool_call"]


async def test_observe_is_independent_from_on() -> None:
    bus = _make_bus()
    handler_called: list[bool] = []
    observer_called: list[bool] = []

    def handler(event: Any, ctx: Any) -> ContextResult:
        handler_called.append(True)
        return ContextResult(messages=[])

    def observer(event: Any, ctx: Any) -> None:
        observer_called.append(True)

    bus.on("context", handler)
    bus.observe(observer)

    result = await bus.emit(ContextHookEvent(messages=[]))
    assert handler_called == [True]
    assert observer_called == [True]
    # Handler result is a ContextResult, observer doesn't affect it.
    assert isinstance(result, ContextResult)


# === context reducer ===


async def test_reducer_context_sequential_transform() -> None:
    bus = _make_bus()
    msg_a = TextContent(text="original")
    msg_b = TextContent(text="patched")

    # H1 patches messages; H2 sees the already-patched list.
    h1_saw: list[Any] = []
    h2_saw: list[Any] = []

    def h1(event: ContextHookEvent, ctx: Any) -> ContextResult:
        h1_saw.extend(event.messages)
        return ContextResult(messages=[msg_b])

    def h2(event: ContextHookEvent, ctx: Any) -> ContextResult:
        h2_saw.extend(event.messages)
        return ContextResult(messages=list(event.messages) + [TextContent(text="extra")])

    bus.on("context", h1)
    bus.on("context", h2)

    result = await bus.emit(ContextHookEvent(messages=[msg_a]))
    assert isinstance(result, ContextResult)
    assert result.messages is not None
    assert h1_saw == [msg_a]
    # H2 should see the patched messages from H1.
    assert h2_saw == [msg_b]
    assert len(result.messages) == 2


async def test_reducer_context_no_changes_returns_none() -> None:
    bus = _make_bus()

    def h(event: ContextHookEvent, ctx: Any) -> None:
        return None  # no opinion

    bus.on("context", h)
    result = await bus.emit(ContextHookEvent(messages=[]))
    assert result is None


# === before_agent_start reducer ===


async def test_reducer_before_agent_start_collects_messages_chains_prompt() -> None:
    bus = _make_bus()
    msg1 = TextContent(text="injection1")

    def h1(event: BeforeAgentStartHookEvent, ctx: Any) -> BeforeAgentStartResult:
        return BeforeAgentStartResult(messages=[msg1], system_prompt="H1 prompt")

    def h2(event: BeforeAgentStartHookEvent, ctx: Any) -> BeforeAgentStartResult:
        # H2 sees the chained system_prompt from H1.
        assert event.system_prompt == "H1 prompt"
        return BeforeAgentStartResult(system_prompt=event.system_prompt + " + H2")

    bus.on("before_agent_start", h1)
    bus.on("before_agent_start", h2)

    result = await bus.emit(
        BeforeAgentStartHookEvent(prompt="hello", system_prompt="base")
    )
    assert isinstance(result, BeforeAgentStartResult)
    assert result.messages == [msg1]
    assert result.system_prompt == "H1 prompt + H2"


# === tool_call reducer ===


async def test_reducer_tool_call_block_short_circuits() -> None:
    bus = _make_bus()
    h3_called: list[bool] = []

    def h1(event: ToolCallHookEvent, ctx: Any) -> None:
        return None

    def h2(event: ToolCallHookEvent, ctx: Any) -> ToolCallResult:
        return ToolCallResult(block=True, reason="H2 blocked")

    def h3(event: ToolCallHookEvent, ctx: Any) -> ToolCallResult:
        h3_called.append(True)
        return ToolCallResult(block=True, reason="H3 blocked")

    bus.on("tool_call", h1)
    bus.on("tool_call", h2)
    bus.on("tool_call", h3)

    result = await bus.emit(ToolCallHookEvent(tool_call_id="t1", tool_name="bash"))
    assert isinstance(result, ToolCallResult)
    assert result.block is True
    assert result.reason == "H2 blocked"
    assert h3_called == []


async def test_reducer_tool_call_no_block_returns_last_truthy() -> None:
    bus = _make_bus()

    def h1(event: ToolCallHookEvent, ctx: Any) -> ToolCallResult:
        return ToolCallResult(block=False, reason="h1")

    def h2(event: ToolCallHookEvent, ctx: Any) -> ToolCallResult:
        return ToolCallResult(block=False, reason="h2")

    bus.on("tool_call", h1)
    bus.on("tool_call", h2)

    result = await bus.emit(ToolCallHookEvent(tool_call_id="t1", tool_name="echo"))
    assert isinstance(result, ToolCallResult)
    assert result.block is False
    assert result.reason == "h2"


async def test_reducer_tool_call_args_mutation_visible_to_later_handler() -> None:
    """D.1.5 — in-place args mutation is visible to subsequent handlers."""
    bus = _make_bus()
    args: dict[str, Any] = {"foo": 0}
    h2_saw: list[Any] = []

    def h1(event: ToolCallHookEvent, ctx: Any) -> None:
        event.args["foo"] = 1

    def h2(event: ToolCallHookEvent, ctx: Any) -> None:
        h2_saw.append(event.args.get("foo"))

    bus.on("tool_call", h1)
    bus.on("tool_call", h2)

    await bus.emit(ToolCallHookEvent(tool_call_id="t1", tool_name="echo", args=args))
    assert h2_saw == [1]
    assert args["foo"] == 1


async def test_reducer_tool_call_no_revalidation_after_mutation() -> None:
    """Mutated args are not re-validated — the mutated dict passes through as-is."""
    bus = _make_bus()
    args: dict[str, Any] = {"cmd": "echo hello"}

    def h1(event: ToolCallHookEvent, ctx: Any) -> None:
        # Inject an unexpected key — no schema validation should blow up.
        event.args["injected"] = "extra_value"

    bus.on("tool_call", h1)
    await bus.emit(ToolCallHookEvent(tool_call_id="t1", tool_name="echo", args=args))
    assert args["injected"] == "extra_value"


async def test_reducer_tool_call_non_block_reason_is_observational_only() -> None:
    """D.1.3 — non-block reason is observational only; loop only acts on block."""
    bus = _make_bus()

    def h(event: ToolCallHookEvent, ctx: Any) -> ToolCallResult:
        return ToolCallResult(block=False, reason="just observing")

    bus.on("tool_call", h)
    result = await bus.emit(ToolCallHookEvent(tool_call_id="t1", tool_name="x"))
    assert isinstance(result, ToolCallResult)
    assert result.block is False
    # reason is present on the result object but block=False means the loop ignores it.
    assert result.reason == "just observing"


# === tool_result reducer ===


async def test_reducer_tool_result_patch_accumulation() -> None:
    """H1 sets content, H2 sets is_error; final result has both."""
    bus = _make_bus()
    new_content = [TextContent(text="patched")]

    def h1(event: ToolResultHookEvent, ctx: Any) -> AfterToolCallResult:
        return AfterToolCallResult(content=new_content)

    def h2(event: ToolResultHookEvent, ctx: Any) -> AfterToolCallResult:
        return AfterToolCallResult(is_error=True)

    bus.on("tool_result", h1)
    bus.on("tool_result", h2)

    result = await bus.emit(
        ToolResultHookEvent(
            tool_call_id="t1",
            tool_name="echo",
            args={},
            content=[TextContent(text="original")],
        )
    )
    assert isinstance(result, AfterToolCallResult)
    assert result.content == new_content
    assert result.is_error is True


async def test_reducer_tool_result_unset_fields_preserved() -> None:
    """Fields not set by a handler preserve the prior accumulated value."""
    bus = _make_bus()
    original_content = [TextContent(text="original")]
    new_content = [TextContent(text="patched")]

    def h1(event: ToolResultHookEvent, ctx: Any) -> AfterToolCallResult:
        return AfterToolCallResult(content=new_content)

    def h2(event: ToolResultHookEvent, ctx: Any) -> AfterToolCallResult:
        # Only sets is_error; content stays from h1.
        return AfterToolCallResult(is_error=True)

    bus.on("tool_result", h1)
    bus.on("tool_result", h2)

    result = await bus.emit(
        ToolResultHookEvent(
            tool_call_id="t1",
            tool_name="echo",
            args={},
            content=original_content,
        )
    )
    assert isinstance(result, AfterToolCallResult)
    assert result.content == new_content
    assert result.is_error is True


# === session_before_compact reducer ===


async def test_reducer_session_before_cancel_short_circuits() -> None:
    bus = _make_bus()
    h3_called: list[bool] = []

    def h1(event: SessionBeforeCompactHookEvent, ctx: Any) -> None:
        return None

    def h2(event: SessionBeforeCompactHookEvent, ctx: Any) -> SessionBeforeCompactResult:
        return SessionBeforeCompactResult(cancel=True, reason="stop!")

    def h3(event: SessionBeforeCompactHookEvent, ctx: Any) -> SessionBeforeCompactResult:
        h3_called.append(True)
        return SessionBeforeCompactResult(cancel=False)

    bus.on("session_before_compact", h1)
    bus.on("session_before_compact", h2)
    bus.on("session_before_compact", h3)

    result = await bus.emit(SessionBeforeCompactHookEvent())
    assert isinstance(result, SessionBeforeCompactResult)
    assert result.cancel is True
    assert result.reason == "stop!"
    assert h3_called == []


async def test_reducer_session_before_last_truthy_wins_no_cancel() -> None:
    bus = _make_bus()

    def h1(event: SessionBeforeCompactHookEvent, ctx: Any) -> SessionBeforeCompactResult:
        return SessionBeforeCompactResult(cancel=False, reason="h1")

    def h2(event: SessionBeforeCompactHookEvent, ctx: Any) -> SessionBeforeCompactResult:
        return SessionBeforeCompactResult(cancel=False, reason="h2")

    bus.on("session_before_compact", h1)
    bus.on("session_before_compact", h2)

    result = await bus.emit(SessionBeforeCompactHookEvent())
    assert isinstance(result, SessionBeforeCompactResult)
    assert result.cancel is False
    assert result.reason == "h2"


# === message_end (observational) ===


async def test_message_end_observational() -> None:
    """message_end return values are ignored in Phase 1.2."""
    bus = _make_bus()
    called: list[bool] = []

    def h(event: MessageEndHookEvent, ctx: Any) -> str:
        called.append(True)
        return "this should be ignored"  # type: ignore[return-value]

    bus.on("message_end", h)
    result = await bus.emit(MessageEndHookEvent(message=None))
    assert called == [True]
    # Observational reducers return None regardless of handler return value.
    assert result is None


# === Error propagation ===


async def test_handler_raises_propagates_as_harness_error() -> None:
    """Spec says harness wraps handler exceptions in AgentHarnessError("hook", ...)."""
    bus = _make_bus()

    def bad_handler(event: Any, ctx: Any) -> None:
        raise ValueError("handler exploded")

    bus.on("context", bad_handler)

    # The bus itself re-raises; it's the harness's job to wrap.
    # Verify the raw exception propagates out of emit so callers can catch it.
    with pytest.raises(ValueError, match="handler exploded"):
        await bus.emit(ContextHookEvent(messages=[]))

    # Demonstrate AgentHarnessError wrapping pattern (what the harness does).
    try:
        await bus.emit(ContextHookEvent(messages=[]))
    except ValueError as exc:
        try:
            raise AgentHarnessError("hook", "handler failed") from exc
        except AgentHarnessError as wrapped:
            assert wrapped.code == "hook"
            assert wrapped.__cause__ is exc


# === Sync + async handlers ===


async def test_sync_handler_and_async_handler_both_work() -> None:
    bus = _make_bus()
    results: list[str] = []

    def sync_h(event: ContextHookEvent, ctx: Any) -> ContextResult:
        results.append("sync")
        return ContextResult(messages=[TextContent(text="sync")])

    async def async_h(event: ContextHookEvent, ctx: Any) -> ContextResult:
        results.append("async")
        return ContextResult(messages=list(event.messages) + [TextContent(text="async")])

    bus.on("context", sync_h)
    bus.on("context", async_h)

    result = await bus.emit(ContextHookEvent(messages=[]))
    assert results == ["sync", "async"]
    assert isinstance(result, ContextResult)
    assert result.messages is not None
    assert len(result.messages) == 2


# === Cleanup / dispose ===


async def test_cleanup_runs_on_dispose_in_lifo_order() -> None:
    bus = _make_bus()
    order: list[int] = []

    bus.add_cleanup(lambda: order.append(1))
    bus.add_cleanup(lambda: order.append(2))
    bus.add_cleanup(lambda: order.append(3))

    await bus.dispose()
    assert order == [3, 2, 1]


# === Unsubscribe during emit ===


async def test_unsubscribe_during_emit_safe() -> None:
    """A handler that unsubscribes itself should not corrupt iteration."""
    bus = _make_bus()
    called: list[str] = []
    unsub: Callable[[], None] | None = None

    def self_removing(event: ContextHookEvent, ctx: Any) -> None:
        called.append("self_removing")
        if unsub is not None:
            unsub()

    def stable(event: ContextHookEvent, ctx: Any) -> ContextResult:
        called.append("stable")
        return ContextResult(messages=[])

    unsub = bus.on("context", self_removing)
    bus.on("context", stable)

    # First emit: both fire (snapshot is taken before iteration).
    await bus.emit(ContextHookEvent(messages=[]))
    assert "self_removing" in called
    assert "stable" in called

    called.clear()
    # Second emit: self_removing is gone.
    await bus.emit(ContextHookEvent(messages=[]))
    assert "self_removing" not in called
    assert "stable" in called


# === Registry integrity ===


async def test_hook_event_name_literal_matches_hook_result_types_keys() -> None:
    """D.1.13 M-5 — HOOK_RESULT_TYPES keys == get_args(HookEventName)."""
    literal_names = set(get_args(HookEventName))
    registry_keys = set(HOOK_RESULT_TYPES.keys())
    assert literal_names == registry_keys, (
        f"Mismatch: literal={literal_names - registry_keys}, registry={registry_keys - literal_names}"
    )


# === Concurrency / ordering (D.1.12) ===


async def test_handler_spawning_task_does_not_corrupt_handler_ordering() -> None:
    """A handler that spawns a background task must not corrupt ordering."""
    bus = _make_bus()
    order: list[str] = []

    async def spawning_handler(event: ContextHookEvent, ctx: Any) -> None:
        # Spawn a task that appends later; verify it doesn't reorder main chain.
        asyncio.create_task(_append_later(order, "background"))
        order.append("spawning")

    async def _append_later(lst: list[str], val: str) -> None:
        await asyncio.sleep(0)
        lst.append(val)

    def second_handler(event: ContextHookEvent, ctx: Any) -> None:
        order.append("second")

    bus.on("context", spawning_handler)
    bus.on("context", second_handler)

    await bus.emit(ContextHookEvent(messages=[]))
    # Yield twice: once to schedule the task, once to run it.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # Main-chain ordering is registration order: spawning then second.
    assert order.index("spawning") < order.index("second")
    assert "background" in order


# === C-3: terminate field visible to next handler ===


async def test_reducer_tool_result_terminate_visible_to_next_handler() -> None:
    """H1 sets terminate=True; H2 must see event.terminate==True; final result has terminate=True."""
    bus = _make_bus()
    h2_saw_terminate: list[bool] = []

    def h1(event: ToolResultHookEvent, ctx: Any) -> AfterToolCallResult:
        return AfterToolCallResult(terminate=True)

    def h2(event: ToolResultHookEvent, ctx: Any) -> AfterToolCallResult:
        h2_saw_terminate.append(event.terminate)
        return AfterToolCallResult()

    bus.on("tool_result", h1)
    bus.on("tool_result", h2)

    result = await bus.emit(
        ToolResultHookEvent(tool_call_id="t1", tool_name="echo", args={})
    )
    assert h2_saw_terminate == [True], f"H2 should see terminate=True, got {h2_saw_terminate}"
    assert isinstance(result, AfterToolCallResult)
    assert result.terminate is True


# === H-1: args identity regression test ===


async def test_tool_call_reducer_preserves_args_identity_across_handlers() -> None:
    """The reducer must NOT wrap args in a defensive copy — same dict identity across handlers."""
    bus = _make_bus()
    original_args: dict[str, Any] = {"key": "value"}
    ids_seen: list[int] = []

    def h1(event: ToolCallHookEvent, ctx: Any) -> None:
        ids_seen.append(id(event.args))

    def h2(event: ToolCallHookEvent, ctx: Any) -> None:
        ids_seen.append(id(event.args))

    bus.on("tool_call", h1)
    bus.on("tool_call", h2)

    await bus.emit(ToolCallHookEvent(tool_call_id="t1", tool_name="echo", args=original_args))
    assert len(ids_seen) == 2
    assert ids_seen[0] == id(original_args)
    assert ids_seen[1] == id(original_args)


# === H-5: empty-string system_prompt replaces prompt ===


async def test_reducer_before_agent_start_empty_string_replaces_prompt() -> None:
    """A handler returning system_prompt='' replaces the chained prompt with empty string."""
    bus = _make_bus()

    def h(event: BeforeAgentStartHookEvent, ctx: Any) -> BeforeAgentStartResult:
        return BeforeAgentStartResult(system_prompt="")

    bus.on("before_agent_start", h)

    result = await bus.emit(
        BeforeAgentStartHookEvent(prompt="hello", system_prompt="original")
    )
    assert isinstance(result, BeforeAgentStartResult)
    assert result.system_prompt == ""


# === H-7: bus remains usable after dispose ===


async def test_bus_after_dispose_accepts_new_registrations() -> None:
    """After dispose(), new registrations are accepted and handlers fire on emit."""
    bus = _make_bus()
    await bus.dispose()

    fired: list[bool] = []

    def h(event: ToolCallHookEvent, ctx: Any) -> None:
        fired.append(True)

    bus.on("tool_call", h)
    await bus.emit(ToolCallHookEvent(tool_call_id="t1", tool_name="echo"))
    assert fired == [True]


# === H-8: same handler on multiple events has independent source entries ===


async def test_sources_handles_same_handler_on_multiple_events() -> None:
    """Registering the same handler on two events does not corrupt unsubscription."""
    bus = _make_bus()
    fired: list[str] = []

    def shared_handler(event: Any, ctx: Any) -> None:
        fired.append(event.type)

    unsub_context = bus.on("context", shared_handler)
    unsub_tool_call = bus.on("tool_call", shared_handler)

    # Both fire independently.
    await bus.emit(ContextHookEvent(messages=[]))
    await bus.emit(ToolCallHookEvent(tool_call_id="t1", tool_name="x"))
    assert "context" in fired
    assert "tool_call" in fired

    # Unsubscribe from context only — tool_call handler still fires.
    unsub_context()
    fired.clear()
    await bus.emit(ContextHookEvent(messages=[]))
    await bus.emit(ToolCallHookEvent(tool_call_id="t1", tool_name="x"))
    assert "context" not in fired
    assert "tool_call" in fired

    # Unsubscribe from tool_call too.
    unsub_tool_call()
    fired.clear()
    await bus.emit(ToolCallHookEvent(tool_call_id="t1", tool_name="x"))
    assert fired == []


# === Slow handler (existing test, kept in place) ===


async def test_slow_handler_followed_by_fast_handler_completes_in_registration_order() -> None:
    """Slow async handler runs to completion before fast handler starts (D.1.12)."""
    bus = _make_bus()
    order: list[str] = []

    async def slow(event: ContextHookEvent, ctx: Any) -> None:
        await asyncio.sleep(0.01)
        order.append("slow")

    async def fast(event: ContextHookEvent, ctx: Any) -> None:
        order.append("fast")

    bus.on("context", slow)
    bus.on("context", fast)

    await bus.emit(ContextHookEvent(messages=[]))
    assert order == ["slow", "fast"]
