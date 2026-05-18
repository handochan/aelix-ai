"""Sprint 6a (Phase 4.1, §B) — Anthropic adapter auth detection tests."""

from __future__ import annotations

from typing import Any

import pytest
from aelix_ai.providers._anthropic_transforms import is_oauth_token
from aelix_ai.providers.anthropic import (
    ANTHROPIC_API,
    ANTHROPIC_PROVIDER,
    BUILTIN_SOURCE_ID,
    _AuthError,
    register_all,
    stream_anthropic,
)
from aelix_ai.streaming import (
    Context,
    Model,
    SimpleStreamOptions,
)


def _model() -> Model:
    return Model(api="anthropic-messages", id="claude-3", provider="anthropic")


def test_is_oauth_token_detects_anthropic_oauth() -> None:
    assert is_oauth_token("sk-ant-oat-12345")


def test_is_oauth_token_rejects_api_key() -> None:
    assert not is_oauth_token("sk-ant-api03-xxx")


def test_is_oauth_token_handles_none() -> None:
    assert not is_oauth_token(None)
    assert not is_oauth_token("")


async def test_oauth_token_raises_before_streaming() -> None:
    """OAuth detection fires synchronously before any SDK call."""

    opts = SimpleStreamOptions(api_key="sk-ant-oat-abc")
    with pytest.raises(_AuthError):
        it = stream_anthropic(_model(), Context(), opts)
        async for _ in it:
            pass


def test_anthropic_provider_api_id() -> None:
    """Pi parity: ``api == "anthropic-messages"``."""

    assert ANTHROPIC_API == "anthropic-messages"
    assert ANTHROPIC_PROVIDER.api == "anthropic-messages"


def test_register_all_uses_builtin_source_id() -> None:
    """``register_all()`` registers under ``"aelix-ai.builtin"``."""

    from aelix_ai import (
        clear_providers,
        get_registered_providers,
        unregister_providers_by_source,
    )

    clear_providers()
    try:
        register_all()
        registry = get_registered_providers()
        assert "anthropic-messages" in registry
        # source_id propagated for unregister-by-source.
        prov = registry["anthropic-messages"]
        assert getattr(prov, "source_id", None) == BUILTIN_SOURCE_ID
        # Round-trip: unregister by source removes the entry.
        unregister_providers_by_source(BUILTIN_SOURCE_ID)
        assert "anthropic-messages" not in get_registered_providers()
    finally:
        clear_providers()


async def test_options_client_override_used_verbatim() -> None:
    """When ``options.client`` is provided, no SDK client is created."""

    class _RecordingClient:
        def __init__(self) -> None:
            self.invoked = False

        @property
        def messages(self) -> Any:
            self.invoked = True
            raise RuntimeError("expected — adapter saw the override")

    client = _RecordingClient()
    opts = SimpleStreamOptions(api_key="sk-test", client=client)
    # The adapter will raise inside its try-block; the error event
    # carries the RuntimeError message verbatim.
    events: list[Any] = []
    async for ev in stream_anthropic(_model(), Context(), opts):
        events.append(ev)
    assert client.invoked
    # An error event must surface the override mock's failure.
    assert any(getattr(ev, "type", None) == "error" for ev in events)
