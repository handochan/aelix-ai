"""Sprint 3a / 5a / 6h₅a — :class:`ExtensionAPI.on` 35-overload + error_mode kwarg surface.

Sprint 5a (Phase 3.1, ADR-0017 §"Phase 3.1 event additions" / ADR-0041)
extended the 28-overload surface from Sprint 3a with 3 new
``coding-agent``-owned events (input / user_bash / resources_discover).

Sprint 6h₅a (Phase 4.14, ADR-0081, P-332/P-335) extends to 35 overloads
with 4 new Pi extension session lifecycle events (``session_start`` /
``session_before_switch`` / ``session_before_fork`` / ``session_shutdown``).

This file is a runtime smoke test only; pyright narrowing is enforced by
``scripts/pyright_spike.py`` and the standalone pyright run. Each call below
exercises the corresponding overload at runtime to catch obvious wiring
mistakes (typo'd event names, mis-wired handler types, missing kwarg).
"""

from __future__ import annotations

from typing import Any, get_args

import pytest
from aelix_agent_core.harness.hooks import (
    HOOK_RESULT_TYPES,
    AbortHookEvent,
    AfterProviderResponseHookEvent,
    AgentEndHookEvent,
    AgentStartHookEvent,
    BeforeAgentStartHookEvent,
    BeforeProviderPayloadHookEvent,
    BeforeProviderRequestHookEvent,
    ContextHookEvent,
    HookEventName,
    InputHookEvent,
    MessageEndHookEvent,
    MessageStartHookEvent,
    MessageUpdateHookEvent,
    ModelSelectHookEvent,
    QueueUpdateHookEvent,
    ResourcesDiscoverHookEvent,
    ResourcesUpdateHookEvent,
    SavePointHookEvent,
    SessionBeforeCompactHookEvent,
    SessionBeforeForkHookEvent,
    SessionBeforeSwitchHookEvent,
    SessionBeforeTreeHookEvent,
    SessionCompactHookEvent,
    SessionShutdownHookEvent,
    SessionStartHookEvent,
    SessionTreeHookEvent,
    SettledHookEvent,
    ThinkingLevelSelectHookEvent,
    ToolCallHookEvent,
    ToolExecutionEndHookEvent,
    ToolExecutionStartHookEvent,
    ToolExecutionUpdateHookEvent,
    ToolResultHookEvent,
    TurnEndHookEvent,
    TurnStartHookEvent,
    UserBashHookEvent,
)
from aelix_coding_agent.extensions.api import (
    Extension,
    ExtensionAPI,
    _ExtensionRuntime,
)


def _make_api() -> ExtensionAPI:
    ext = Extension(name="test")
    runtime = _ExtensionRuntime()
    return ExtensionAPI(ext, runtime)


def test_extension_api_accepts_all_36_event_names() -> None:
    """Smoke test — every name in HookEventName is accepted by ExtensionAPI.on.

    36 = Sprint 3a 28 + Sprint 5a Phase 3.1 (input / user_bash /
    resources_discover) + Sprint 6h₅a Phase 4.14 (session_start /
    session_before_switch / session_before_fork / session_shutdown) +
    Issue #5 Lane C (project_trust).
    """

    api = _make_api()

    def noop(event: Any, ctx: Any) -> None:
        return None

    for name in get_args(HookEventName):
        api.on(name, noop)  # type: ignore[arg-type]
    # 36 names registered.
    total = sum(len(handlers) for handlers in api.extension.handlers.values())
    assert total == 36
    assert len(HOOK_RESULT_TYPES) == 36


def test_extension_api_rejects_unknown_event_name() -> None:
    """Unknown names raise KeyError (strict — typo defence)."""
    api = _make_api()

    def noop(event: Any, ctx: Any) -> None:
        return None

    with pytest.raises(KeyError):
        api.on("definitely_not_an_event", noop)  # type: ignore[arg-type]


def test_extension_api_error_mode_kwarg_threads_through() -> None:
    """``error_mode`` is stored on the extension's handler_error_modes map."""
    api = _make_api()

    def noop(event: Any, ctx: Any) -> None:
        return None

    api.on("context", noop, error_mode="continue")
    api.on("tool_call", noop, error_mode="throw")
    # Two registrations on different events but same handler — independent keys.
    assert api.extension.handler_error_modes[("context", id(noop))] == "continue"
    assert api.extension.handler_error_modes[("tool_call", id(noop))] == "throw"


def test_extension_api_unsubscribe_clears_error_mode_entry() -> None:
    api = _make_api()

    def noop(event: Any, ctx: Any) -> None:
        return None

    unsub = api.on("context", noop, error_mode="continue")
    assert ("context", id(noop)) in api.extension.handler_error_modes
    unsub()
    assert ("context", id(noop)) not in api.extension.handler_error_modes


# === Per-event-name spot-checks to exercise each overload ===


def test_overload_spot_check_payload_constructors() -> None:
    """Construct one event of each Sprint 3a new type to lock the dataclass shapes."""
    # Existing 16 (re-checked for parity).
    ContextHookEvent(messages=[])
    BeforeAgentStartHookEvent()
    ToolCallHookEvent(tool_call_id="t", tool_name="x")
    ToolResultHookEvent(tool_call_id="t", tool_name="x")
    MessageEndHookEvent()
    AgentStartHookEvent()
    AgentEndHookEvent()
    TurnStartHookEvent()
    TurnEndHookEvent()
    MessageStartHookEvent()
    MessageUpdateHookEvent()
    ToolExecutionStartHookEvent()
    ToolExecutionUpdateHookEvent()
    ToolExecutionEndHookEvent()
    SessionBeforeCompactHookEvent()
    SettledHookEvent()
    # 12 new Sprint 3a additions.
    QueueUpdateHookEvent()
    SavePointHookEvent()
    AbortHookEvent()
    BeforeProviderRequestHookEvent()
    BeforeProviderPayloadHookEvent()
    AfterProviderResponseHookEvent()
    SessionCompactHookEvent()
    SessionBeforeTreeHookEvent()
    SessionTreeHookEvent()
    ModelSelectHookEvent()
    ThinkingLevelSelectHookEvent()
    ResourcesUpdateHookEvent()
    # 3 new Sprint 5a additions.
    InputHookEvent()
    UserBashHookEvent()
    ResourcesDiscoverHookEvent()
    # 4 new Sprint 6h₅a (Phase 4.14, ADR-0081) additions.
    SessionStartHookEvent()
    SessionBeforeSwitchHookEvent()
    SessionBeforeForkHookEvent()
    SessionShutdownHookEvent()
