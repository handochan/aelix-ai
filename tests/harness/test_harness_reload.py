"""Sprint 6h₇b §E (Phase 5a-iii-β, ADR-0091) —
:meth:`AgentHarness.reload` + :attr:`AgentHarness.settings_manager`.

Pi parity: ``agent-session.ts:reload`` (SHA ``734e08e``).

Three invariants locked here:

  - :meth:`reload` raises :class:`AgentHarnessError` with code
    ``"invalid_state"`` when no settings manager is attached.
  - :attr:`settings_manager` returns the instance supplied via
    :class:`AgentHarnessOptions`, or ``None`` when omitted.
  - :meth:`reload` delegates to ``settings_manager.reload()`` — an
    in-memory settings manager reflects storage changes after reload.

Sprint 6h₇c §F (Phase 5a-iii-γ, ADR-0093, P-453) expands the 2-op stub
into the full 7-op Pi parity chain (``agent-session.ts:2382-2413``).
Additional invariants:

  - ``session_shutdown`` emits BEFORE ``settings_manager.reload``.
  - ``reset_api_providers`` invoked AFTER ``settings_manager.reload``.
  - ``model_registry.reset()`` is skipped when the registry is not
    attached (Aelix may construct harnesses without one).
  - ``session_start`` emits ONLY when extensions are loaded
    (``has_bindings`` proxy for Pi 4-field UI check, P-449).
  - ``flag_values`` snapshot is captured BEFORE ``session_shutdown``
    (UNUSED this sprint — round-trip is Phase 5b).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Literal

import pytest
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
)
from aelix_agent_core.harness.hooks import (
    SessionShutdownHookEvent,
    SessionStartHookEvent,
)
from aelix_ai.messages import AssistantMessage, TextContent
from aelix_ai.settings import InMemorySettingsStorage, SettingsManager
from aelix_ai.streaming import (
    AssistantEndEvent,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
)
from aelix_coding_agent.extensions.api import Extension

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    settings_manager: SettingsManager | None = None,
) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream(),
            settings_manager=settings_manager,
        )
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_reload_raises_invalid_state_when_no_settings_manager() -> None:
    """reload() without a settings_manager → AgentHarnessError("invalid_state")."""

    harness = _new_harness(settings_manager=None)

    with pytest.raises(AgentHarnessError) as exc_info:
        await harness.reload()

    err = exc_info.value
    assert err.code == "invalid_state"
    assert "settings_manager" in str(err)


async def test_settings_manager_property_returns_attached_instance() -> None:
    """settings_manager property returns the exact object supplied at construction."""

    sm = SettingsManager.in_memory()
    harness = _new_harness(settings_manager=sm)

    assert harness.settings_manager is sm


async def test_settings_manager_property_none_when_omitted() -> None:
    """settings_manager property returns None when not supplied."""

    harness = _new_harness(settings_manager=None)

    assert harness.settings_manager is None


async def test_reload_delegates_to_settings_manager_and_refreshes() -> None:
    """reload() calls settings_manager.reload() — observable via in-memory storage.

    Sequence:
    1. Construct InMemorySettingsStorage with global content = {"theme": "dark"}.
    2. Build SettingsManager.from_storage(storage) and harness wrapping it.
    3. Overwrite storage global content to {"theme": "light"} (simulates external edit).
    4. Call harness.reload() — must delegate to settings_manager.reload().
    5. Assert sm.get_theme() == "light".
    """

    storage = InMemorySettingsStorage()
    # Seed initial global settings (theme = dark).
    storage.with_lock("global", lambda _: json.dumps({"theme": "dark"}))

    sm = SettingsManager.from_storage(storage)
    harness = _new_harness(settings_manager=sm)

    # Simulate external change: overwrite storage to theme = light.
    storage.with_lock("global", lambda _: json.dumps({"theme": "light"}))

    # reload() must pick up the new value.
    await harness.reload()

    assert sm.get_theme() == "light"


# ---------------------------------------------------------------------------
# Sprint 6h₇c §F (Phase 5a-iii-γ, ADR-0093) — 2-op → 7-op expansion
# ---------------------------------------------------------------------------


def _new_harness_with_extensions(
    *,
    settings_manager: SettingsManager | None = None,
    extensions: list[Extension] | None = None,
) -> AgentHarness:
    return AgentHarness(
        AgentHarnessOptions(
            model=Model(id="mock", provider="mock"),
            stream_fn=_stream(),
            settings_manager=settings_manager,
            extensions=extensions or [],
        )
    )


async def test_reload_emits_session_shutdown_when_handlers_registered() -> None:
    """session_shutdown handler MUST fire with reason="reload"."""

    observed: list[SessionShutdownHookEvent] = []

    async def handler(event: SessionShutdownHookEvent, ctx: Any) -> None:
        observed.append(event)

    ext = Extension(name="watcher")
    ext.handlers["session_shutdown"] = [handler]

    sm = SettingsManager.in_memory()
    harness = _new_harness_with_extensions(settings_manager=sm, extensions=[ext])

    await harness.reload()

    assert len(observed) == 1
    assert observed[0].reason == "reload"


async def test_reload_emits_session_start_when_extensions_loaded() -> None:
    """session_start MUST fire when has_bindings is true (extensions present)."""

    observed: list[SessionStartHookEvent] = []

    async def handler(event: SessionStartHookEvent, ctx: Any) -> None:
        observed.append(event)

    ext = Extension(name="w")
    ext.handlers["session_start"] = [handler]

    sm = SettingsManager.in_memory()
    harness = _new_harness_with_extensions(settings_manager=sm, extensions=[ext])

    await harness.reload()

    assert len(observed) == 1
    assert observed[0].reason == "reload"


async def test_reload_skips_session_start_when_no_extensions_loaded() -> None:
    """session_start MUST NOT fire when has_bindings is false (no extensions).

    Even when a handler is registered on the bus from some other source,
    the ``has_bindings`` predicate (``bool(extensions)``) gates the emit.
    Constructing a harness without any extensions means
    ``self._extension_runner.extensions == []`` — gate stays false.
    """

    sm = SettingsManager.in_memory()
    harness = _new_harness_with_extensions(settings_manager=sm, extensions=[])

    # Sanity: no extensions means has_bindings proxy is false.
    assert not bool(harness._extension_runner.extensions)

    # Track session_start emits via direct bus subscription (bypasses extension wiring).
    fired: list[str] = []
    original = harness._emit_session_start

    async def spy(
        reason: Literal["startup", "reload", "new", "resume", "fork"],
    ) -> bool:
        fired.append(reason)
        return await original(reason)

    harness._emit_session_start = spy  # type: ignore[method-assign]

    await harness.reload()

    # Inside the has_bindings gate, _emit_session_start is NOT called.
    assert fired == []


async def test_reload_snapshots_flag_values_before_shutdown() -> None:
    """``get_flag_values`` MUST be invoked BEFORE the session_shutdown emit."""

    order: list[str] = []

    sm = SettingsManager.in_memory()

    async def shutdown_handler(event: SessionShutdownHookEvent, ctx: Any) -> None:
        order.append("session_shutdown")

    ext = Extension(name="w")
    ext.handlers["session_shutdown"] = [shutdown_handler]
    harness = _new_harness_with_extensions(settings_manager=sm, extensions=[ext])

    original_get = harness._extension_runner.get_flag_values

    def spy_get() -> dict[str, bool | str]:
        order.append("get_flag_values")
        return original_get()

    harness._extension_runner.get_flag_values = spy_get  # type: ignore[method-assign]

    await harness.reload()

    # First call must be get_flag_values, then session_shutdown.
    assert order[0] == "get_flag_values"
    assert "session_shutdown" in order
    assert order.index("get_flag_values") < order.index("session_shutdown")


async def test_reload_calls_reset_api_providers_after_settings_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``reset_api_providers`` MUST run AFTER ``settings_manager.reload``."""

    order: list[str] = []

    sm = SettingsManager.in_memory()
    original_sm_reload = sm.reload

    async def spy_sm_reload() -> None:
        order.append("settings_manager.reload")
        await original_sm_reload()

    sm.reload = spy_sm_reload  # type: ignore[method-assign]

    def spy_reset() -> None:
        order.append("reset_api_providers")

    monkeypatch.setattr(
        "aelix_agent_core.harness.core.reset_api_providers", spy_reset
    )

    harness = _new_harness_with_extensions(settings_manager=sm)

    await harness.reload()

    assert order.index("settings_manager.reload") < order.index("reset_api_providers")


async def test_reload_skips_model_registry_reset_when_unattached() -> None:
    """``reload()`` is a no-op for the model registry when none is attached.

    The harness MUST NOT raise ``AttributeError`` when ``_model_registry``
    is absent (Aelix harnesses may not always attach one).
    """

    sm = SettingsManager.in_memory()
    harness = _new_harness_with_extensions(settings_manager=sm)

    assert not hasattr(harness, "_model_registry")

    # MUST NOT raise.
    await harness.reload()


async def test_reload_ordering_pi_parity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Full Pi parity ordering of the 7-op reload chain (P-453).

    Verifies the canonical order against
    ``agent-session.ts:2382-2413``:

    1. get_flag_values  (Pi :2384)
    2. session_shutdown emit  (Pi :2385)
    3. settings_manager.reload  (Pi :2386)
    4. reset_api_providers  (Pi :2388)
    5. session_start emit  (Pi :2407, inside has_bindings gate)
    6. resources_discover emit  (Pi :2411, inside has_bindings gate)
    """

    order: list[str] = []

    sm = SettingsManager.in_memory()
    original_sm_reload = sm.reload

    async def spy_sm_reload() -> None:
        order.append("settings_manager.reload")
        await original_sm_reload()

    sm.reload = spy_sm_reload  # type: ignore[method-assign]

    def spy_reset_api() -> None:
        order.append("reset_api_providers")

    monkeypatch.setattr(
        "aelix_agent_core.harness.core.reset_api_providers", spy_reset_api
    )

    async def shutdown_handler(event: SessionShutdownHookEvent, ctx: Any) -> None:
        order.append("session_shutdown")

    async def start_handler(event: SessionStartHookEvent, ctx: Any) -> None:
        order.append("session_start")

    ext = Extension(name="w")
    ext.handlers["session_shutdown"] = [shutdown_handler]
    ext.handlers["session_start"] = [start_handler]

    harness = _new_harness_with_extensions(settings_manager=sm, extensions=[ext])

    original_get_flags = harness._extension_runner.get_flag_values

    def spy_get_flags() -> dict[str, bool | str]:
        order.append("get_flag_values")
        return original_get_flags()

    harness._extension_runner.get_flag_values = spy_get_flags  # type: ignore[method-assign]

    original_discover = harness._emit_resources_discover

    async def spy_discover(reason: Any) -> None:
        order.append("resources_discover")
        await original_discover(reason)

    harness._emit_resources_discover = spy_discover  # type: ignore[method-assign]

    await harness.reload()

    # Canonical Pi ordering check.
    expected = [
        "get_flag_values",
        "session_shutdown",
        "settings_manager.reload",
        "reset_api_providers",
        "session_start",
        "resources_discover",
    ]
    # Filter out any unrelated entries; preserve relative order.
    filtered = [s for s in order if s in expected]
    assert filtered == expected, f"Pi parity order broken: {order!r}"
