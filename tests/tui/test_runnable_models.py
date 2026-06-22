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


def test_introspection_failure_is_safe(monkeypatch) -> None:
    def _boom() -> dict[str, object]:
        raise RuntimeError("registry blew up")

    monkeypatch.setattr(_REGISTRY, _boom)
    assert rm.supported_apis() == set()
    # Empty set → treat as runnable (don't lock the user out on an error).
    assert rm.is_runnable(_model("anything"))
