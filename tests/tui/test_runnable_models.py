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
