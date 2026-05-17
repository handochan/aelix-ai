"""Sprint 3a payload roundtrip tests — Section E.1 / spec §A.3.

For each of the 13 new Sprint 3a hook event types, verify:
- The event registers in :data:`HOOK_RESULT_TYPES`.
- :meth:`HookBus.on` accepts a handler under the corresponding ``Literal``.
- :meth:`HookBus.emit` dispatches the event, the handler observes the
  payload, and (where the event has a result type) the reducer surfaces the
  returned value to the caller.

Sprint 3a does NOT install emit sites for these events (most live in Sprint
3b or Phase 4). This file tests the type / registry / reducer plumbing only.
"""

from __future__ import annotations

from typing import Any

from aelix_agent_core.harness.hooks import (
    HOOK_RESULT_TYPES,
    AbortHookEvent,
    AfterProviderResponseHookEvent,
    BeforeProviderPayloadHookEvent,
    BeforeProviderPayloadResult,
    BeforeProviderRequestHookEvent,
    BeforeProviderRequestResult,
    HookBus,
    ModelSelectHookEvent,
    QueueUpdateHookEvent,
    ResourcesUpdateHookEvent,
    SavePointHookEvent,
    SessionBeforeTreeHookEvent,
    SessionBeforeTreeResult,
    SessionCompactHookEvent,
    SessionTreeHookEvent,
    SettledHookEvent,
    ThinkingLevelSelectHookEvent,
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


# === Registry sanity ===


async def test_hook_result_types_registry_has_31_entries() -> None:
    """31 = Sprint 3a 28 + Sprint 5a Phase 3.1 (input/user_bash/resources_discover)."""

    assert len(HOOK_RESULT_TYPES) == 31


async def test_all_new_sprint_3a_event_names_registered() -> None:
    new_names = {
        "queue_update",
        "save_point",
        "abort",
        "before_provider_request",
        "before_provider_payload",
        "after_provider_response",
        "session_compact",
        "session_before_tree",
        "session_tree",
        "model_select",
        "thinking_level_select",
        "resources_update",
    }
    assert new_names <= set(HOOK_RESULT_TYPES.keys())


# === Per-event roundtrip (observational events) ===


async def test_queue_update_roundtrip() -> None:
    bus = _make_bus()
    seen: list[QueueUpdateHookEvent] = []

    def handler(event: QueueUpdateHookEvent, ctx: Any) -> None:
        seen.append(event)

    bus.on("queue_update", handler)
    event = QueueUpdateHookEvent(steer=[], follow_up=[], next_turn=[])
    result = await bus.emit(event)
    assert result is None  # observational
    assert len(seen) == 1
    assert seen[0].type == "queue_update"


async def test_save_point_roundtrip() -> None:
    bus = _make_bus()
    seen: list[bool] = []

    def handler(event: SavePointHookEvent, ctx: Any) -> None:
        seen.append(event.had_pending_mutations)

    bus.on("save_point", handler)
    await bus.emit(SavePointHookEvent(had_pending_mutations=True))
    assert seen == [True]


async def test_abort_roundtrip() -> None:
    bus = _make_bus()
    seen: list[AbortHookEvent] = []

    def handler(event: AbortHookEvent, ctx: Any) -> None:
        seen.append(event)

    bus.on("abort", handler)
    await bus.emit(AbortHookEvent(cleared_steer=[], cleared_follow_up=[]))
    assert seen[0].type == "abort"


async def test_after_provider_response_roundtrip() -> None:
    bus = _make_bus()
    seen: list[int] = []

    def handler(event: AfterProviderResponseHookEvent, ctx: Any) -> None:
        seen.append(event.status)

    bus.on("after_provider_response", handler)
    await bus.emit(
        AfterProviderResponseHookEvent(status=200, headers={"x-trace": "abc"})
    )
    assert seen == [200]


async def test_session_compact_roundtrip() -> None:
    bus = _make_bus()
    seen: list[bool] = []

    def handler(event: SessionCompactHookEvent, ctx: Any) -> None:
        seen.append(event.from_hook)

    bus.on("session_compact", handler)
    await bus.emit(SessionCompactHookEvent(from_hook=True))
    assert seen == [True]


async def test_session_tree_roundtrip() -> None:
    bus = _make_bus()
    seen: list[tuple[str, str]] = []

    def handler(event: SessionTreeHookEvent, ctx: Any) -> None:
        seen.append((event.old_leaf_id, event.new_leaf_id))

    bus.on("session_tree", handler)
    await bus.emit(SessionTreeHookEvent(new_leaf_id="b", old_leaf_id="a"))
    assert seen == [("a", "b")]


async def test_model_select_roundtrip() -> None:
    bus = _make_bus()
    seen: list[str] = []

    def handler(event: ModelSelectHookEvent, ctx: Any) -> None:
        seen.append(event.source)

    bus.on("model_select", handler)
    await bus.emit(ModelSelectHookEvent(source="restore"))
    assert seen == ["restore"]


async def test_thinking_level_select_roundtrip() -> None:
    bus = _make_bus()
    seen: list[tuple[str, str]] = []

    def handler(event: ThinkingLevelSelectHookEvent, ctx: Any) -> None:
        seen.append((event.previous_level, event.level))

    bus.on("thinking_level_select", handler)
    await bus.emit(ThinkingLevelSelectHookEvent(level="high", previous_level="off"))
    assert seen == [("off", "high")]


async def test_resources_update_roundtrip() -> None:
    bus = _make_bus()
    seen: list[dict[str, Any]] = []

    def handler(event: ResourcesUpdateHookEvent, ctx: Any) -> None:
        seen.append(event.resources)

    bus.on("resources_update", handler)
    await bus.emit(
        ResourcesUpdateHookEvent(
            resources={"k": "v"}, previous_resources={}
        )
    )
    assert seen == [{"k": "v"}]


# === Per-event roundtrip (result-producing events) ===


async def test_before_provider_request_reducer_chains_stream_options() -> None:
    """Sequential patch chain — Pi ``agent-harness.ts:232-250`` parity."""
    bus = _make_bus()

    def h1(event: BeforeProviderRequestHookEvent, ctx: Any) -> BeforeProviderRequestResult:
        return BeforeProviderRequestResult(stream_options={"a": 1})

    def h2(event: BeforeProviderRequestHookEvent, ctx: Any) -> BeforeProviderRequestResult:
        # H2 sees the chained patch from H1.
        assert event.stream_options == {"a": 1}
        return BeforeProviderRequestResult(stream_options={"b": 2})

    bus.on("before_provider_request", h1)
    bus.on("before_provider_request", h2)

    result = await bus.emit(
        BeforeProviderRequestHookEvent(
            session_id="s1", stream_options={}
        )
    )
    assert isinstance(result, BeforeProviderRequestResult)
    assert result.stream_options == {"a": 1, "b": 2}


async def test_before_provider_payload_reducer_chains_payload() -> None:
    """Each handler sees the previous handler's payload."""
    bus = _make_bus()

    def h1(event: BeforeProviderPayloadHookEvent, ctx: Any) -> BeforeProviderPayloadResult:
        return BeforeProviderPayloadResult(payload={"v": 1})

    def h2(event: BeforeProviderPayloadHookEvent, ctx: Any) -> BeforeProviderPayloadResult:
        assert event.payload == {"v": 1}
        return BeforeProviderPayloadResult(payload={"v": 2})

    bus.on("before_provider_payload", h1)
    bus.on("before_provider_payload", h2)

    result = await bus.emit(BeforeProviderPayloadHookEvent(payload={}))
    assert isinstance(result, BeforeProviderPayloadResult)
    assert result.payload == {"v": 2}


async def test_session_before_tree_cancel_short_circuits() -> None:
    bus = _make_bus()
    h3_called: list[bool] = []

    def h1(event: SessionBeforeTreeHookEvent, ctx: Any) -> SessionBeforeTreeResult:
        return SessionBeforeTreeResult(cancel=False, label="h1")

    def h2(event: SessionBeforeTreeHookEvent, ctx: Any) -> SessionBeforeTreeResult:
        return SessionBeforeTreeResult(cancel=True)

    def h3(event: SessionBeforeTreeHookEvent, ctx: Any) -> SessionBeforeTreeResult:
        h3_called.append(True)
        return SessionBeforeTreeResult(cancel=False)

    bus.on("session_before_tree", h1)
    bus.on("session_before_tree", h2)
    bus.on("session_before_tree", h3)

    result = await bus.emit(SessionBeforeTreeHookEvent())
    assert isinstance(result, SessionBeforeTreeResult)
    assert result.cancel is True
    assert h3_called == []


async def test_settled_payload_extension_default_next_turn_count_zero() -> None:
    """Settled gains ``next_turn_count: int = 0`` in Sprint 3a; populated in 3b."""
    bus = _make_bus()
    seen: list[int] = []

    def handler(event: SettledHookEvent, ctx: Any) -> None:
        seen.append(event.next_turn_count)

    bus.on("settled", handler)
    await bus.emit(SettledHookEvent())
    assert seen == [0]
    # Explicit non-zero passes through (3b populating shape).
    await bus.emit(SettledHookEvent(next_turn_count=3))
    assert seen == [0, 3]
