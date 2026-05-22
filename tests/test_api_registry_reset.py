"""Sprint 6h₇c §B (Phase 5a-iii-γ, ADR-0093) — ``reset_api_providers`` tests.

Pi parity: ``register-builtins.ts:400-403`` (P-446).

Aelix has no module-level lazy provider cache to flush; the function
ships as a registry-flush wrapper around :func:`clear_providers` so
:meth:`AgentHarness.reload` matches the Pi reload chain step-for-step.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from aelix_ai import (
    AssistantMessage,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    SimpleStreamOptions,
    clear_providers,
    get_registered_providers,
    register_provider,
    register_provider_object,
    reset_api_providers,
)


class _FakeProvider:
    """Provider Protocol implementer for tests."""

    api: str

    def __init__(self, api: str, source_id: str | None = None) -> None:
        self.api = api
        self.source_id = source_id

    async def stream(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    clear_providers()
    yield
    clear_providers()


def test_reset_api_providers_flushes_registry() -> None:
    """After register → reset, the registry is empty."""

    register_provider_object(_FakeProvider(api="api-a"), source_id="builtin")
    register_provider_object(_FakeProvider(api="api-b"), source_id="third-party")
    assert len(get_registered_providers()) == 2

    reset_api_providers()

    assert get_registered_providers() == {}


def test_reset_api_providers_flushes_bare_callable_path() -> None:
    """Bare-callable registrations (Phase 1.4 path) are flushed too."""

    async def fn(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        yield AssistantStartEvent()

    register_provider("legacy-api", fn)
    assert "legacy-api" in get_registered_providers()

    reset_api_providers()

    assert get_registered_providers() == {}


def test_reset_api_providers_is_idempotent() -> None:
    """Calling reset twice in a row stays empty."""

    register_provider_object(_FakeProvider(api="api-x"))
    reset_api_providers()
    reset_api_providers()

    assert get_registered_providers() == {}


def test_reset_api_providers_on_empty_registry_is_noop() -> None:
    """Reset on an already-empty registry does not raise."""

    assert get_registered_providers() == {}
    reset_api_providers()  # MUST NOT raise.
    assert get_registered_providers() == {}


def test_reset_api_providers_does_not_re_register_builtins() -> None:
    """Aelix-additive divergence (ADR-0093 §B.4 / §G.1.2 #1).

    Pi composes ``clearApiProviders + registerBuiltInApiProviders``;
    Aelix has no module-level lazy cache so reset is a flush only.
    After reset, no providers are pre-installed — callers must
    re-register explicitly (e.g. via provider package init).
    """

    register_provider_object(_FakeProvider(api="anthropic-messages"))
    reset_api_providers()

    # The registry is empty — Aelix does NOT auto-re-register built-ins.
    assert get_registered_providers() == {}
