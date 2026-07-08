"""Issue #77 follow-up — ExtensionAPI.register_api_adapter (custom wire adapters).

An extension can register a custom StreamFn under its own ``api`` id (for an
endpoint the built-in openai-completions config can't express — verify=False,
model-in-URL, non-OpenAI fields). Covers queue + immediate fan-out + the
reload-survival replay (reset_api_providers wipes the process-global registry;
the harness rebuild's bind_api_adapters re-applies).
"""

from __future__ import annotations

import contextlib

import pytest
from aelix_ai import api_registry
from aelix_coding_agent.extensions.api import Extension, ExtensionAPI, _ExtensionRuntime

_TEST_API = "telnaut-openai-test"


@pytest.fixture(autouse=True)
def _isolate_api_registry():
    # The api registry is process-global; snapshot + restore so a test's
    # register / reset / bind never leaks into another test (built-ins included).
    snapshot = dict(api_registry._PROVIDERS)
    yield
    api_registry._PROVIDERS.clear()
    api_registry._PROVIDERS.update(snapshot)


async def _fn(model, ctx, opts):  # a minimal StreamFn (never actually streamed here)
    if False:  # pragma: no cover
        yield None


def _api() -> tuple[ExtensionAPI, _ExtensionRuntime]:
    rt = _ExtensionRuntime()
    return ExtensionAPI(Extension(name="t"), rt), rt


def test_register_api_adapter_queues_and_fans_out() -> None:
    api, rt = _api()
    api.register_api_adapter(_TEST_API, _fn)
    assert (_TEST_API, _fn) in rt.pending_api_adapters  # queued for replay
    assert _TEST_API in api_registry.get_registered_providers()  # immediate fan-out


def test_unregister_api_adapter_removes() -> None:
    api, rt = _api()
    api.register_api_adapter(_TEST_API, _fn)
    api.unregister_api_adapter(_TEST_API)
    assert _TEST_API in rt.pending_api_adapter_unregistrations
    assert all(a != _TEST_API for a, _ in rt.pending_api_adapters)
    assert _TEST_API not in api_registry.get_registered_providers()


def test_bind_api_adapters_replays_after_reset() -> None:
    # THE reload-survival guarantee: reset_api_providers() wipes the registry
    # on /reload; the harness rebuild's bind_api_adapters re-applies the adapter.
    api, rt = _api()
    api.register_api_adapter(_TEST_API, _fn)
    api_registry.reset_api_providers()  # simulate the /reload wipe
    assert _TEST_API not in api_registry.get_registered_providers()
    rt.bind_api_adapters()  # simulate the harness rebuild
    assert _TEST_API in api_registry.get_registered_providers()
    assert rt.pending_api_adapters == []  # queue drained


def test_registered_adapter_resolves_for_its_api() -> None:
    # A Model carrying the custom api routes to our stream_fn (the dispatcher
    # finds it) rather than raising "no provider registered".
    api, _ = _api()
    api.register_api_adapter(_TEST_API, _fn)
    provider = api_registry.get_registered_providers()[_TEST_API]
    assert getattr(provider, "source_id", None) == "aelix-ext-api-adapter"


def test_telnaut_example_registers_custom_adapter() -> None:
    from aelix_coding_agent.login_registry import (
        get_login_providers,
        reset_login_providers,
    )

    reset_login_providers()
    try:
        api, rt = _api()
        from aelix_coding_agent.examples.telnaut.telnaut import setup

        setup(api)
        assert "telnaut-openai" in api_registry.get_registered_providers()  # custom adapter
        assert any(n == "telnaut" for n, _ in rt.pending_provider_registrations)  # /model
        assert "telnaut" in [p.id for p in get_login_providers()]  # /login
    finally:
        with contextlib.suppress(Exception):
            reset_login_providers()
