"""Sprint 5a (Phase 3.1) — full ExtensionAPI 48-method surface tests (P-22).

Pi parity invariant: ``ExtensionAPI`` exposes the 29 ``on()`` overloads
plus 19 non-event methods (Pi ``types.ts:1064-1218``). Aelix Sprint 5a
ships:

- 31 ``on()`` overloads (28 Sprint 3a + 3 Sprint 5a new events).
- 19 non-event methods (registrations + actions + ``events`` property).

The 3 extra Aelix overloads (vs Pi's 29) account for the events Aelix
landed earlier (Sprint 3a setter / provider chain) that Pi exposes as
plain own-events rather than via ``on()``; this is captured in the
drift fixture as 31 vs 29 names.
"""

from __future__ import annotations

from typing import Any

import pytest
from aelix_coding_agent.extensions.api import (
    EventBus,
    Extension,
    ExtensionAPI,
    ExtensionError,
    _ExtensionRuntime,
)


def _make_api() -> tuple[ExtensionAPI, Extension, _ExtensionRuntime]:
    rt = _ExtensionRuntime()
    ext = Extension(name="t")
    return ExtensionAPI(ext, rt), ext, rt


# === Registration surface ===


def test_register_command_records_on_extension() -> None:
    api, ext, _ = _make_api()
    api.register_command("hello", handler=lambda: None, description="greet")
    assert "hello" in ext.commands
    assert ext.commands["hello"].description == "greet"
    assert ext.commands["hello"].source == "t"


def test_register_shortcut_records_on_extension() -> None:
    api, ext, _ = _make_api()
    api.register_shortcut("ctrl+x", handler=lambda: None, description="exit")
    assert "ctrl+x" in ext.shortcuts
    assert ext.shortcuts["ctrl+x"].description == "exit"


def test_register_message_renderer_records() -> None:
    api, ext, _ = _make_api()
    api.register_message_renderer("custom_type", lambda m, o, t: None)
    assert "custom_type" in ext.message_renderers


# === Provider registration → pending queue ===


def test_register_provider_queues_pending_registration() -> None:
    api, _, rt = _make_api()
    api.register_provider("my-proxy", {"baseUrl": "https://x"})
    assert ("my-proxy", {"baseUrl": "https://x"}) in rt.pending_provider_registrations


def test_unregister_provider_queues_unregistration() -> None:
    api, _, rt = _make_api()
    api.register_provider("my-proxy", {"a": 1})
    api.unregister_provider("my-proxy")
    assert "my-proxy" in rt.pending_provider_unregistrations
    # Matching pending registration should be removed (Pi parity).
    assert all(n != "my-proxy" for n, _ in rt.pending_provider_registrations)


# === Throwing-stub actions (deferred to 5b/Phase 4) ===


def test_send_message_pre_bind_raises_unbound() -> None:
    api, _, _ = _make_api()
    with pytest.raises(ExtensionError) as exc:
        api.send_message({"customType": "x"})
    assert exc.value.code == "unbound"


def test_send_user_message_pre_bind_raises_unbound() -> None:
    api, _, _ = _make_api()
    with pytest.raises(ExtensionError) as exc:
        api.send_user_message("hi")
    assert exc.value.code == "unbound"


def test_append_entry_pre_bind_raises_unbound() -> None:
    api, _, _ = _make_api()
    with pytest.raises(ExtensionError) as exc:
        api.append_entry("custom", {"x": 1})
    assert exc.value.code == "unbound"


def test_get_commands_returns_empty_list_when_unbound() -> None:
    api, _, _ = _make_api()
    # Sprint 5a documented contract: pre-bind returns [].
    assert api.get_commands() == []


def test_get_all_tools_returns_empty_list_when_unbound() -> None:
    api, _, _ = _make_api()
    assert api.get_all_tools() == []


# === events property ===


def test_events_property_returns_shared_runtime_event_bus() -> None:
    api, _, rt = _make_api()
    bus = api.events
    assert isinstance(bus, EventBus)
    assert bus is rt.event_bus
    # Two ExtensionAPIs sharing the same runtime see the same bus.
    api2 = ExtensionAPI(Extension(name="u"), rt)
    assert api2.events is bus


def test_events_pubsub_round_trip() -> None:
    api, _, _ = _make_api()
    seen: list[Any] = []
    unsub = api.events.on("ch", seen.append)
    api.events.emit("ch", {"x": 1})
    assert seen == [{"x": 1}]
    unsub()
    api.events.emit("ch", {"y": 2})
    assert seen == [{"x": 1}]  # unsubscribed
