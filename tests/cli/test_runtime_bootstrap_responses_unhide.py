"""#15 Workflow B — the openai-responses un-hide (register_providers wiring).

Asserts that after :func:`register_providers` the live API registry exposes
``openai-responses`` and that the previously-blocked ``openai-responses`` models
(OpenAI Responses + GitHub Copilot gpt-5.x) move from *blocked* to *runnable* in
:func:`partition_runnable`. Auth resolution for the four hidden providers (openai
/ github-copilot / cloudflare-ai-gateway / opencode) is covered separately by the
adapter + env-key tests; here we only prove the surfacing wiring.

The API registry is process-global, so each test snapshots and restores it to
avoid leaking provider registrations into sibling tests.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from aelix_ai import api_registry
from aelix_ai.models import get_model, get_models
from aelix_coding_agent.cli.runtime_bootstrap import register_providers
from aelix_coding_agent.core.runnable_models import (
    partition_runnable,
    supported_apis,
)


@pytest.fixture
def _isolated_registry() -> Iterator[None]:
    """Snapshot + restore the global provider registry around a test."""

    saved = api_registry.get_registered_providers()
    try:
        yield
    finally:
        api_registry.clear_providers()
        for prov in saved.values():
            api_registry.register_provider_object(
                prov, source_id=getattr(prov, "source_id", None)
            )


def test_register_providers_surfaces_openai_responses_api(
    _isolated_registry: None,
) -> None:
    # Before: the catalog declares openai-responses models, but the api is not
    # yet registered, so those models are blocked.
    api_registry.clear_providers()
    responses_models = [
        m for m in get_models("openai") if m.api == "openai-responses"
    ]
    assert responses_models, "catalog must declare openai-responses models"

    register_providers()

    # After: the api is live in the registry...
    assert "openai-responses" in supported_apis()
    # ...alongside the other built-in adapters.
    apis = supported_apis()
    assert "openai-completions" in apis
    assert "anthropic-messages" in apis


def test_partition_surfaces_previously_hidden_responses_models(
    _isolated_registry: None,
) -> None:
    # Representative models: an OpenAI Responses model + a GitHub Copilot
    # gpt-5.x model (the exact case that used to fail at the first turn).
    openai_resp = get_model("openai", "gpt-4.1")
    copilot_g5 = get_model("github-copilot", "gpt-5.2")
    assert openai_resp is not None and openai_resp.api == "openai-responses"
    assert copilot_g5 is not None and copilot_g5.api == "openai-responses"

    # Blocked while the adapter is hidden (only completions registered).
    api_registry.clear_providers()
    from aelix_ai.providers import openai_completions as _openai

    _openai.register_all()
    runnable, blocked = partition_runnable([openai_resp, copilot_g5])
    assert runnable == []
    assert {m.id for m in blocked} == {"gpt-4.1", "gpt-5.2"}

    # Un-hide: both move blocked -> runnable.
    register_providers()
    runnable, blocked = partition_runnable([openai_resp, copilot_g5])
    assert {m.id for m in runnable} == {"gpt-4.1", "gpt-5.2"}
    assert blocked == []


def _responses_models(provider: str) -> list:
    models = [m for m in get_models(provider) if m.api == "openai-responses"]
    assert models, f"{provider} must declare openai-responses models"
    return models


def test_concrete_base_url_providers_always_surface(
    _isolated_registry: None,
) -> None:
    # openai / github-copilot / opencode carry CONCRETE base_urls
    # (api.openai.com, api.individual.githubcopilot.com, opencode.ai/zen/v1)
    # and authenticate from a raw env key (opencode uses pi's envApiKeyAuth
    # ["OPENCODE_API_KEY"], a plain bearer — no OAuth required), so they are
    # runnable with no extra base-URL config.
    register_providers()
    assert "openai-responses" in supported_apis()
    for provider in ("openai", "github-copilot", "opencode"):
        models = _responses_models(provider)
        runnable, blocked = partition_runnable(models)
        assert blocked == [], f"{provider} responses models should surface"
        assert len(runnable) == len(models)


def test_cloudflare_hidden_until_account_and_gateway_set(
    _isolated_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_providers()
    cf_models = _responses_models("cloudflare-ai-gateway")

    # Unset: the templated base_url ({CLOUDFLARE_ACCOUNT_ID}/{CLOUDFLARE_GATEWAY_ID})
    # cannot be expanded → every model stays hidden (no turn-1 bad-URL crash).
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_GATEWAY_ID", raising=False)
    runnable, blocked = partition_runnable(cf_models)
    assert runnable == []
    assert len(blocked) == len(cf_models)

    # Only one of the two set → still unexpanded → still hidden.
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct-123")
    runnable, blocked = partition_runnable(cf_models)
    assert runnable == []
    assert len(blocked) == len(cf_models)

    # Both set → base_url expands fully → all models surface.
    monkeypatch.setenv("CLOUDFLARE_GATEWAY_ID", "gw-456")
    runnable, blocked = partition_runnable(cf_models)
    assert blocked == []
    assert len(runnable) == len(cf_models)


def test_unexpanded_placeholder_base_url_is_not_runnable(
    _isolated_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aelix_coding_agent.core.runnable_models import is_runnable

    register_providers()
    cf = get_model("cloudflare-ai-gateway", "gpt-5.2")
    assert cf is not None and cf.api == "openai-responses"
    assert "{CLOUDFLARE_ACCOUNT_ID}" in cf.base_url

    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CLOUDFLARE_GATEWAY_ID", raising=False)
    # api IS supported, but the unexpanded {ENV} placeholder makes it un-runnable.
    assert is_runnable(cf) is False

    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct-123")
    monkeypatch.setenv("CLOUDFLARE_GATEWAY_ID", "gw-456")
    assert is_runnable(cf) is True
