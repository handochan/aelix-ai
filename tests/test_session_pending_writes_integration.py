"""§E.5 — 8-arm flush + no-session fallback + P-11 regression (Sprint 4a)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessOptions,
    PendingCustomMessageWrite,
    PendingCustomWrite,
    PendingLabelWrite,
    PendingLeafWrite,
    PendingMessageWrite,
    PendingModelChangeWrite,
    PendingSessionInfoWrite,
    PendingSessionWrite,
    PendingThinkingLevelChangeWrite,
)
from aelix_agent_core.session import (
    MemorySessionStorage,
    Session,
)
from aelix_ai.messages import AssistantMessage, TextContent, UserMessage
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
                content=[TextContent(text="done")],
                stop_reason="end_turn",
            )
        )

    return fn


def _new_session() -> Session:
    return Session(MemorySessionStorage())


async def test_p11_regression_no_active_tools_change_variant() -> None:
    """P-11 LOCKDOWN: ``PendingSessionWrite`` union has no
    ``active_tools_change`` arm. Verified by introspecting the union's
    type names.
    """

    type_names = {
        cls.__name__
        for cls in (
            PendingMessageWrite,
            PendingModelChangeWrite,
            PendingThinkingLevelChangeWrite,
            PendingCustomWrite,
            PendingCustomMessageWrite,
            PendingLabelWrite,
            PendingSessionInfoWrite,
            PendingLeafWrite,
        )
    }
    assert "PendingActiveToolsChangeWrite" not in type_names
    # The union has exactly 8 variants.
    args = PendingSessionWrite.__args__  # type: ignore[attr-defined]
    assert len(args) == 8
    arg_names = {a.__name__ for a in args}
    assert "PendingActiveToolsChangeWrite" not in arg_names


async def test_8_arm_dispatch_with_session_routes_all() -> None:
    """Inject all 8 variants synthetically and verify each routes to a
    corresponding ``session.append_*`` (or ``set_leaf_id``) call."""

    session = _new_session()
    # Seed the session with one entry so the leaf-target ID exists for
    # PendingLabelWrite and PendingLeafWrite targets.
    seed_id = await session.append_message(
        UserMessage(content=[TextContent(text="seed")])
    )

    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream(), session=session))
    h._pending_session_writes.extend(
        [
            PendingMessageWrite(
                message=UserMessage(content=[TextContent(text="m1")])
            ),
            PendingModelChangeWrite(provider="anthropic", model_id="claude-x"),
            PendingThinkingLevelChangeWrite(thinking_level="high"),
            PendingCustomWrite(custom_type="ct", data={"x": 1}),
            PendingCustomMessageWrite(
                custom_type="cm",
                content="text",
                display=True,
                details=None,
            ),
            PendingLabelWrite(target_id=seed_id, label="checkpoint"),
            PendingSessionInfoWrite(name="my session"),
            PendingLeafWrite(target_id=seed_id),
        ]
    )

    await h.flush_pending_session_writes()
    entries = await session.get_entries()
    # Seed (1) + the 7 append_* arms (the leaf write writes a `leaf`
    # entry, not an append_*) = 9 entries. Order: seed, message,
    # model_change, thinking_level_change, custom, custom_message, label,
    # session_info, leaf.
    type_sequence = [e.type for e in entries]
    assert type_sequence == [
        "message",
        "message",
        "model_change",
        "thinking_level_change",
        "custom",
        "custom_message",
        "label",
        "session_info",
        "leaf",
    ]
    assert h._pending_session_writes == []


async def test_no_session_fallback_drops_non_message_variants() -> None:
    """Aelix-additive backward-compat path: when ``session is None`` the
    flush dispatcher mirrors ``message`` into ``state.messages`` and drops
    the other 7 variants with a debug log.
    """

    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))
    msg = UserMessage(content=[TextContent(text="appended")])
    h._pending_session_writes.extend(
        [
            PendingMessageWrite(message=msg),
            PendingCustomWrite(custom_type="ct"),
        ]
    )
    await h.flush_pending_session_writes()
    assert msg in h.state.messages
    assert h._pending_session_writes == []


async def test_flush_with_session_during_turn_via_set_model() -> None:
    """Pi push site path: ``set_model`` during a turn enqueues a
    ``PendingModelChangeWrite``; the turn_end flush routes it to
    ``session.append_model_change``.
    """

    session = _new_session()
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream(), session=session))

    async def in_turn(event: Any, _ctx: Any) -> Any:
        await h.set_model(Model(api="openai", id="gpt-99"))
        return None

    h.hooks.on("before_agent_start", in_turn)  # type: ignore[arg-type]
    await h.prompt("hi")

    entries = await session.get_entries()
    # The assistant message_end fires before turn_end, so the session
    # carries (assistant message, model_change) entries.
    types = [e.type for e in entries]
    assert "model_change" in types


async def test_p11_set_active_tools_during_turn_does_not_enqueue() -> None:
    """P-11 regression: ``set_active_tools`` during a turn must NOT push
    any pending session write (Pi parity, ``agent-harness.ts:875-882``).
    """

    from aelix_agent_core.types import AgentTool
    from aelix_ai.tools import ToolExecutionContext, ToolResult

    async def _noop(args: Any, ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult(content=[TextContent(text="ok")])

    tools = [AgentTool(name=n, execute=_noop) for n in ("a", "b", "c")]
    h = AgentHarness(AgentHarnessOptions(tools=tools, stream_fn=_stream()))
    captured: list[Any] = []

    async def in_turn(event: Any, _ctx: Any) -> Any:
        await h.set_active_tools(["b"])
        captured.extend(h._pending_session_writes)
        return None

    h.hooks.on("before_agent_start", in_turn)  # type: ignore[arg-type]
    await h.prompt("hi")

    type_names = {type(p).__name__ for p in captured}
    assert "PendingActiveToolsChangeWrite" not in type_names
    # State mutation still happened.
    assert h.state.active_tool_names == ["b"]
