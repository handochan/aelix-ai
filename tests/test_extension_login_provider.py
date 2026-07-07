"""Issue #77 — ExtensionAPI.register_login_provider queue + fan-out + replay.

Mirrors the register_provider idiom: registrations queue onto the runtime for
replay on every harness (re)build AND fan out immediately to the process-global
login registry.
"""

from __future__ import annotations

import pytest
from aelix_coding_agent.extensions.api import Extension, ExtensionAPI, _ExtensionRuntime
from aelix_coding_agent.login_registry import (
    LoginProvider,
    get_login_providers,
    reset_login_providers,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_login_providers()
    yield
    reset_login_providers()


async def _auth(_ctx):
    return "x"


def _api() -> tuple[ExtensionAPI, _ExtensionRuntime]:
    rt = _ExtensionRuntime()
    return ExtensionAPI(Extension(name="t"), rt), rt


def _provider(id_: str = "telnaut", name: str = "Telnaut") -> LoginProvider:
    return LoginProvider(id=id_, name=name, authenticate=_auth)


def test_register_login_provider_queues_and_fans_out() -> None:
    api, rt = _api()
    provider = _provider()
    api.register_login_provider(provider)
    # Queued for replay on the next harness (re)build...
    assert provider in rt.pending_login_provider_registrations
    # ...and visible immediately in the process-global registry.
    assert [p.id for p in get_login_providers()] == ["telnaut"]


def test_unregister_login_provider_queues_and_removes() -> None:
    api, rt = _api()
    api.register_login_provider(_provider())
    api.unregister_login_provider("telnaut")
    assert "telnaut" in rt.pending_login_provider_unregistrations
    # The pending registration was dropped so replay order stays clean.
    assert all(
        getattr(p, "id", None) != "telnaut"
        for p in rt.pending_login_provider_registrations
    )
    assert get_login_providers() == []


def test_bind_login_registries_replays_queue() -> None:
    api, rt = _api()
    api.register_login_provider(_provider())
    # Simulate a fresh process registry (e.g. a reset between builds); the
    # queued registration must be replayable by the bind.
    reset_login_providers()
    assert get_login_providers() == []
    rt.bind_login_registries()
    assert [p.id for p in get_login_providers()] == ["telnaut"]
    assert rt.pending_login_provider_registrations == []  # queue drained


def test_telnaut_example_registers_both_surfaces() -> None:
    # The shipped worked example must keep registering a login provider (for
    # /login) AND a turn provider (for /model), sharing the id 'telnaut'.
    from aelix_coding_agent.examples.telnaut.telnaut import setup

    api, rt = _api()
    setup(api)
    assert "telnaut" in [p.id for p in get_login_providers()]
    assert any(name == "telnaut" for name, _ in rt.pending_provider_registrations)
