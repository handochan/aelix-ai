"""ITEM #2 — ``cli/auth_guidance.py`` formatter tests.

Pi source: ``coding-agent/src/core/auth-guidance.ts`` (SHA 734e08e). These
pin the four formatters' shapes AND the honesty adaptation (no dead doc paths;
no non-existent ``/login`` command; the real ``<PROVIDER>_API_KEY`` env route,
with the real ``/model`` command surfaced by the no-model-selected formatter).
"""

from __future__ import annotations

from aelix_coding_agent.cli.auth_guidance import (
    format_no_api_key_found_message,
    format_no_model_selected_message,
    format_no_models_available_message,
    get_provider_login_help,
)


def test_login_help_is_honest() -> None:
    """The shared help block references the REAL surfaces and drops Pi's
    non-existent doc paths (P0 #5 honesty principle)."""

    help_text = get_provider_login_help()
    # Aelix registers NO ``/login`` command, so the help block MUST NOT claim
    # one (same false-claim class as the dropped doc paths).
    assert "/login" not in help_text
    assert "_API_KEY" in help_text  # the env-var route Aelix actually supports
    # Pi's ``<docs>/providers.md`` / ``<docs>/models.md`` MUST NOT appear —
    # those files do not exist in Aelix (printing them would be a false claim).
    assert "providers.md" not in help_text
    assert "models.md" not in help_text
    assert "See:" not in help_text


def test_no_models_available_shape() -> None:
    """Pi: ``No models available. {help}``."""

    msg = format_no_models_available_message()
    assert msg.startswith("No models available. ")
    assert get_provider_login_help() in msg


def test_no_model_selected_shape() -> None:
    """Pi: ``No model selected.\\n\\n{help}\\n\\nThen use /model to select a
    model.`` — preserved around the adapted help block."""

    msg = format_no_model_selected_message()
    assert msg.startswith("No model selected.\n\n")
    assert msg.endswith("Then use /model to select a model.")
    assert get_provider_login_help() in msg


def test_no_api_key_found_named_provider() -> None:
    """A concrete provider display name is interpolated verbatim."""

    msg = format_no_api_key_found_message("Anthropic")
    assert msg.startswith("No API key found for Anthropic.\n\n")
    assert get_provider_login_help() in msg


def test_no_api_key_found_unknown_collapses_to_selected_model() -> None:
    """Pi: an empty / ``"unknown"`` provider collapses to the phrase
    ``"the selected model"``."""

    for provider in ("", "unknown"):
        msg = format_no_api_key_found_message(provider)
        assert "No API key found for the selected model." in msg
