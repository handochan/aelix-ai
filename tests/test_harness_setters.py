"""Tests for the 8 Sprint 3b harness setters (Pi parity, spec §A).

Each setter ships in ``aelix_agent_core/harness/core.py`` mirroring Pi
``agent-harness.ts`` setMethod with identical state mutation, emit, and
pending-write behaviour. The Pi-verified emit-site fixture lives at
``tests/pi_parity/fixtures/pi_setter_emit_sites_734e08e.json``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
    PendingActiveToolsChangeWrite,
    PendingModelChangeWrite,
    PendingThinkingLevelChangeWrite,
)
from aelix_agent_core.harness.hooks import (
    HookBus,
    ModelSelectHookEvent,
    ResourcesUpdateHookEvent,
    ThinkingLevelSelectHookEvent,
)
from aelix_agent_core.types import AgentTool
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_ai.tools import ToolExecutionContext, ToolResult


async def _noop_execute(args: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
    return ToolResult(content=[TextContent(text="ok")])


def _tools(*names: str) -> list[AgentTool]:
    return [AgentTool(name=n, execute=_noop_execute) for n in names]


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


def _collect(bus: HookBus, event_name: str, sink: list[Any]) -> None:
    bus.on(event_name, lambda e, _ctx: sink.append(e))  # type: ignore[arg-type, call-overload]


# === A.1 set_model — 3 tests ===========================================


async def test_set_model_roundtrip_and_previous_snapshot() -> None:
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))
    seen: list[ModelSelectHookEvent] = []
    _collect(h.hooks, "model_select", seen)

    original = h.state.model
    new_model = Model(api="anthropic", id="claude-x")
    await h.set_model(new_model)

    assert h.state.model is new_model
    assert len(seen) == 1
    assert seen[0].model is new_model
    assert seen[0].previous_model is original
    assert seen[0].source == "set"


async def test_set_model_during_turn_pushes_pending_write() -> None:
    # During-turn behaviour: emit a model_select handler that calls
    # set_model() from inside ``before_agent_start`` so we are in "turn" phase.
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))
    other = Model(api="openai", id="gpt-99")

    captured_phase: list[str] = []

    async def in_turn(event: Any, _ctx: Any) -> Any:
        captured_phase.append(h.phase)
        await h.set_model(other)
        return None

    h.hooks.on("before_agent_start", in_turn)  # type: ignore[arg-type]

    await h.prompt("hi")

    assert captured_phase == ["turn"]
    # One pending write recorded during the turn.
    types = [type(p) for p in h._pending_session_writes_drained_for_test()]
    assert PendingModelChangeWrite in types
    assert h.state.model is other


async def test_set_model_handler_raise_propagates() -> None:
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))

    def boom(event: Any, _ctx: Any) -> Any:
        raise RuntimeError("nope")

    h.hooks.on("model_select", boom)  # type: ignore[arg-type]

    with pytest.raises(AgentHarnessError) as exc:
        await h.set_model(Model(api="anthropic", id="claude-y"))
    assert exc.value.code == "hook"


# === A.2 set_thinking_level — 3 tests ==================================


async def test_set_thinking_level_mutation_and_emit() -> None:
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))
    seen: list[ThinkingLevelSelectHookEvent] = []
    _collect(h.hooks, "thinking_level_select", seen)

    await h.set_thinking_level("high")

    assert h.state.thinking_level == "high"
    assert len(seen) == 1
    assert seen[0].level == "high"
    assert seen[0].previous_level == "off"


async def test_set_thinking_level_during_turn_pending() -> None:
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))

    async def in_turn(event: Any, _ctx: Any) -> Any:
        await h.set_thinking_level("medium")
        return None

    h.hooks.on("before_agent_start", in_turn)  # type: ignore[arg-type]
    await h.prompt("hi")

    types = [type(p) for p in h._pending_session_writes_drained_for_test()]
    assert PendingThinkingLevelChangeWrite in types
    assert h.state.thinking_level == "medium"


async def test_set_thinking_level_handler_raise_propagates() -> None:
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))

    def boom(event: Any, _ctx: Any) -> Any:
        raise RuntimeError("nope")

    h.hooks.on("thinking_level_select", boom)  # type: ignore[arg-type]

    with pytest.raises(AgentHarnessError) as exc:
        await h.set_thinking_level("high")
    assert exc.value.code == "hook"


# === A.3 set_active_tools — 3 tests ====================================


async def test_set_active_tools_public_equals_private() -> None:
    h = AgentHarness(AgentHarnessOptions(tools=_tools("a", "b", "c"), stream_fn=_stream()))

    await h.set_active_tools(["a"])
    assert h.state.active_tool_names == ["a"]
    assert {t.name for t in h.state.tools} == {"a", "b", "c"}

    # Equivalence: the sync action still reaches the same state.
    h._action_set_active_tools(["b"])
    assert h.state.active_tool_names == ["b"]


async def test_set_active_tools_does_not_emit_pi_parity() -> None:
    """Pi parity guard (P-4): setActiveTools emits no event."""

    h = AgentHarness(AgentHarnessOptions(tools=_tools("a", "b"), stream_fn=_stream()))
    seen_any: list[Any] = []
    for name in (
        "queue_update",
        "model_select",
        "thinking_level_select",
        "resources_update",
        "save_point",
        "settled",
    ):
        h.hooks.on(name, lambda e, _c, _s=seen_any, _n=name: _s.append((_n, e)))  # type: ignore[arg-type, call-overload]

    await h.set_active_tools(["a"])
    assert seen_any == []


async def test_set_active_tools_invalid_no_mutation() -> None:
    h = AgentHarness(AgentHarnessOptions(tools=_tools("a"), stream_fn=_stream()))

    with pytest.raises(AgentHarnessError) as exc:
        await h.set_active_tools(["nope"])
    assert exc.value.code == "invalid_argument"
    assert h.state.active_tool_names is None


async def test_set_active_tools_during_turn_enqueues_pending_write() -> None:
    """W4 MAJOR-1 (Pi parity): Pi ``setActiveTools`` pushes onto
    ``pendingSessionWrites`` when called during a turn.

    Captures the pending queue via a ``save_point`` listener that snapshots
    the writes BEFORE ``flush_pending_session_writes`` runs at turn_end
    (W4 MAJOR-2 — no monkey-patch). The harness still emits no event for
    ``set_active_tools`` itself (P-4 verdict); persistence is observed via
    the pending write Phase 2.2 Session ADR-0022 will drain.
    """

    h = AgentHarness(
        AgentHarnessOptions(tools=_tools("a", "b", "c"), stream_fn=_stream())
    )
    snapshot_during_turn: list[Any] = []

    async def in_turn(event: Any, _ctx: Any) -> Any:
        await h.set_active_tools(["b"])
        # Snapshot inside the turn — before turn_end flush.
        snapshot_during_turn.extend(h._pending_session_writes)
        return None

    h.hooks.on("before_agent_start", in_turn)  # type: ignore[arg-type]
    await h.prompt("hi")

    types = [type(p) for p in snapshot_during_turn]
    assert PendingActiveToolsChangeWrite in types
    # Verify state mutation also occurred (Pi parity — sync action runs first).
    assert h.state.active_tool_names == ["b"]


# === A.4 set_steering_mode — 2 tests ===================================


async def test_set_steering_mode_flips_mode() -> None:
    h = AgentHarness(
        AgentHarnessOptions(steering_mode="one-at-a-time", stream_fn=_stream())
    )
    assert h._steering_queue.mode == "one-at-a-time"
    await h.set_steering_mode("all")
    assert h._steering_queue.mode == "all"


async def test_set_steering_mode_no_event_pi_parity() -> None:
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))
    seen: list[Any] = []
    for name in ("queue_update", "save_point", "settled"):
        h.hooks.on(name, lambda e, _c, _s=seen: _s.append(e))  # type: ignore[arg-type, call-overload]
    await h.set_steering_mode("all")
    assert seen == []


# === A.5 set_follow_up_mode — 2 tests ==================================


async def test_set_follow_up_mode_flips_mode() -> None:
    h = AgentHarness(
        AgentHarnessOptions(follow_up_mode="one-at-a-time", stream_fn=_stream())
    )
    assert h._follow_up_queue.mode == "one-at-a-time"
    await h.set_follow_up_mode("all")
    assert h._follow_up_queue.mode == "all"


async def test_set_follow_up_mode_no_event_pi_parity() -> None:
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))
    seen: list[Any] = []
    for name in ("queue_update", "save_point", "settled"):
        h.hooks.on(name, lambda e, _c, _s=seen: _s.append(e))  # type: ignore[arg-type, call-overload]
    await h.set_follow_up_mode("all")
    assert seen == []


# === A.6 set_resources — 3 tests =======================================


async def test_set_resources_roundtrip_and_clone() -> None:
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))
    seen: list[ResourcesUpdateHookEvent] = []
    _collect(h.hooks, "resources_update", seen)

    payload = {"k": 1}
    await h.set_resources(payload)

    assert h.state.resources == {"k": 1}
    # Shallow clone semantics — mutating the input dict should NOT touch state.
    payload["k"] = 99
    assert h.state.resources == {"k": 1}
    assert len(seen) == 1
    assert seen[0].resources == {"k": 1}
    assert seen[0].previous_resources == {}


async def test_set_resources_emit_payload_isolation() -> None:
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))
    snapshots: list[dict[str, Any]] = []

    h.hooks.on(  # type: ignore[arg-type]
        "resources_update",
        lambda e, _c: snapshots.append(dict(e.resources)),
    )

    await h.set_resources({"x": 1})
    # Mutating state afterwards must not retroactively change the captured emit.
    h.state.resources["x"] = 999
    assert snapshots == [{"x": 1}]


async def test_set_resources_handler_raise_propagates() -> None:
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))

    def boom(event: Any, _ctx: Any) -> Any:
        raise RuntimeError("nope")

    h.hooks.on("resources_update", boom)  # type: ignore[arg-type]
    with pytest.raises(AgentHarnessError) as exc:
        await h.set_resources({"x": 1})
    assert exc.value.code == "hook"


# === A.7 set_stream_options — 2 tests ==================================


async def test_set_stream_options_roundtrip() -> None:
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))
    await h.set_stream_options({"timeout": 5})
    assert h.state.stream_options == {"timeout": 5}
    # Shallow clone — outer dict isolated.
    h.state.stream_options["x"] = 1
    assert "x" in h.state.stream_options


async def test_set_stream_options_no_event_pi_parity() -> None:
    h = AgentHarness(AgentHarnessOptions(stream_fn=_stream()))
    seen: list[Any] = []
    for name in (
        "queue_update",
        "save_point",
        "settled",
        "model_select",
        "thinking_level_select",
        "resources_update",
    ):
        h.hooks.on(name, lambda e, _c, _s=seen: _s.append(e))  # type: ignore[arg-type, call-overload]
    await h.set_stream_options({"timeout": 5})
    assert seen == []


# === A.8 set_tools — 3 tests ===========================================


async def test_set_tools_atomic_replace() -> None:
    h = AgentHarness(AgentHarnessOptions(tools=_tools("a", "b"), stream_fn=_stream()))

    new_tools = _tools("c", "d")
    await h.set_tools(new_tools)
    assert {t.name for t in h.state.tools} == {"c", "d"}


async def test_set_tools_failure_no_partial_mutation() -> None:
    h = AgentHarness(AgentHarnessOptions(tools=_tools("a", "b"), stream_fn=_stream()))
    original_tools = list(h.state.tools)

    with pytest.raises(AgentHarnessError) as exc:
        await h.set_tools(_tools("c"), active_tool_names=["unknown"])
    assert exc.value.code == "invalid_argument"
    # State must be untouched after rejection.
    assert h.state.tools == original_tools
    assert h.state.active_tool_names is None


async def test_set_tools_preserves_active_filter_when_names_omitted() -> None:
    h = AgentHarness(AgentHarnessOptions(tools=_tools("a", "b", "c"), stream_fn=_stream()))
    await h.set_active_tools(["a", "b"])

    # Replace tools but keep prior active set — names omitted means "preserve".
    await h.set_tools(_tools("a", "b", "x"))
    assert h.state.active_tool_names == ["a", "b"]

    # F-3b-2 (W5 must-document): Pi ``validateToolNames`` raises when the
    # prior active filter contains names that are no longer present in the
    # new tool list. Aelix mirrors this strictly — no silent widening.
    with pytest.raises(AgentHarnessError) as exc:
        await h.set_tools(_tools("x", "y"))
    assert exc.value.code == "invalid_argument"
    # State must be untouched after the strict rejection.
    assert {t.name for t in h.state.tools} == {"a", "b", "x"}
    assert h.state.active_tool_names == ["a", "b"]


async def test_set_tools_explicit_widening_via_empty_list() -> None:
    """F-3b-2 escape hatch: pass an empty active list to intentionally widen.

    Pi parity: when the caller wants to drop the stale filter alongside a
    tool-list replacement, they pass an explicit ``active_tool_names`` (an
    empty list, or a fresh list of new names) — never relying on silent
    widening from the prior state.
    """

    h = AgentHarness(AgentHarnessOptions(tools=_tools("a", "b", "c"), stream_fn=_stream()))
    await h.set_active_tools(["a", "b"])

    # Caller acknowledges the stale-filter situation by passing []. The new
    # active set becomes [] (no tools active until the caller re-narrows).
    await h.set_tools(_tools("x", "y"), active_tool_names=[])
    assert {t.name for t in h.state.tools} == {"x", "y"}
    assert h.state.active_tool_names == []

    # Alternatively the caller can pass a fresh explicit list of new names.
    await h.set_tools(_tools("p", "q"), active_tool_names=["p"])
    assert h.state.active_tool_names == ["p"]


# === Test helper: drain pending writes for test assertions =============


def _install_test_helper() -> None:
    """Add a test-only helper to AgentHarness to peek at writes drained during
    a turn (turn_end already flushed _pending_session_writes by then).

    Implementation: monkeypatch on the class once at import time; safe because
    tests run single-process and the helper is internal.
    """

    if hasattr(AgentHarness, "_pending_session_writes_drained_for_test"):
        return

    # We need to capture the pending writes BEFORE turn_end flushes them.
    # Approach: install a `save_point` handler at construction time that
    # remembers what was about to be flushed. Simpler: snapshot inside the
    # setter calls by wrapping flush_pending_session_writes.
    original_flush = AgentHarness.flush_pending_session_writes

    async def patched_flush(self: AgentHarness) -> None:
        snapshot = list(self._pending_session_writes)
        await original_flush(self)
        existing = getattr(self, "_test_drained", [])
        existing.extend(snapshot)
        # Use object.__setattr__ in case dataclasses ever land — AgentHarness
        # is currently a regular class so plain assignment is fine.
        self._test_drained = existing  # type: ignore[attr-defined]

    AgentHarness.flush_pending_session_writes = patched_flush  # type: ignore[method-assign]

    def drained(self: AgentHarness) -> list[Any]:
        return list(getattr(self, "_test_drained", []))

    AgentHarness._pending_session_writes_drained_for_test = drained  # type: ignore[attr-defined]


_install_test_helper()
