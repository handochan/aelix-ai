"""WP-8 follow-up — runnable-API filter/guard (core/runnable_models.py).

Covers the fix for the GitHub-Copilot ``gpt-5.x`` failure: such models declare
``api='openai-responses'``, which has no registered adapter, so they must be
hidden from the picker + guarded at switch instead of failing at the first turn.
"""

from __future__ import annotations

import types

from aelix_coding_agent.core import runnable_models as rm

_REGISTRY = "aelix_ai.api_registry.get_registered_providers"


def _model(api: str | None) -> types.SimpleNamespace:
    return types.SimpleNamespace(id=f"model-{api}", api=api)


def test_supported_apis_reads_registry(monkeypatch) -> None:
    monkeypatch.setattr(
        _REGISTRY,
        lambda: {"openai-completions": object(), "anthropic-messages": object()},
    )
    assert rm.supported_apis() == {"openai-completions", "anthropic-messages"}


def test_is_runnable_by_registered_api(monkeypatch) -> None:
    monkeypatch.setattr(_REGISTRY, lambda: {"openai-completions": object()})
    assert rm.is_runnable(_model("openai-completions"))
    assert not rm.is_runnable(_model("openai-responses"))
    # A model with no api attribute can't be proven unrunnable → runnable.
    assert rm.is_runnable(_model(None))


def test_empty_registry_never_over_filters(monkeypatch) -> None:
    monkeypatch.setattr(_REGISTRY, lambda: {})
    assert rm.is_runnable(_model("anything"))
    runnable, blocked = rm.partition_runnable([_model("x"), _model("y")])
    assert len(runnable) == 2
    assert blocked == []


def test_partition_runnable_splits(monkeypatch) -> None:
    monkeypatch.setattr(_REGISTRY, lambda: {"openai-completions": object()})
    runnable, blocked = rm.partition_runnable(
        [_model("openai-completions"), _model("openai-responses"), _model(None)]
    )
    assert [m.api for m in runnable] == ["openai-completions", None]
    assert [m.api for m in blocked] == ["openai-responses"]


def test_unsupported_message_is_actionable(monkeypatch) -> None:
    monkeypatch.setattr(_REGISTRY, lambda: {"openai-completions": object()})
    msg = rm.unsupported_message(_model("openai-responses"))
    assert "openai-responses" in msg  # the offending api
    assert "openai-completions" in msg  # what IS supported
    assert "model-openai-responses" in msg  # the model id


def test_unsupported_message_explains_an_unresolved_api(monkeypatch) -> None:
    """#98: ``api="unknown"`` is the Model DEFAULT — resolution failed to name a
    protocol. Reporting that the model "uses the 'unknown' API" and advising the
    user to "pick a model on a supported API" describes a choice they never made;
    the real fault is an unresolvable id/provider pair. Names the pair instead.
    """

    monkeypatch.setattr(_REGISTRY, lambda: {"openai-completions": object()})
    model = types.SimpleNamespace(id="tn-1", api="unknown", provider="telnaut")
    msg = rm.unsupported_message(model)
    assert "tn-1" in msg and "telnaut" in msg  # the unresolvable pair
    assert "could not be resolved" in msg
    assert "uses the 'unknown' API" not in msg  # the old misdescription


def test_unsupported_message_unresolved_api_without_a_provider(monkeypatch) -> None:
    """The empty-provider trigger (settings defaultModel with no defaultProvider)
    must read naturally rather than quoting an empty provider name.
    """

    monkeypatch.setattr(_REGISTRY, lambda: {"openai-completions": object()})
    model = types.SimpleNamespace(id="gpt-5.4", api="unknown", provider="")
    msg = rm.unsupported_message(model)
    assert "no provider" in msg
    assert "provider ''" not in msg


# === #98: a model that declares NO base_url is a credential-egress hazard =====
# Every adapter resolves its host as ``base_url or None``, so "" collapses to the
# SDK's own FIRST-PARTY host (AsyncAnthropic → api.anthropic.com, AsyncOpenAI →
# api.openai.com). For a model whose provider is NOT that vendor, running it ships
# that provider's credentials to a third party. ``models.json`` already drops such
# models at load (``if not base_url: continue``); an extension
# ``register_provider`` is the one path that reaches the registry with base_url="",
# via BOTH ``resolve_model`` (CLI flags) and the ``/model`` picker (which hands
# registry models straight to ``set_model``). Gating here covers both.


def test_declared_empty_base_url_is_not_runnable(monkeypatch) -> None:
    monkeypatch.setattr(_REGISTRY, lambda: {"anthropic-messages": object()})
    leak = types.SimpleNamespace(
        id="corp-x", api="anthropic-messages", provider="mycorp", base_url=""
    )
    assert rm.is_runnable(leak) is False
    # …and the picker HIDES it rather than offering a one-click credential leak.
    runnable, blocked = rm.partition_runnable([leak])
    assert runnable == [] and blocked == [leak]


def test_absent_base_url_attribute_is_still_runnable(monkeypatch) -> None:
    """ABSENT (no attribute) is not the same claim as DECLARED-EMPTY.

    An object that never mentions a host cannot be PROVEN hostless — mirroring the
    ``api is None`` rule right above it. Only the real ``Model`` dataclass (whose
    base_url defaults to "") is blocked.
    """

    monkeypatch.setattr(_REGISTRY, lambda: {"openai-completions": object()})
    assert rm.is_runnable(types.SimpleNamespace(id="m", api="openai-completions"))


def test_populated_base_url_is_runnable(monkeypatch) -> None:
    monkeypatch.setattr(_REGISTRY, lambda: {"openai-completions": object()})
    model = types.SimpleNamespace(
        id="tn-1",
        api="openai-completions",
        provider="telnaut",
        base_url="https://telnaut.example/v1",
    )
    assert rm.is_runnable(model) is True


def test_unsupported_message_explains_a_missing_base_url(monkeypatch) -> None:
    monkeypatch.setattr(_REGISTRY, lambda: {"anthropic-messages": object()})
    model = types.SimpleNamespace(
        id="corp-x", api="anthropic-messages", provider="mycorp", base_url=""
    )
    msg = rm.unsupported_message(model)
    assert "corp-x" in msg and "mycorp" in msg
    assert "base URL" in msg


def test_unresolved_api_is_reported_before_the_missing_base_url(monkeypatch) -> None:
    """A bare model carries BOTH api="unknown" AND base_url="" — order matters.

    "We could not resolve it" is the accurate half; telling the user to set a
    baseUrl for a provider we never identified would send them down a dead end.
    """

    monkeypatch.setattr(_REGISTRY, lambda: {"anthropic-messages": object()})
    bare = types.SimpleNamespace(id="tn-1", api="unknown", provider="", base_url="")
    msg = rm.unsupported_message(bare)
    assert "could not be resolved" in msg
    assert "base URL" not in msg


def _vertex_model() -> types.SimpleNamespace:
    # Mirrors the catalog: a templated {location} base_url filled by the SDK.
    return types.SimpleNamespace(
        id="gemini-2.5-flash",
        api="google-vertex",
        base_url="https://{location}-aiplatform.googleapis.com",
    )


def test_vertex_hidden_without_gcp_config(monkeypatch) -> None:
    monkeypatch.setattr(_REGISTRY, lambda: {"google-vertex": object()})
    for name in (
        "GOOGLE_CLOUD_API_KEY",
        "GOOGLE_CLOUD_PROJECT",
        "GCLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
    ):
        monkeypatch.delenv(name, raising=False)
    m = _vertex_model()
    # api IS registered, but no GCP auth is resolvable -> hidden. The {location}
    # placeholder must NOT be the (wrong) reason; the message names GCP env vars.
    assert rm.is_runnable(m) is False
    msg = rm.unsupported_message(m)
    assert "GOOGLE_CLOUD_API_KEY" in msg
    assert "GOOGLE_CLOUD_LOCATION" in msg


def test_vertex_runnable_with_project_and_location(monkeypatch) -> None:
    monkeypatch.setattr(_REGISTRY, lambda: {"google-vertex": object()})
    monkeypatch.delenv("GOOGLE_CLOUD_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    assert rm.is_runnable(_vertex_model()) is True


def test_vertex_runnable_with_api_key(monkeypatch) -> None:
    monkeypatch.setattr(_REGISTRY, lambda: {"google-vertex": object()})
    for name in ("GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_API_KEY", "real-key")
    assert rm.is_runnable(_vertex_model()) is True


def test_introspection_failure_is_safe(monkeypatch) -> None:
    def _boom() -> dict[str, object]:
        raise RuntimeError("registry blew up")

    monkeypatch.setattr(_REGISTRY, _boom)
    assert rm.supported_apis() == set()
    # Empty set → treat as runnable (don't lock the user out on an error).
    assert rm.is_runnable(_model("anything"))
