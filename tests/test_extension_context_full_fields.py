"""Sprint 5a (Phase 3.1) — full ExtensionContext 14-field surface tests (P-23).

Pi parity: ``ExtensionContext`` exposes 14 fields. Sprint 5a lands 13 of
them as production bindings; the 14th (``ui``) is deferred to ADR-0033
(Phase 5 TUI). The stale-check shim (D.1.4) must extend to every new
attribute.
"""

from __future__ import annotations

from typing import Any

import pytest
from aelix_coding_agent.extensions.api import (
    ContextUsage,
    EventBus,
    ExtensionContext,
    ExtensionError,
    _ExtensionRuntime,
    _StubModelRegistry,
)


def _make_ctx(**overrides: Any) -> tuple[ExtensionContext, _ExtensionRuntime]:
    rt = _ExtensionRuntime()
    kwargs = dict(
        cwd="/tmp",
        model=None,
        is_idle=lambda: True,
        abort=lambda: None,
        get_active_tools=lambda: [],
        get_system_prompt=lambda: "",
    )
    kwargs.update(overrides)
    ctx = ExtensionContext(rt, **kwargs)
    return ctx, rt


def test_has_ui_is_constant_false() -> None:
    ctx, _ = _make_ctx()
    assert ctx.has_ui is False


def test_session_manager_raises_invalid_state_when_unattached() -> None:
    ctx, _ = _make_ctx()
    with pytest.raises(ExtensionError) as exc:
        _ = ctx.session_manager
    assert exc.value.code == "invalid_state"


def test_session_manager_returns_attached_manager() -> None:
    sentinel_session = object()

    class SM:
        def get_session(self) -> object:
            return sentinel_session

    ctx, _ = _make_ctx(session_manager=SM())
    assert ctx.session_manager.get_session() is sentinel_session


def test_model_registry_defaults_to_stub() -> None:
    ctx, _ = _make_ctx()
    registry = ctx.model_registry
    assert isinstance(registry, _StubModelRegistry)
    # Stub tracks register/unregister calls.
    registry.register_provider("x", {"a": 1})
    assert registry.registrations == [("x", {"a": 1})]


def test_signal_defaults_to_none() -> None:
    ctx, _ = _make_ctx()
    assert ctx.signal is None


def test_signal_passthrough() -> None:
    sentinel = object()
    ctx, _ = _make_ctx(signal=sentinel)
    assert ctx.signal is sentinel


def test_has_pending_messages_defaults_false() -> None:
    ctx, _ = _make_ctx()
    assert ctx.has_pending_messages() is False


def test_has_pending_messages_observes_callback() -> None:
    ctx, _ = _make_ctx(has_pending_messages=lambda: True)
    assert ctx.has_pending_messages() is True


def test_shutdown_default_raises_invalid_state() -> None:
    ctx, _ = _make_ctx()
    with pytest.raises(ExtensionError) as exc:
        ctx.shutdown()
    assert exc.value.code == "invalid_state"


def test_shutdown_invokes_supplied_callback() -> None:
    called: list[bool] = []
    ctx, _ = _make_ctx(shutdown=lambda: called.append(True))
    ctx.shutdown()
    assert called == [True]


def test_get_context_usage_returns_none_by_default() -> None:
    ctx, _ = _make_ctx()
    assert ctx.get_context_usage() is None


def test_get_context_usage_passthrough() -> None:
    usage = ContextUsage(tokens=12, context_window=200_000, percent=0.001)
    ctx, _ = _make_ctx(get_context_usage=lambda: usage)
    assert ctx.get_context_usage() is usage


def test_compact_defaults_to_noop() -> None:
    ctx, _ = _make_ctx()
    # No-op default; just verifies the call does not raise.
    ctx.compact()


def test_compact_invokes_supplied_callback() -> None:
    seen: list[dict] = []
    ctx, _ = _make_ctx(compact=lambda **kw: seen.append(kw))
    ctx.compact(custom_instructions="brief")
    assert seen == [
        {"custom_instructions": "brief", "on_complete": None, "on_error": None}
    ]


def test_events_property_proxies_runtime_bus() -> None:
    ctx, rt = _make_ctx()
    bus = rt.event_bus
    assert isinstance(bus, EventBus)


# === Stale-check coverage extension ===


def test_stale_check_blocks_session_manager_access() -> None:
    ctx, rt = _make_ctx(
        session_manager=type("SM", (), {"get_session": lambda self: None})()
    )
    rt.invalidate("disposed")
    with pytest.raises(ExtensionError) as exc:
        _ = ctx.session_manager
    assert exc.value.code == "stale"


def test_stale_check_blocks_model_registry_access() -> None:
    ctx, rt = _make_ctx()
    rt.invalidate("disposed")
    with pytest.raises(ExtensionError) as exc:
        _ = ctx.model_registry
    assert exc.value.code == "stale"


def test_stale_check_blocks_has_pending_messages() -> None:
    ctx, rt = _make_ctx()
    rt.invalidate("disposed")
    with pytest.raises(ExtensionError) as exc:
        ctx.has_pending_messages()
    assert exc.value.code == "stale"
