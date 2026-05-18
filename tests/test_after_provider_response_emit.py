"""Sprint 6a (Phase 4.1, §D) — ``after_provider_response`` emit-site tests.

Wired via ``SimpleStreamOptions.on_response`` callback. Observational —
no result type, but handler errors propagate as
``AgentHarnessError("hook", …)``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from aelix_agent_core.harness.core import (
    AgentHarness,
    AgentHarnessError,
    AgentHarnessOptions,
)
from aelix_agent_core.harness.hooks import AfterProviderResponseHookEvent
from aelix_ai import (
    AssistantDoneEvent,
    AssistantMessage,
    AssistantMessageEvent,
    AssistantStartEvent,
    Context,
    Model,
    ProviderResponse,
    SimpleStreamOptions,
    clear_providers,
    register_provider,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    clear_providers()
    yield
    clear_providers()


def _register_response_firing_provider(
    status: int = 200, headers: dict[str, str] | None = None
) -> None:
    """Provider that fires ``on_response`` then completes successfully."""

    hdrs = headers or {"x-anthropic-request-id": "req-123"}

    async def fake(
        model: Model,
        context: Context,
        options: SimpleStreamOptions,
    ) -> AsyncIterator[AssistantMessageEvent]:
        if options.on_response is not None:
            await options.on_response(
                ProviderResponse(status=status, headers=dict(hdrs)), model
            )
        yield AssistantStartEvent(partial=AssistantMessage(content=[]))
        yield AssistantDoneEvent(
            reason="stop", message=AssistantMessage(stop_reason="stop")
        )

    register_provider("anthropic-messages", fake)


async def test_after_provider_response_emits_with_status_and_headers() -> None:
    """The handler sees the upstream status + headers."""

    _register_response_firing_provider(status=201, headers={"x-rate-limit": "30"})
    seen: list[AfterProviderResponseHookEvent] = []

    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="anthropic-messages"),
            get_api_key_and_headers=lambda _m: {"apiKey": "k"},
        )
    )

    def handler(event: AfterProviderResponseHookEvent, _ctx: Any) -> None:
        seen.append(event)
        return None

    h.hooks.on("after_provider_response", handler)
    await h.prompt("hi")

    assert len(seen) == 1
    assert seen[0].status == 201
    assert seen[0].headers.get("x-rate-limit") == "30"


async def test_after_provider_response_is_observational() -> None:
    """Handler return values are ignored (no reducer aggregation)."""

    _register_response_firing_provider()

    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="anthropic-messages"),
            get_api_key_and_headers=lambda _m: {"apiKey": "k"},
        )
    )

    def handler(event: AfterProviderResponseHookEvent, _ctx: Any) -> str:
        # Returns a non-result type; the reducer must ignore it.
        return "ignored"

    h.hooks.on("after_provider_response", handler)
    # No exception → reducer treated handler return as observational.
    await h.prompt("hi")


async def test_after_provider_response_handler_error_propagates() -> None:
    """Handler exceptions raise ``AgentHarnessError("hook", …)``."""

    _register_response_firing_provider()

    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="anthropic-messages"),
            get_api_key_and_headers=lambda _m: {"apiKey": "k"},
        )
    )

    def handler(event: AfterProviderResponseHookEvent, _ctx: Any) -> None:
        raise RuntimeError("boom")

    h.hooks.on("after_provider_response", handler)
    with pytest.raises(AgentHarnessError) as ei:
        await h.prompt("hi")
    assert ei.value.code == "hook"


async def test_after_provider_response_not_fired_when_no_handler() -> None:
    """No subscribers — emit is still called but no observable side effect."""

    _register_response_firing_provider()

    h = AgentHarness(
        AgentHarnessOptions(
            model=Model(api="anthropic-messages"),
            get_api_key_and_headers=lambda _m: {"apiKey": "k"},
        )
    )
    # Should not raise.
    await h.prompt("hi")
