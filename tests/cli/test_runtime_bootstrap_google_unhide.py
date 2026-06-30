"""#15 Workflow B — the native Gemini un-hide (register_providers wiring).

Asserts that after :func:`register_providers` the live API registry exposes
``google-generative-ai`` and ``google-vertex``, that the previously-blocked
Gemini Developer API models move from *blocked* to *runnable*, and — the
cloudflare "never surface a model that errors at turn-1 for missing required
config" precedent — that ``google-vertex`` models stay HIDDEN until GCP auth is
resolvable (a key, or a project + location) and surface once it is.

The 2 opencode-zen gemini models (provider=opencode, served via the
google-generative-ai protocol at ``opencode.ai/zen/v1/models/{id}``) surface
like the opencode openai-responses models: concrete base_url + ``OPENCODE_API_KEY``.

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
    is_runnable,
    partition_runnable,
    supported_apis,
    unsupported_message,
)

# GCP env vars the vertex guard consults — cleared per test for determinism.
_GCP_ENV = (
    "GOOGLE_CLOUD_API_KEY",
    "GOOGLE_CLOUD_PROJECT",
    "GCLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
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


@pytest.fixture
def _clean_gcp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _GCP_ENV:
        monkeypatch.delenv(name, raising=False)


def test_register_providers_surfaces_google_apis(
    _isolated_registry: None,
) -> None:
    # Before: the catalog declares google models, but neither api is registered.
    api_registry.clear_providers()
    gga_models = [m for m in get_models("google") if m.api == "google-generative-ai"]
    vtx_models = get_models("google-vertex")
    assert gga_models, "catalog must declare google-generative-ai models"
    assert vtx_models, "catalog must declare google-vertex models"

    register_providers()

    apis = supported_apis()
    assert "google-generative-ai" in apis
    assert "google-vertex" in apis
    # ...alongside the other built-in adapters (superset, nothing displaced).
    assert "openai-completions" in apis
    assert "anthropic-messages" in apis
    assert "openai-responses" in apis


def test_gemini_developer_models_move_blocked_to_runnable(
    _isolated_registry: None,
) -> None:
    pro = get_model("google", "gemini-3-pro-preview")
    flash = get_model("google", "gemini-flash-latest")
    assert pro is not None and pro.api == "google-generative-ai"
    assert flash is not None and flash.api == "google-generative-ai"

    # Blocked while only completions is registered.
    api_registry.clear_providers()
    from aelix_ai.providers import openai_completions as _openai

    _openai.register_all()
    runnable, blocked = partition_runnable([pro, flash])
    assert runnable == []
    assert {m.id for m in blocked} == {"gemini-3-pro-preview", "gemini-flash-latest"}

    # Un-hide: both move blocked -> runnable (Developer API surfaces
    # unconditionally; a missing GEMINI_API_KEY is a normal turn-1 auth error).
    register_providers()
    runnable, blocked = partition_runnable([pro, flash])
    assert {m.id for m in runnable} == {"gemini-3-pro-preview", "gemini-flash-latest"}
    assert blocked == []


def test_vertex_models_hidden_until_gcp_config(
    _isolated_registry: None,
    _clean_gcp_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_providers()
    vtx_models = get_models("google-vertex")
    assert vtx_models

    # No GCP config: every vertex model stays hidden (would raise at turn-1).
    runnable, blocked = partition_runnable(vtx_models)
    assert runnable == []
    assert len(blocked) == len(vtx_models)

    # A project alone is not enough (location still missing) -> still hidden.
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-proj")
    runnable, blocked = partition_runnable(vtx_models)
    assert runnable == []
    assert len(blocked) == len(vtx_models)

    # Project + location -> ADC resolvable -> all surface.
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    runnable, blocked = partition_runnable(vtx_models)
    assert blocked == []
    assert len(runnable) == len(vtx_models)


def test_vertex_runnable_with_api_key_only(
    _isolated_registry: None,
    _clean_gcp_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_providers()
    vtx = get_model("google-vertex", "gemini-2.5-flash")
    assert vtx is not None and vtx.api == "google-vertex"

    # No config -> hidden, with an actionable GCP-config message.
    assert is_runnable(vtx) is False
    msg = unsupported_message(vtx)
    assert "GOOGLE_CLOUD_API_KEY" in msg
    assert "GOOGLE_CLOUD_LOCATION" in msg
    assert "gemini-2.5-flash" in msg

    # A real Vertex API key alone is sufficient (no project/location needed).
    monkeypatch.setenv("GOOGLE_CLOUD_API_KEY", "real-vertex-key")
    assert is_runnable(vtx) is True


def test_vertex_placeholder_api_key_is_not_enough(
    _isolated_registry: None,
    _clean_gcp_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_providers()
    vtx = get_model("google-vertex", "gemini-2.5-flash")
    assert vtx is not None

    # A ``<...>`` placeholder / the gcp-vertex-credentials marker resolve to no
    # key (pi parity), so without a project+location the model stays hidden.
    monkeypatch.setenv("GOOGLE_CLOUD_API_KEY", "<your-key-here>")
    assert is_runnable(vtx) is False
    monkeypatch.setenv("GOOGLE_CLOUD_API_KEY", "gcp-vertex-credentials")
    assert is_runnable(vtx) is False


def test_developer_api_models_not_gated_by_gcp_config(
    _isolated_registry: None,
    _clean_gcp_env: None,
) -> None:
    # Gemini Developer API models (provider=google) are NOT GCP-gated: they
    # surface even with no GCP env set (auth = GEMINI_API_KEY at turn-1).
    register_providers()
    pro = get_model("google", "gemini-3-pro-preview")
    assert pro is not None
    assert is_runnable(pro) is True


def test_opencode_zen_gemini_models_surface(
    _isolated_registry: None,
) -> None:
    # The 2 opencode-zen gemini models carry a CONCRETE base_url
    # (opencode.ai/zen/v1, served via the google-generative-ai protocol) and
    # authenticate from OPENCODE_API_KEY — like the opencode openai-responses
    # models, they surface with no extra config (no GCP gating: provider !=
    # google-vertex).
    register_providers()
    oc_gemini = [m for m in get_models("opencode") if m.api == "google-generative-ai"]
    assert oc_gemini, "catalog must declare opencode-zen gemini models"
    runnable, blocked = partition_runnable(oc_gemini)
    assert blocked == []
    assert len(runnable) == len(oc_gemini)
